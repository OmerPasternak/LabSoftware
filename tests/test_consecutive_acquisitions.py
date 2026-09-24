import pytest
import h5py
import numpy as np
from pathlib import Path
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


def test_consecutive_scan_steps(tmp_path):
    """Test running multiple consecutive acquisitions in a single session without state corruption."""
    cam = MockPcoCamera()
    cam.connect()
    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)

    # Simulate 5 consecutive scan steps in a single session
    for step in range(5):
        h5_path, latest_frame = scan_mgr.acquire_and_save_step(
            experiment_name="consecutive_test",
            step_index=step,
            param_name="delay_stage_mm",
            param_value=float(step * 0.1),
            num_frames=3
        )

        assert h5_path.exists()
        assert latest_frame.shape == (2160, 2560)
        assert latest_frame.dtype == np.uint16

        with h5py.File(h5_path, "r") as h5f:
            assert h5f.attrs["scan_step_index"] == step
            assert pytest.approx(h5f.attrs["scan_parameter_setpoint_value"], 1e-5) == step * 0.1
            assert h5f.attrs["frame_accumulation_count"] == 3
            assert h5f["images"].shape == (3, 2160, 2560)

    cam.close()

