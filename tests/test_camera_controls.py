"""Offline tests for ROI, live preview, and scientific frame metadata."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import time
import h5py
import pytest
from PyQt6.QtWidgets import QApplication
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.ui.camera.camera_panel import CameraMainWindow


@pytest.fixture
def camera():
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()
    yield cam
    cam.close()


def test_roi_shape_and_saved_metadata(camera, tmp_path):
    manager = CameraScanManager(camera, tmp_path)
    manager.set_roi((100, 1000, 300, 1160))
    path, frame = manager.acquire_and_save_step("ROI", 0, "delay fs", 0, 1)
    assert frame.shape == (160, 200)
    with h5py.File(path) as saved:
        assert tuple(saved.attrs["roi_xyxy_exclusive"]) == (100, 1000, 300, 1160)
        meta = json.loads(saved["frame_metadata_json"][0])
        assert meta["timestamp"] > 0
        assert meta["simulated"]
    manager.set_roi((0, 0, 2560, 2160))
    assert camera.acquire_frames(1)[0].shape == (1, 2160, 2560)


@pytest.mark.parametrize("roi", [(-4, 0, 2560, 2160), (1, 0, 101, 2160),
    (0, 0, 32, 2160), (0, 100, 2560, 2160), (0, 0, 2564, 2160)])
def test_invalid_roi_does_not_change_camera(camera, roi):
    original = camera.get_roi()
    with pytest.raises(ValueError):
        camera.set_roi(roi)
    assert camera.get_roi() == original


def test_live_stop_and_color_scale(camera):
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    window.camera = camera
    window.scan_manager.camera = camera
    camera.set_roi((100, 1000, 300, 1160))
    window.color_scale.setCurrentIndex(1)
    window._toggle_live()
    deadline = time.monotonic() + 10
    try:
        while window._image_artist is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window._image_artist is not None
        assert window._image_artist.get_clim() == (0, 65535)
        assert "Frame received (UTC):" in window.lbl_frame_time.text()
        assert not window.btn_connect.isEnabled()
        window._toggle_live()
        while (window.active_preview_task is not None or not window.btn_connect.isEnabled()) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.active_preview_task is None
        assert not window._live_requested
        assert window.btn_connect.isEnabled()
    finally:
        window._live_requested = False
        if window.active_preview_task is not None:
            window.active_preview_task.wait()
        app.processEvents()
        window.close()


def test_real_adapter_uses_sdk_roi_and_stops_on_failure(monkeypatch):
    # Replace the SDK entirely: this test never opens a camera.
    from types import SimpleNamespace
    from hhg_control.drivers import pco_edge
    import numpy as np

    class FakeSdkCamera:
        description = {"max width": 2560, "max height": 2160, "bit resolution": 16,
                       "min exposure time": 0.0005, "max exposure time": 2,
                       "roi steps": (4, 1), "min width": 64, "min height": 16,
                       "roi is horz symmetric": False, "roi is vert symmetric": True}
        configuration = {"binning": (1, 1), "roi": (1, 1, 2560, 2160)}
        is_recording = False
        recorded_image_count = 1
        fail = False
        stopped = False
        def record(self, number_of_images, mode):
            assert mode == "sequence non blocking"
            self.is_recording = True
        def stop(self):
            self.stopped = True
            self.is_recording = False
        def images(self):
            if self.fail:
                raise RuntimeError("USB disconnected")
            return [np.zeros((160, 200), dtype=np.uint16)], [{}]
        def close(self):
            pass

    sdk = FakeSdkCamera()
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kw: sdk), raising=False)
    cam = pco_edge.PcoEdgeCamera()
    cam.connect()
    cam.set_roi((100, 1000, 300, 1160))
    assert sdk.configuration["roi"] == (101, 1001, 300, 1160)
    assert cam.acquire_frames(1)[0].shape == (1, 160, 200)
    assert sdk.stopped
    with pytest.raises(ValueError):
        cam.set_exposure_time(3)
    sdk.stopped = False
    sdk.fail = True
    with pytest.raises(RuntimeError, match="USB disconnected"):
        cam.acquire_frames(1)
    assert sdk.stopped
    cam.close()
