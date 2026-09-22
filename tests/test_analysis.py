"""
Tests for Data Analysis Package (HDF5 IO loaders and beam profiling).
"""

import pytest
import numpy as np
from pathlib import Path
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.analysis import (
    load_scan_step,
    load_full_scan,
    get_scan_metadata,
    compute_beam_centroid,
    fit_gaussian_beam_profile,
)


def test_analysis_hdf5_loaders_and_beam_profiling(tmp_path):
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)

    # Acquire 3 steps
    saved_files = []
    for step in range(3):
        filepath, _ = scan_mgr.acquire_and_save_step(
            experiment_name="analysis_test",
            step_index=step,
            param_name="delay_stage_mm",
            param_value=float(step * 0.2),
            num_frames=2
        )
        saved_files.append(filepath)

    first_step_file = saved_files[0]
    assert first_step_file.exists()

    # 1. Test get_scan_metadata without loading arrays
    meta = get_scan_metadata(first_step_file)
    assert meta["scan_step_index"] == 0
    assert meta["dataset_shape"] == (2, 2160, 2560)

    # 2. Test load_scan_step
    images, step_meta = load_scan_step(first_step_file)
    assert images.shape == (2, 2160, 2560)
    assert images.dtype == np.uint16
    assert step_meta["scan_step_index"] == 0

    # 3. Test load_full_scan
    full_scan = load_full_scan(tmp_path, experiment_prefix="analysis_test")
    assert full_scan["step_indices"] == [0, 1, 2]
    assert np.allclose(full_scan["setpoints"], [0.0, 0.2, 0.4])
    assert full_scan["mean_frames"].shape == (3, 2160, 2560)

    # 4. Test compute_beam_centroid
    cx, cy = compute_beam_centroid(images[0])
    # Expected centered at (x0=1280, y0=1080) within tolerance
    assert 1200 <= cx <= 1360
    assert 1000 <= cy <= 1160

    # 5. Test fit_gaussian_beam_profile
    fit_results = fit_gaussian_beam_profile(images[0])
    assert 1200 <= fit_results["centroid_x"] <= 1360
    assert 1000 <= fit_results["centroid_y"] <= 1160
    # Sigma is simulated at 120 pixels
    assert 90.0 <= fit_results["sigma_x"] <= 150.0
    assert 90.0 <= fit_results["sigma_y"] <= 150.0
    assert fit_results["fwhm_x"] > fit_results["sigma_x"]

    cam.close()
