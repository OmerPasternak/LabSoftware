import pytest
import h5py
import numpy as np
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


def test_scan_step_hdf5_output(tmp_path):
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    cam.set_exposure_time(0.005)

    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)
    h5_path = scan_mgr.acquire_and_save_step(
        experiment_name="hhg_test",
        step_index=0,
        param_name="delay_ps",
        param_value=1.525,
        num_frames=4
    )

    assert h5_path.exists()

    with h5py.File(h5_path, "r") as h5f:
        assert "images" in h5f
        dset = h5f["images"]
        assert dset.shape == (4, 2160, 2560)
        assert dset.dtype == np.uint16
        assert h5f.attrs["experiment_name"] == "hhg_test"
        assert h5f.attrs["step_index"] == 0
        assert h5f.attrs["scan_parameter_name"] == "delay_ps"
        assert pytest.approx(h5f.attrs["scan_parameter_value"], 1e-4) == 1.525
        assert pytest.approx(h5f.attrs["exposure_time_s"], 1e-4) == 0.005

    cam.close()

