import pytest
import h5py
import numpy as np
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.drivers.base_camera import TriggerMode
from hhg_control.sequencer.scan_manager import CameraScanManager


def test_scan_step_hdf5_output(tmp_path):
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    cam.set_exposure_time(0.005)
    cam.set_trigger_mode(TriggerMode.EXTERNAL_EXPOSURE_START)

    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)
    h5_path, latest_frame = scan_mgr.acquire_and_save_step(
        experiment_name="hhg_test",
        step_index=0,
        param_name="delay_ps",
        param_value=1.525,
        num_frames=4
    )

    assert h5_path.exists()
    assert isinstance(latest_frame, np.ndarray)
    assert latest_frame.shape == (2160, 2560)
    assert latest_frame.dtype == np.uint16

    with h5py.File(h5_path, "r") as h5f:
        assert "images" in h5f
        dset = h5f["images"]
        assert dset.shape == (4, 2160, 2560)
        assert dset.dtype == np.uint16
        assert dset.attrs["physical_units"] == "16-bit digital counts (ADU)"
        assert dset.attrs["data_dimension_ordering"] == "[frame_index, sensor_height_y, sensor_width_x]"

        # Verify backward compatibility
        assert h5f.attrs["experiment_name"] == "hhg_test"
        assert h5f.attrs["step_index"] == 0
        assert h5f.attrs["scan_parameter_name"] == "delay_ps"
        assert pytest.approx(h5f.attrs["scan_parameter_value"], 1e-4) == 1.525
        assert pytest.approx(h5f.attrs["exposure_time_s"], 1e-4) == 0.005
        assert h5f.attrs["trigger_mode"] == TriggerMode.EXTERNAL_EXPOSURE_START.value

        # Verify expanded descriptive metadata
        assert h5f.attrs["experiment_identifier"] == "hhg_test"
        assert h5f.attrs["scan_step_index"] == 0
        assert h5f.attrs["scan_parameter_display_name"] == "delay_ps"
        assert pytest.approx(h5f.attrs["scan_parameter_setpoint_value"], 1e-4) == 1.525
        assert pytest.approx(h5f.attrs["exposure_duration_seconds"], 1e-4) == 0.005
        assert h5f.attrs["frame_accumulation_count"] == 4
        assert "pco.edge" in h5f.attrs["camera_manufacturer_and_model"]

    cam.close()


def test_execute_scan_full_sequence(tmp_path):
    """Test full multi-step automated scan sequence execution."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)

    results = list(
        scan_mgr.execute_scan(
            experiment_name="full_scan_test",
            param_name="stage_pos_mm",
            start_value=10.0,
            step_size=0.5,
            num_steps=4,
            num_frames=2
        )
    )

    assert len(results) == 4
    for idx, (step, val, filepath, frame) in enumerate(results):
        assert step == idx
        assert pytest.approx(val, 1e-4) == 10.0 + idx * 0.5
        assert filepath.exists()
        assert frame.shape == (2160, 2560)

        with h5py.File(filepath, "r") as h5f:
            assert h5f.attrs["scan_step_index"] == idx
            assert pytest.approx(h5f.attrs["scan_parameter_setpoint_value"], 1e-4) == 10.0 + idx * 0.5
            assert h5f["images"].shape == (2, 2160, 2560)

    cam.close()


def test_execute_scan_resume_and_callbacks(tmp_path):
    """Test resuming an experiment scan from a given step index and callback invocations."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    scan_mgr = CameraScanManager(camera=cam, storage_dir=tmp_path)

    started_steps = []
    def on_start(step_idx: int, val: float) -> None:
        started_steps.append((step_idx, val))

    # Resume from step 2 (retake step 2, then step 3)
    results = list(
        scan_mgr.execute_scan(
            experiment_name="resume_test",
            param_name="delay_mm",
            start_value=1.0,
            step_size=0.1,
            num_steps=4,
            num_frames=2,
            start_step=2,
            on_step_start=on_start,
        )
    )

    assert len(results) == 2
    assert started_steps == [(2, 1.2), (3, 1.3)]
    assert [r[0] for r in results] == [2, 3]
    for step_idx, val, filepath, frame in results:
        assert filepath.exists()

    cam.close()
