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

