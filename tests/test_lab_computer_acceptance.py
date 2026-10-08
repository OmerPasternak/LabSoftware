"""Exercise the section runner without hardware or large acquisition files."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import h5py
import pytest

from scripts import lab_computer_acceptance as lab


@pytest.fixture
def config(tmp_path, monkeypatch):
    class SmallMock(lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    monkeypatch.setattr(lab, "MockPcoCamera", SmallMock)
    return lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32),
                        short_frames=(3, 9), speed_frames=8, repeats=1,
                        sustained_seconds=0.01, target_fps=400,
                        run_offline_tests=False)


def test_sections_use_production_writer_and_preserve_copy_hashes(config, capsys):
    identity = lab.section_2(config)
    assert identity["reconnect_readbacks"][0]["sensor"]["model"].endswith("(EMULATOR)")
    short = lab.execute(3, config)
    output = capsys.readouterr().out
    assert "=== IMPORTANT METRICS — SECTION 3 ===" in output
    assert '"total_fps"' in output and '"consecutive_frame_ids": true' in output
    assert short["important_metrics"]["decision"] == "AUTOMATED_CHECKS_PASS"
    assert len(short["important_metrics"]["runs"]) == 2
    first_timing = short["important_metrics"]["runs"][0]["pipeline_stage_timing_s"]
    assert first_timing["hdf5_batch_write_s"] > 0
    assert short["important_metrics"]["runs"][0]["final_os_fsync_s"] >= 0
    saved_report = json.loads(Path(short["important_metrics"]["report_file"]).read_text())
    assert saved_report["important_metrics"] == short["important_metrics"]
    assert [item["frames"] for item in short["result"]["runs"]] == [3, 9]
    assert all(item["consecutive_ids"] for item in short["result"]["runs"])
    report = lab.section_7(config)
    source = report["files"][0]
    copy = config.output_dir / "copy.h5"
    shutil.copyfile(source["file"], copy)
    comparison = lab.section_7(replace(config, data_files=(source["file"], copy)))
    assert comparison["copy_comparison"]["byte_for_byte_match"]
    assert comparison["files"][0]["file_sha256"] == comparison["files"][1]["file_sha256"]
    copied = lab.inspect_file(copy, full_read=True)
    assert source["pixel_sha256"] == copied["pixel_sha256"]
    assert source["metadata_sha256"] == copied["metadata_sha256"]
    with h5py.File(copy, "r+") as h5:
        item = json.loads(h5["frame_metadata/json"][1])
        item["frame_id"] += 1
        h5["frame_metadata/json"][1] = json.dumps(item)
    with pytest.raises(AssertionError, match="Missing, duplicate or reordered"):
        lab.inspect_file(copy)


def test_section_3_review_displays_first_middle_and_last_frames(config, monkeypatch):
    import matplotlib.pyplot as plt

    report = {"result": lab.section_3(config)}
    shown = []
    monkeypatch.setattr(plt, "show", lambda: shown.append(True))
    figure = lab.show_section_3_review_images(report)
    try:
        image_axes = [axis for axis in figure.axes if axis.images]
        assert shown == [True]
        assert len(image_axes) == 6
        assert "frame 1/3" in image_axes[0].get_title()
        assert "frame 2/3" in image_axes[1].get_title()
        assert "frame 3/3" in image_axes[2].get_title()
        assert "frame 1/9" in image_axes[3].get_title()
        assert "frame 5/9" in image_axes[4].get_title()
        assert "frame 9/9" in image_axes[5].get_title()
    finally:
        plt.close(figure)


def test_copy_pixel_change_fails_even_with_valid_frame_ids(config):
    source = lab.section_3(config)["runs"][0]["file"]
    copy = config.output_dir / "copy.h5"
    shutil.copyfile(source, copy)
    with h5py.File(copy, "r+") as h5:
        frame = h5["images"][0]
        frame[0, 0] += 1
        h5["images"][0] = frame
    with pytest.raises(AssertionError, match="Original and copy differ"):
        lab.section_7(replace(config, data_files=(source, copy)))


def test_storage_speed_and_sustained_sections(config):
    readiness = lab.section_1(config)
    assert readiness["paced_writer"]["verified_consecutive_frame_ids"]
    assert readiness["paced_writer"]["verified_image_frame_markers"]
    speed = lab.section_4(config)
    assert speed["capture_median_fps"] > 0
    assert speed["provisional_capture_target_met"] is None  # Mock cannot certify hardware.
    sustained = lab.section_5(config)
    assert sustained["continuous_run"]["frames"] == 4


def test_offline_failure_is_recorded_and_storage_still_runs(config, monkeypatch):
    actual_run = subprocess.run

    def fake_pytest(command, **kwargs):
        if len(command) >= 3 and command[1:3] == ["-m", "pytest"]:
            return subprocess.CompletedProcess(command, 1, stdout="1 failed\n", stderr="")
        return actual_run(command, **kwargs)

    monkeypatch.setattr(lab.subprocess, "run", fake_pytest)
    result = lab.section_1(replace(config, run_offline_tests=True))
    assert result["offline_tests"]["passed"] is False
    assert result["readiness_passed"] is False
    assert "unpaced_writer" in result and "paced_writer" in result
    assert "1 failed" in (config.output_dir / Path(result["offline_tests"]["log"]).name).read_text()


def test_faults_are_mock_only_and_trigger_defaults_to_deferred(config, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("physical driver must not be opened")

    from hhg_control.drivers import pco_edge
    monkeypatch.setattr(pco_edge, "PcoEdgeCamera", forbidden)
    physical = replace(config, source="physical")
    recovery = lab.section_6(physical)
    assert recovery["fault_source"] == "mock only"
    assert len(recovery["retained_partial_files"]) == 2
    assert recovery["recovery"]["complete"]
    assert lab.section_8(physical)["status"] == "DEFERRED"


def test_failure_reports_and_budget_gate(config):
    tiny_budget = replace(config, max_section_raw_gb=1e-9)
    with pytest.raises(AssertionError, match="Section needs"):
        lab.execute(3, tiny_budget)
    reports = list(config.output_dir.glob("section_3_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["status"] == "FAILED"
    assert not list(config.output_dir.glob("*.h5"))


def test_camera_is_closed_on_settings_mismatch(config, monkeypatch):
    closed = []
    original = lab.MockPcoCamera

    class WrongExposure(original):
        def get_exposure_time(self):
            return 0.01

        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(lab, "MockPcoCamera", WrongExposure)
    with pytest.raises(
        AssertionError,
        match=r"Exposure readback 0\.01 s differs from requested 0\.001 s",
    ):
        lab.section_2(config)
    assert closed == [True]


def test_camera_exposure_readback_accepts_one_microsecond_quantization(config, monkeypatch):
    original = lab.MockPcoCamera

    class QuantizedExposure(original):
        def get_exposure_time(self):
            return self._exposure_time_s + 0.4e-6

    monkeypatch.setattr(lab, "MockPcoCamera", QuantizedExposure)
    result = lab.section_2(config)
    assert result["reconnect_readbacks"][0]["exposure_s"] == pytest.approx(0.0010004)


def test_notebook_setup_and_cells_are_executable():
    notebook = json.loads((lab.ROOT / "application tests/lab_computer_acceptance.ipynb").read_text(encoding="utf-8"))
    code_cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 9
    for source in code_cells:
        compile(source, "lab_computer_acceptance.ipynb", "exec")
    namespace = {}
    exec(code_cells[0], namespace)
    assert namespace["CONFIG"].source == "mock"
    assert namespace["CONFIG"].trigger_bench_ready is False
    assert namespace["CONFIG"].roi == (0, 1016, 2560, 1144)
    assert namespace["USE_SHORT_STORAGE_DIAGNOSTIC"] is False
    assert namespace["CONFIG"].batch_size == 4
    assert namespace["CONFIG"].speed_frames == 10000
    assert namespace["CONFIG"].repeats == 3
    assert namespace["CONFIG"].max_section_raw_gb == 120.0
    short_namespace = {}
    exec(
        code_cells[0].replace(
            "USE_SHORT_STORAGE_DIAGNOSTIC = False",
            "USE_SHORT_STORAGE_DIAGNOSTIC = True",
        ),
        short_namespace,
    )
    assert short_namespace["CONFIG"].batch_size == 16
    assert short_namespace["CONFIG"].speed_frames == 2000
    assert short_namespace["CONFIG"].repeats == 1
    assert code_cells[-1] == "REPORT_8 = lab.execute(8, CONFIG)\n"


def test_terminal_runs_only_requested_section_and_deferred_trigger(config):
    script = str(lab.ROOT / "scripts/lab_computer_acceptance.py")
    run = subprocess.run([sys.executable, script, "--section", "8", "--source", "physical",
                          "--output-dir", str(config.output_dir)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    reports = list(config.output_dir.glob("section_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["result"]["status"] == "DEFERRED"
    help_run = subprocess.run([sys.executable, script], capture_output=True, text=True)
    assert help_run.returncode == 0 and "--section" in help_run.stdout


def test_important_metrics_cover_all_sections(config):
    results = {
        1: lab.section_1(config),
        2: lab.section_2(config),
        3: lab.section_3(config),
        4: lab.section_4(config),
        5: lab.section_5(config),
        6: lab.section_6(config),
        7: lab.section_7(config),
        8: lab.section_8(config),
    }
    expected_decisions = {
        1: "NOT_PASSED",  # Offline tests are deliberately skipped in this fixture.
        2: "AUTOMATED_CHECKS_PASS",
        3: "AUTOMATED_CHECKS_PASS",
        4: "PENDING_BASELINE",
        5: "AUTOMATED_CHECKS_PASS",
        6: "AUTOMATED_CHECKS_PASS",
        7: "AUTOMATED_CHECKS_PASS",
        8: "DEFERRED",
    }
    for number, result in results.items():
        report_path = config.output_dir / f"summary_{number}.json"
        report = {"status": "EXECUTED", "result": result}
        summary = lab.important_metrics(number, report, config, report_path)
        assert summary["section"] == number
        assert summary["decision"] == expected_decisions[number]
        assert summary["report_file"] == str(report_path.resolve())


def test_failed_section_summary_preserves_actionable_error(config):
    report_path = config.output_dir / "failed.json"
    report = {"status": "FAILED", "error": "AssertionError: example failure"}
    summary = lab.important_metrics(3, report, config, report_path)
    assert summary["decision"] == "FAILED"
    assert summary["error"] == "AssertionError: example failure"
