"""
Unit tests for hardware ROI validation, symmetrical constraints, camera timestamps,
and scan manager integration for the pco.edge 5.5 camera.
"""

import pytest
import numpy as np
import h5py
from pathlib import Path

from hhg_control.drivers.base_camera import BaseCamera
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


def test_base_camera_roi_symmetry_and_step_validation():
    """Test that BaseCamera enforces pco.edge 5.5 vertical symmetry and 4-px step rules."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()

    # Full sensor (0, 0, 2560, 2160) is valid (0 + 2160 = 2160, 0 and 2560 % 4 == 0)
    cam.set_roi((0, 0, 2560, 2160))
    assert cam.get_roi() == (0, 0, 2560, 2160)

    # Valid symmetric ROI centered at 1080: Y in [540, 1620] (540 + 1620 = 2160)
    # X in [100, 1100] (both multiples of 4)
    cam.set_roi((100, 540, 1100, 1620))
    assert cam.get_roi() == (100, 540, 1100, 1620)

    # Invalid: Asymmetric vertical bounds (e.g. y0=100, y1=500 -> 100 + 500 = 600 != 2160)
    with pytest.raises(ValueError, match="symmetric about sensor center"):
        cam.set_roi((100, 100, 500, 500))

    # Invalid: Horizontal bound not multiple of 4 (e.g. x0=101)
    with pytest.raises(ValueError, match="multiples of 4"):
        cam.set_roi((101, 540, 1100, 1620))

    # Invalid: Out of bounds (e.g. x1 > 2560)
    with pytest.raises(ValueError, match="outside sensor bounds"):
        cam.set_roi((0, 0, 2564, 2160))

    cam.close()


def test_mock_camera_roi_cropping_and_timestamps():
    """Test that MockPcoCamera returns cropped frames and includes camera metadata timestamps."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()

    # Apply centered ROI: 800 x 540 pixels (Y centered at 1080 -> [810, 1350])
    x0, y0, x1, y1 = 400, 810, 1200, 1350
    cam.set_roi((x0, y0, x1, y1))

    frames, metas = cam.acquire_frames(num_frames=2)
    assert frames.shape == (2, y1 - y0, x1 - x0)
    assert frames.dtype == np.uint16
    assert len(metas) == 2

    # Verify camera timestamp metadata
    for meta in metas:
        assert "camera_timestamp" in meta
        assert "camera_time_str" in meta
        assert meta["roi"] == (x0, y0, x1, y1)

    # Reset to full sensor
    cam.set_roi((0, 0, 2560, 2160))
    full_frames, _ = cam.acquire_frames(num_frames=1)
    assert full_frames.shape == (1, 2160, 2560)

    cam.close()


def test_scan_manager_preview_and_step_saving_with_roi(tmp_path: Path):
    """Test that CameraScanManager properly captures preview and saves HDF5 datasets with ROI bounds."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()

    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)

    # Apply hardware ROI
    roi = (200, 580, 1000, 1580)  # 580 + 1580 = 2160 (symmetric), width=800, height=1000
    scan_mgr.set_roi(roi)
    assert scan_mgr.get_roi() == roi

    # Test preview acquisition
    frame, meta = scan_mgr.acquire_preview()
    assert frame.shape == (1000, 800)
    assert "camera_time_str" in meta
    assert meta["roi"] == roi

    # Test step acquisition and HDF5 saving
    filepath, latest = scan_mgr.acquire_and_save_step(
        experiment_name="ROI_Test",
        step_index=0,
        param_name="Stage_X",
        param_value=1.234,
        num_frames=3
    )

    assert filepath.exists()
    assert latest.shape == (1000, 800)

    # Inspect HDF5 attributes and dataset shape
    with h5py.File(filepath, "r") as h5f:
        images_dset = h5f["images"]
        assert images_dset.shape == (3, 1000, 800)
        assert tuple(h5f.attrs["roi_bounds"]) == roi
        assert h5f.attrs["scan_parameter_name"] == "Stage_X"
        assert h5f.attrs["scan_parameter_value"] == 1.234

    cam.close()

