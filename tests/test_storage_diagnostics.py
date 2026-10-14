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
    assert all(Path(run["saved"]["file"]).is_file() for run in runs)
    assert cfg.batch_size == 4 and cfg.speed_frames == 8
    persisted = json.loads(Path(report["report_file"]).read_text(encoding="utf-8"))
    assert persisted["drives"]["selected"]["within_output_cap"]


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
