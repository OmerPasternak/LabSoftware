import pytest
import numpy as np
import time
from hhg_control.drivers.mock_camera import MockPcoCamera


def test_mock_prepares_large_arrays_only_on_connect():
    """Opening an unconnected GUI should not allocate synthetic full-sensor data."""
    camera = MockPcoCamera()
    assert camera._gaussian_profile is None
    assert camera._dark_bank is None

    camera.connect()
    assert camera._gaussian_profile is not None
    assert camera._dark_bank is not None
    camera.close()
from hhg_control.drivers.base_camera import CameraSafetyError


def test_mock_camera_acquisition():
    cam = MockPcoCamera()
    assert not cam.is_connected
    cam.connect()
    assert cam.is_connected

    cam.set_exposure_time(0.02)
    assert cam.get_exposure_time() == 0.02

    frames, metas = cam.acquire_frames(3)
    assert frames.shape == (3, 2160, 2560)
    assert frames.dtype == np.uint16
    assert len(metas) == 3

    cam.close()
    assert not cam.is_connected


def test_mock_waits_for_each_requested_exposure_even_with_synthetic_frames():
    """Three simulated frames include three full exposure waits in wall time."""
    cam = MockPcoCamera()
    cam.connect()
    cam.set_roi((1200, 1064, 1360, 1096))
    cam.set_exposure_time(0.020)

    started = time.perf_counter()
    frames, _ = cam.acquire_frames(3)
    elapsed_s = time.perf_counter() - started

    assert len(frames) == 3
    assert elapsed_s >= 0.058  # 3 x 20 ms, allowing 2 ms timer uncertainty.
    cam.close()


def test_mock_external_trigger_selection_is_recorded_without_hardware():
    """The mock carries trigger settings through the same camera contract."""
    from hhg_control.drivers.base_camera import TriggerMode

    cam = MockPcoCamera()
    cam.connect()
    cam.set_trigger_mode(TriggerMode.EXTERNAL_EXPOSURE_START)
    assert cam.get_trigger_mode() == TriggerMode.EXTERNAL_EXPOSURE_START
    _, metadata = cam.acquire_frames(1)
    assert metadata[0]["trigger_mode"] == TriggerMode.EXTERNAL_EXPOSURE_START.value
    with pytest.raises(ValueError, match="TriggerMode"):
        cam.set_trigger_mode("external exposure control")
    cam.close()


def test_mock_camera_safety_limits():
    cam = MockPcoCamera()
    with pytest.raises(CameraSafetyError):
        cam.set_exposure_time(0.0001)
    with pytest.raises(CameraSafetyError):
        cam.set_exposure_time(15.0)


def test_mock_camera_readout_modes():
    from hhg_control.drivers.base_camera import ReadoutMode
    cam = MockPcoCamera()
    cam.connect()

    # Default mode is rolling shutter
    assert cam.get_readout_mode() == ReadoutMode.ROLLING_SHUTTER

    # Global reset remains a distinct mode for compatibility with old scans.
    cam.set_readout_mode(ReadoutMode.GLOBAL_RESET)
    assert cam.get_readout_mode() == ReadoutMode.GLOBAL_RESET

    cam.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)
    assert cam.get_readout_mode() == ReadoutMode.GLOBAL_SHUTTER

    # Metadata should record readout mode
    frames, metas = cam.acquire_frames(2)
    assert metas[0]["readout_mode"] == "GLOBAL_SHUTTER"

    cam.close()


def test_mock_global_shutter_enforces_mode_specific_exposure_limit():
    from hhg_control.drivers.base_camera import ReadoutMode

    cam = MockPcoCamera()
    cam.set_exposure_time(0.2)
    with pytest.raises(CameraSafetyError, match="Reduce exposure"):
        cam.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)
    assert cam.get_readout_mode() == ReadoutMode.ROLLING_SHUTTER

    cam.set_exposure_time(0.1)
    cam.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)
    with pytest.raises(CameraSafetyError, match="at most 0.1 s"):
        cam.set_exposure_time(0.101)
    assert cam.get_exposure_time() == 0.1
