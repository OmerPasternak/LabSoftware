import pytest
import numpy as np
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.drivers.base_camera import CameraSafetyError


def test_mock_camera_acquisition():
    cam = MockPcoCamera(fast_simulation=True)
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


def test_mock_camera_safety_limits():
    cam = MockPcoCamera(fast_simulation=True)
    with pytest.raises(CameraSafetyError):
        cam.set_exposure_time(0.0001)
    with pytest.raises(CameraSafetyError):
        cam.set_exposure_time(15.0)


def test_mock_camera_readout_modes():
    from hhg_control.drivers.base_camera import ReadoutMode
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()

    # Default mode is rolling shutter
    assert cam.get_readout_mode() == ReadoutMode.ROLLING_SHUTTER

    # Switch to global reset (recommended for HHG)
    cam.set_readout_mode(ReadoutMode.GLOBAL_RESET)
    assert cam.get_readout_mode() == ReadoutMode.GLOBAL_RESET

    # Global shutter should raise ValueError on pco.edge 5.5 sCMOS
    with pytest.raises(ValueError, match="not supported"):
        cam.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)

    # Metadata should record readout mode
    frames, metas = cam.acquire_frames(2)
    assert metas[0]["readout_mode"] == "GLOBAL_RESET"

    cam.close()

