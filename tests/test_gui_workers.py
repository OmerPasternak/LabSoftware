"""Qt lifecycle tests, enabled when the pytest-qt development extra is installed."""

import pytest
import numpy as np
from types import SimpleNamespace

pytest.importorskip("pytestqt")

from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.ui.camera.camera_panel import CameraMainWindow
from hhg_control.ui.camera.workers import LiveStreamTask


def test_live_worker_stops_cooperatively(qtbot, tmp_path):
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    camera.set_roi((800, 980, 1056, 1180))
    manager = CameraScanManager(camera, tmp_path)
    worker = LiveStreamTask(manager, target_fps=20.0)

    with qtbot.waitSignal(worker.frame_ready, timeout=3000):
        worker.start()
    with qtbot.waitSignal(worker.finished, timeout=3000):
        worker.stop()

    assert manager.state == "IDLE"
    camera.close()


def test_gui_reuses_plot_artists_and_frame_metadata_roi(qtbot, monkeypatch):
    """Live repaint avoids camera calls on the GUI thread and plot rebuilds."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    assert window.camera._dark_bank is None

    def unexpected_camera_call():
        raise AssertionError("GUI rendering queried the camera")

    monkeypatch.setattr(window.camera, "get_roi", unexpected_camera_call)
    frame = np.zeros((200, 256), dtype=np.uint16)
    meta = {
        "roi": (800, 980, 1056, 1180),
        "camera_time_str": "12:00:00.000",
        "timestamp_source": "host_fallback",
    }
    window._update_display(frame, meta)
    image_artist = window._image_artist
    colorbar = window._colorbar

    assert window.grp_color_scale.title() == "Color Scale"
    assert not hasattr(window, "scale_tag")
    assert window._plot_background is not None
    assert tuple(image_artist.get_extent()) == (800, 1056, 1180, 980)
    assert window._timestamp_artist.get_text() == "Host 12:00:00.000"

    window._update_display(np.full_like(frame, 100), meta)
    assert window._image_artist is image_artist
    assert window._colorbar is colorbar
    assert window._plot_background is not None

    window._set_clim(10, 200)
    window._update_display(frame, meta)
    assert image_artist.get_clim() == (10, 200)


def test_mock_gui_live_start_stop_keeps_camera_idle(qtbot, tmp_path):
    """Exercise the complete worker-to-display flow without physical hardware."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = MockPcoCamera(fast_simulation=True)
    window.camera.connect()
    window.camera.set_roi((800, 980, 1056, 1180))
    window.scan_manager = CameraScanManager(window.camera, tmp_path)

    window._on_go_clicked()
    qtbot.waitUntil(lambda: window._frame_count >= 3, timeout=3000)
    assert window.scan_manager.state == "LIVE"
    assert window._image_artist is not None

    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=3000)
    assert window.scan_manager.state == "IDLE"
    window.close()


def test_roi_drag_pauses_live_frame_acknowledgements(qtbot):
    """The live worker waits during ROI dragging and resumes on release."""
    window = CameraMainWindow()
    qtbot.addWidget(window)

    class AcknowledgementCounter:
        count = 0

        def acknowledge_frame(self):
            self.count += 1

    counter = AcknowledgementCounter()
    window.active_live_task = counter
    window._is_live_active = True
    window._is_dragging_roi = True
    window._on_live_frame_ready(np.zeros((2, 2), dtype=np.uint16), {})
    assert counter.count == 0

    window._on_canvas_button_release(None)
    assert counter.count == 1
    window.active_live_task = None
    window._is_live_active = False


def test_draw_roi_keeps_image_visible_and_draws_outline(qtbot):
    """Entering draw mode and dragging must preserve the image and paint the ROI."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window._update_display(
        np.full((216, 256), 100, dtype=np.uint16),
        {"roi": (0, 0, 2560, 2160), "camera_time_str": "12:00:00"},
    )
    image = window._image_artist
    before = np.asarray(window.canvas.buffer_rgba()).copy()

    window.btn_draw_roi.click()
    assert window._image_artist is image
    assert np.array_equal(np.asarray(window.canvas.buffer_rgba()), before)

    window._on_canvas_button_press(SimpleNamespace(inaxes=window.axis, xdata=800, ydata=800, button=1))
    window._on_canvas_motion(SimpleNamespace(xdata=1200, ydata=1300))
    assert window._roi_drag_patch is not None
    window._on_canvas_button_release(SimpleNamespace(xdata=1200, ydata=1300))
    assert window._roi_drag_patch is None
    assert window._roi_patch is not None
    assert window._roi_patch.get_animated()
    assert window._roi_patch.axes is window.axis
    assert window._image_artist is image
    assert window.spn_roi_x0.value() == 800
    assert window.spn_roi_x1.value() == 1200
    edge_x, edge_y = window.axis.transData.transform((1000, window.spn_roi_y0.value()))
    pixels = np.asarray(window.canvas.buffer_rgba())
    row = pixels.shape[0] - int(edge_y)
    col = int(edge_x)
    edge = pixels[row - 3:row + 4, col - 3:col + 4, :3]
    assert np.any((edge[:, :, 0] > 150) & (edge[:, :, 1] < 100) & (edge[:, :, 2] < 100))


def test_repeated_roi_and_full_sensor_during_live_stays_open(qtbot, tmp_path):
    """ROI changes wait for live shutdown and repeated clicks never close Qt."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.show()
    window.camera = MockPcoCamera(fast_simulation=True)
    window.camera.connect()
    window.camera.set_roi((800, 980, 1056, 1180))
    window.scan_manager = CameraScanManager(window.camera, tmp_path)
    window.spn_roi_x0.setValue(800)
    window.spn_roi_x1.setValue(1056)
    window.spn_roi_y0.setValue(980)
    window.spn_roi_y1.setValue(1180)

    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 2, timeout=4000)
    for expected_roi, change in [
        ((800, 980, 1056, 1180), window._apply_roi),
        ((0, 0, 2560, 2160), window._reset_full_sensor),
        ((800, 980, 1056, 1180), window._apply_roi),
        ((0, 0, 2560, 2160), window._reset_full_sensor),
    ]:
        change()
        assert window._roi_change_pending
        assert not window.btn_apply_roi.isEnabled()
        qtbot.waitUntil(
            lambda: not window._roi_change_pending and window.scan_manager.state == "LIVE",
            timeout=6000,
        )
        assert window.camera.get_roi() == expected_roi
        assert window.isVisible()

    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=4000)
    window.close()
