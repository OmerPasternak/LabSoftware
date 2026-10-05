"""Exercise the section runner without hardware or large acquisition files."""

from dataclasses import replace
import json
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


def test_sections_use_production_writer_and_preserve_copy_hashes(config):
    identity = lab.section_2(config)
    assert identity["reconnect_readbacks"][0]["sensor"]["model"].endswith("(EMULATOR)")
    short = lab.execute(3, config)
    assert [item["frames"] for item in short["result"]["runs"]] == [3, 9]
    assert all(item["consecutive_ids"] for item in short["result"]["runs"])
    report = lab.section_7(config)
    source = report["files"][0]
    copy = config.output_dir / "copy.h5"
    shutil.copyfile(source["file"], copy)
    copied = lab.inspect_file(copy, full_read=True)
    assert source["pixel_sha256"] == copied["pixel_sha256"]
    assert source["metadata_sha256"] == copied["metadata_sha256"]
    with h5py.File(copy, "r+") as h5:
        item = json.loads(h5["frame_metadata/json"][1])
        item["frame_id"] += 1
        h5["frame_metadata/json"][1] = json.dumps(item)
    with pytest.raises(AssertionError, match="Missing, duplicate or reordered"):
        lab.inspect_file(copy)


def test_storage_speed_and_sustained_sections(config):
    readiness = lab.section_1(config)
    assert readiness["paced_writer"]["verified_consecutive_frame_ids"]
    assert readiness["paced_writer"]["verified_image_frame_markers"]
    speed = lab.section_4(config)
    assert speed["capture_median_fps"] > 0
    assert speed["provisional_speed_target_met"] is None  # Mock cannot certify hardware.
    sustained = lab.section_5(config)
    assert sustained["continuous_run"]["frames"] == 4


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
    with pytest.raises(AssertionError, match="Exposure readback differs"):
        lab.section_2(config)
    assert closed == [True]


def test_notebook_setup_and_cells_are_executable():
    notebook = json.loads((lab.ROOT / "scripts/lab_computer_acceptance.ipynb").read_text(encoding="utf-8"))
    code_cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 9
    for source in code_cells:
        compile(source, "lab_computer_acceptance.ipynb", "exec")
    namespace = {}
    exec(code_cells[0], namespace)
    assert namespace["CONFIG"].source == "mock"
    assert namespace["CONFIG"].trigger_bench_ready is False
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
