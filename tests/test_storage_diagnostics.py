"""Small offline controls for 4C/4D; no camera SDK or lab drive is accessed."""

from dataclasses import replace
import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from scripts import lab_computer_acceptance as lab
from scripts import storage_diagnostics as diagnostics


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    class SmallMock(lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    monkeypatch.setattr(lab, "MockPcoCamera", SmallMock)
    return lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32),
                        speed_frames=8, repeats=1, target_fps=400)


def test_batch_control_uses_production_writer_and_keeps_config(cfg, capsys):
    report = diagnostics.run_batch_size_trial(
        cfg, batch_sizes=(1, 4), frames=7, repeats=2,
    )
    assert capsys.readouterr().out == ""
    assert report["status"] == "EXECUTED"
    runs = report["drives"]["selected"]["runs"]
    assert [run["batch_size"] for run in runs] == [1, 4, 4, 1]
    assert all(run["saved"]["frames"] == 7 and run["saved"]["consecutive_ids"] for run in runs)
    assert all(run["queue_discard"]["frames"] == 7 for run in runs)
    assert all(Path(run["saved"]["file"]).is_file() for run in runs)
    assert cfg.batch_size == 4 and cfg.speed_frames == 8
    persisted = json.loads(Path(report["report_file"]).read_text(encoding="utf-8"))
    assert persisted["drives"]["selected"]["within_output_cap"]
    details = persisted["drives"]["selected"]["diagnostics"]
    assert all(item["pixel_integrity"]["status"] == "VERIFIED" for item in details)
    assert all(item["saved"]["camera_timestamp_status"] == "UNAVAILABLE" for item in details)
    assert all(item["interpretation"]["run_complete"] for item in details)


def test_batch_budget_fails_before_camera_access(cfg, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Camera configuration must not be reached")

    monkeypatch.setattr(lab, "configured_camera", forbidden)
    report = diagnostics.run_batch_size_trial(
        replace(cfg, source="physical", expected_serial="61008541"),
        frames=1_000_000, max_output_gb_per_drive=0.1,
    )
    assert report["status"] == "FAILED"
    assert "exceeds the per-drive cap" in report["error"]


@pytest.mark.parametrize("periodic", [False, True])
def test_bin_hdf5_control_matches_every_pixel_and_metadata(cfg, monkeypatch, capsys, periodic):
    def forbidden(*args, **kwargs):
        raise AssertionError("Synthetic 4D must never open the physical camera")

    monkeypatch.setattr(lab, "configured_camera", forbidden)
    # The simulated C:/D: folders remain on tmp_path. Their volume identities
    # are mocked explicitly, so neither lab drive is touched on Windows CI.
    monkeypatch.setattr(diagnostics, "_volume_key", lambda path: path.name)
    report = diagnostics.run_bin_hdf5_trial(
        replace(cfg, source="physical", expected_serial="61008541"),
        output_dirs={"C": cfg.output_dir / "C", "D": cfg.output_dir / "D"},
        target_raw_gb_per_format=7 * 64 * 32 * 2 / 1e9,
        batch_size=3, compare_periodic_sync=periodic, sync_every_gb=0.000001,
    )
    assert capsys.readouterr().out == ""
    assert report["status"] == "EXECUTED", report.get("error")
    assert report["source"] == "synthetic"
    for drive in report["drives"].values():
        assert len(drive["runs"]) == (4 if periodic else 2)
        assert drive["within_output_cap"]
        for run in drive["runs"]:
            assert run["frames"] == 7
            assert run["pixels_equal_source"] and run["consecutive_frame_ids"]
            assert run["pixel_sha256"] == report["expected_pixel_sha256"]
            assert run["durable_s"] >= run["write_close_s"]
            assert run["final_os_fsync_s"] >= 0 and run["immediate_second_fsync_s"] >= 0
            if run["sync_policy"] == "periodic_and_final":
                assert run["periodic_fsync_count"] > 0
    assert report["drives"]["C"]["runs"][0]["format"] == "hdf5"
    assert report["drives"]["D"]["runs"][0]["format"] == "bin"


def test_bin_control_rejects_pixel_corruption(cfg):
    bank = np.ones((2, 2, 4), dtype="<u2")
    digest = diagnostics.hashlib.sha256()
    for _, block in diagnostics._blocks(bank, 3):
        digest.update(block.tobytes(order="C"))
    result = diagnostics._write_control(cfg.output_dir / "control", "bin", bank, 3, digest.hexdigest(), 0)
    path = Path(result["file"])
    with path.open("r+b") as handle:
        handle.seek(6)
        handle.write(b"\xff\xff")
    with pytest.raises(AssertionError, match="pixels differ"):
        diagnostics._verify_control(path, Path(result["metadata_file"]), "bin", bank, 3, digest.hexdigest())


def test_hdf5_control_rejects_frame_metadata_corruption(cfg):
    bank = np.ones((2, 2, 4), dtype="<u2")
    digest = diagnostics.hashlib.sha256()
    for _, block in diagnostics._blocks(bank, 3):
        digest.update(block.tobytes(order="C"))
    result = diagnostics._write_control(cfg.output_dir / "control", "hdf5", bank, 3, digest.hexdigest(), 0)
    path = Path(result["file"])
    with h5py.File(path, "r+") as h5:
        h5["frame_metadata/json"][1] = json.dumps({"frame_id": 99})
    with pytest.raises(AssertionError, match="metadata differs"):
        diagnostics._verify_control(path, Path("unused"), "hdf5", bank, 3, digest.hexdigest())


def test_diagnostic_refuses_two_folders_on_the_same_drive(cfg):
    with pytest.raises(ValueError, match="one destination per drive"):
        diagnostics.run_bin_hdf5_trial(
            cfg, output_dirs={"one": cfg.output_dir / "one", "two": cfg.output_dir / "two"},
        )


def test_small_roi_metadata_budget_is_checked_before_writing(cfg):
    report = diagnostics.run_bin_hdf5_trial(
        replace(cfg, roi=(0, 0, 2, 1)), target_raw_gb_per_format=2.0,
    )
    assert report["status"] == "FAILED"
    assert "exceeds the per-drive cap" in report["error"]
    assert report["drives"] == {}


def test_compact_report_is_chart_ready_in_main_folder(cfg):
    report = diagnostics.run_bin_hdf5_trial(
        cfg, target_raw_gb_per_format=7 * 64 * 32 * 2 / 1e9,
        batch_size=3, sync_every_gb=0.000001,
    )
    path = Path(report["report_file"])
    assert path.parent == cfg.output_dir
    packet = json.loads(path.read_text(encoding="utf-8"))
    assert packet["compare_periodic_sync"] is True
    assert len(packet["rows"]) == 4
    assert all(len(row) == len(packet["columns"]) for row in packet["rows"])
    rows = [dict(zip(packet["columns"], row)) for row in packet["rows"]]
    assert all(row["pixels_equal_source"] and row["consecutive_frame_ids"] for row in rows)
    assert {row["sync_policy"] for row in rows} == {"final_only", "periodic_and_final"}
    assert len(json.dumps(packet)) < 0.7 * len(json.dumps(report, default=lab.json_default))


def test_notebook_contains_minimal_status_and_periodic_sync_default():
    notebook = json.loads((lab.ROOT / "application tests/lab_computer_acceptance.ipynb").read_text(encoding="utf-8"))
    cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert any("run_batch_size_trial(" in cell and "RUN_BATCH_SIZE_TEST = True" in cell for cell in cells)
    assert any("run_bin_hdf5_trial(" in cell and "COMPARE_PERIODIC_SYNC = True" in cell for cell in cells)
    assert all("print(" in cell or "run_notebook_cell(" in cell for cell in cells)
    assert all("raise_on_error=False" not in cell for cell in cells)


def test_pixel_trial_checks_source_and_reports_added_time(cfg, capsys):
    report = diagnostics.run_hdf5_pixel_verification_trial(cfg, frames=7)
    assert capsys.readouterr().out == ""
    assert report["status"] == "EXECUTED", report.get("error")
    drive = report["drives"]["selected"]
    assert drive["verification"]["pixels_equal_source"]
    assert drive["verification"]["frames_verified"] == 7
    assert drive["source_hash_s"] >= 0
    assert drive["post_acquisition_added_s"] >= drive["verification"]["verification_s"]
    assert drive["durable_plus_verification_s"] == pytest.approx(
        drive["verified_run"]["synced_s"] + drive["post_acquisition_added_s"])
    assert drive["acquisition_fps_change_percent"] == pytest.approx(
        100 * (drive["verified_run"]["total_fps"] / drive["baseline"]["total_fps"] - 1))
    path = Path(report["report_file"])
    assert path.parent == cfg.output_dir
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["source"] == "mock"
    # A readable HDF5 file with the same shape/count must still fail if a pixel changes.
    with h5py.File(drive["verified_run"]["file"], "r+") as h5:
        h5["images"][3, 2, 2] = int(h5["images"][3, 2, 2]) ^ 1
    with pytest.raises(AssertionError, match="pixels differ"):
        diagnostics.verify_hdf5_pixels(Path(drive["verified_run"]["file"]), Path(drive["reference_manifest"]))


def test_pixel_trial_failure_is_saved_and_raised_by_notebook(cfg, monkeypatch):
    def corrupt_check(*args):
        raise AssertionError("Injected pixel mismatch")

    monkeypatch.setattr(diagnostics, "verify_hdf5_pixels", corrupt_check)
    with pytest.raises(RuntimeError, match="Injected pixel mismatch"):
        lab.run_notebook_cell("Section 4E", lambda: diagnostics.run_hdf5_pixel_verification_trial(cfg, frames=3))
    report = json.loads(next(cfg.output_dir.glob("section_4e_*.json")).read_text(encoding="utf-8"))
    assert report["status"] == "FAILED"
    assert report["drives"]["selected"]["verification"]["pixels_equal_source"] is False


def test_pixel_trial_budget_stops_before_camera(cfg, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Must not connect")

    monkeypatch.setattr(lab, "configured_camera", forbidden)
    report = diagnostics.run_hdf5_pixel_verification_trial(cfg, frames=1_000_000, max_output_gb_per_drive=0.1)
    assert report["status"] == "FAILED"
    assert "exceeds the per-drive cap" in report["error"]


@pytest.mark.parametrize("budget,files", [(40, 3), (20, 3), (100, 1), (200, 1), (40, 4), (200, 4)])
def test_stage_budget_includes_all_files_and_metadata(cfg, budget, files):
    frames = diagnostics.stage_frames(cfg, budget, files)
    frame_bytes = 64 * 32 * 2
    total = frames * files * (frame_bytes + 4096) + files * 100_000_000
    assert total <= budget * 1e9
    assert budget * 1e9 - total < files * (frame_bytes + 4096)


def test_stage_profile_and_continuous_selection_guard(cfg, monkeypatch):
    assert diagnostics.stage_profile("C", 200) == 40
    assert diagnostics.stage_profile("D", 100) == 100
    with pytest.raises(ValueError):
        diagnostics.stage_profile("D", 40)
    with pytest.raises(ValueError):
        diagnostics.stage_profile("invalid")
    def forbidden(*args):
        raise AssertionError("Camera must not be reached")
    monkeypatch.setattr(lab, "configured_camera", forbidden)
    report = diagnostics.run_batch_size_trial(cfg, stage_output_gb=200)
    assert report["status"] == "FAILED"
    assert "selected_batch_size" in report["error"]


def test_hdf5_only_control_remains_synthetic(cfg, monkeypatch):
    def forbidden(*args):
        raise AssertionError("No camera")
    monkeypatch.setattr(lab, "configured_camera", forbidden)
    report = diagnostics.run_bin_hdf5_trial(cfg, target_raw_gb_per_format=7 * 4096 / 1e9, include_bin=False)
    assert report["status"] == "EXECUTED", report.get("error")
    assert {run["format"] for run in report["drives"]["selected"]["runs"]} == {"hdf5"}
    assert len(report["drives"]["selected"]["runs"]) == 2


def test_interpretation_distinguishes_retrieval_and_postcapture_delay():
    from scripts.capture_diagnostics import interpret
    capture = {"frames": 2000, "total_fps": 449, "profile": {"host_first_to_last_fps": 450}}
    queued = {"frames": 2000, "total_fps": 448, "profile": {"host_first_to_last_fps": 450}}
    saved = {"frames": 2000, "total_fps": 440, "synced_fps": 300,
             "total_close_s": 2000 / 440, "synced_s": 2000 / 300,
             "final_os_fsync_s": 2000 / 300 - 2000 / 440,
             "profile": {"camera_cadence_fps": 455, "host_first_to_last_fps": 450, "frames_returned": 2000,
                         "fifo_capacity_frames": 409, "frame_id_gap_events": 0, "fifo_overflow": False}}
    result = diagnostics.capture_diag.interpret(capture, queued, saved, 455, "physical")
    assert result["camera_minus_host_fps"] == 5
    assert result["saved_minus_durable_fps"] == 140
    assert result["camera_side_missing_frame_count"] is None
    assert not result["observed_transfer_error"]
    assert result["fifo_fill_estimate_s_if_rates_persist"] == pytest.approx(409 / 5)
    saved["profile"]["camera_cadence_fps"] = None
    result = interpret(capture, queued, saved, 455, "physical")
    assert result["reference_minus_camera_fps"] is None
    assert any("unavailable" in text.lower() for text in result["findings"])
    saved["profile"]["fifo_overflow"] = True
    assert interpret(capture, queued, saved, 455, "physical")["observed_transfer_error"]


def test_camera_time_never_uses_host_fallback(cfg):
    from scripts.capture_diagnostics import Probe
    probe = Probe(object())
    probe.observe(np.zeros((2, 1, 2), dtype=np.uint16), [
        {"frame_id": i, "camera_timestamp": 100 + i / 455, "timestamp_source": "host_fallback"}
        for i in range(2)
    ])
    assert probe.summary()["camera_cadence_fps"] is None


def test_batch_failure_retains_partial_profile_and_original_error(cfg, monkeypatch):
    def fail(camera, config, *args, **kwargs):
        # Exercise acquisition/queue before simulating a storage failure.
        list(camera.iter_frames(3, batch_size=2))
        raise OSError("Injected disk failure")
    monkeypatch.setattr(lab, "saved_run", fail)
    report = diagnostics.run_batch_size_trial(cfg, batch_sizes=(2,), frames=3)
    assert report["status"] == "FAILED"
    assert "Injected disk failure" in report["error"]
    packet = json.loads(Path(report["report_file"]).read_text(encoding="utf-8"))
    details = packet["drives"]["selected"]["diagnostics"][0]
    assert details["saved"]["frames_returned"] == 3
    assert not details["interpretation"]["run_complete"]


def test_stages_do_not_allocate_gigabytes_or_mutate_config(cfg, monkeypatch):
    # Test orchestration with a tiny substitute for the stage planner. Actual
    # large-volume arithmetic is checked above; CI never allocates GB payloads.
    monkeypatch.setattr(diagnostics, "stage_frames", lambda cfg, budget, files: 7)
    report = diagnostics.run_batch_size_trial(cfg, stage_output_gb=200, selected_batch_size=4)
    assert report["status"] == "EXECUTED", report.get("error")
    assert len(report["drives"]["selected"]["runs"]) == 1
    assert cfg.max_section_raw_gb == 120
    report = diagnostics.run_bin_hdf5_trial(cfg, stage_output_gb=200)
    assert report["status"] == "EXECUTED", report.get("error")
