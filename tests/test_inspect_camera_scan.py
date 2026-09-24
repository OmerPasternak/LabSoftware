"""Offline checks for the camera scan reader using the real mock writer."""

import csv

import h5py
import numpy as np
import pytest

from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from scripts.inspect_camera_scan import main, read_frame_metadata, scan_files, summarize_step


def test_inspect_mock_scan_and_exports(tmp_path):
    camera = MockPcoCamera()
    camera.connect()
    try:
        camera.set_roi((800, 980, 1056, 1180))
        path, _ = CameraScanManager(camera, tmp_path).acquire_and_save_step(
            "reader_test", 2, "delay_mm", 1.5, 3, run_id="test-run", batch_size=2
        )
    finally:
        camera.close()

    summary, mean_frame = summarize_step(path, batch_size=2)
    with h5py.File(path, "r") as h5f:
        expected = h5f["images"][:].mean(axis=0)
    assert np.array_equal(mean_frame, expected)
    assert summary["step_index"] == 2
    assert summary["scan_parameter_value"] == 1.5
    assert summary["frames"] == 3
    assert summary["mean_adu"] == pytest.approx(float(expected.mean()))
    assert read_frame_metadata(path, 0)["timestamp_source"] == "simulated_host_clock"
    with pytest.raises(IndexError):
        read_frame_metadata(path, 3)

    (tmp_path / "interrupted.h5.partial").touch()
    assert scan_files(tmp_path) == [path]
    csv_path = tmp_path / "summary.csv"
    mean_dir = tmp_path / "means"
    assert main([str(tmp_path), "--csv", str(csv_path), "--mean-dir", str(mean_dir)]) == 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["run_id"] == "test-run"
    assert np.array_equal(np.load(mean_dir / f"{path.stem}_mean.npy"), expected)


def test_reject_incomplete_scan(tmp_path):
    path = tmp_path / "bad.h5"
    with h5py.File(path, "w") as h5f:
        h5f.attrs["schema_version"] = "2.0"
        h5f.attrs["complete"] = False
    with pytest.raises(ValueError, match="Incomplete"):
        summarize_step(path)
