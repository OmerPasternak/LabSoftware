"""Qt lifecycle tests, enabled when the pytest-qt development extra is installed."""

import pytest
import numpy as np
from types import SimpleNamespace
from PyQt6.QtCore import Qt

pytest.importorskip("pytestqt")

from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.ui.camera.camera_panel import CameraMainWindow
import hhg_control.ui.camera.camera_panel as camera_panel
from hhg_control.ui.camera.workers import LiveStreamTask


def test_measurement_log_reports_complete_scan_duration(qtbot, tmp_path):
    """A mock-camera GUI scan reports elapsed capture and save time."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    camera.set_roi((0, 1052, 2560, 1108))
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager.camera = camera
    window.txt_storage_dir.setText(str(tmp_path))
    window.spn_roi_y0.setValue(1052)
    window.spn_frames.setValue(1)
    window.spn_num_steps.setValue(2)

    window._toggle_measurement_scan()
    qtbot.waitUntil(
        lambda: "[SCAN COMPLETE]" in window.txt_activity_log.toPlainText()
        and window.active_scan_task is None,
        timeout=10000,
    )
    log = window.txt_activity_log.toPlainText()
    assert "All 2 steps saved to disk. Measurement duration:" in log
    assert len(list(tmp_path.glob("*.h5"))) == 2
    window.close()


def test_live_view_resumes_after_mock_measurement(qtbot, tmp_path):
    """A scan returns camera ownership to the live worker after saving."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    camera.set_roi((800, 1052, 1056, 1108))
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window.txt_storage_dir.setText(str(tmp_path))
    window.spn_roi_x0.setValue(800)
    window.spn_roi_x1.setValue(1056)
    window.spn_roi_y0.setValue(1052)
    window.spn_roi_y1.setValue(1108)
    window.spn_frames.setValue(2)
    window.spn_num_steps.setValue(2)

    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 1, timeout=3000)
    window._toggle_measurement_scan()
    qtbot.waitUntil(
        lambda: window.active_scan_task is None and window._is_live_active
        and window.scan_manager.state == "LIVE"
        and "[SCAN COMPLETE]" in window.txt_activity_log.toPlainText(),
        timeout=10000,
    )
    assert len(list(tmp_path.glob("*.h5"))) == 2
    assert window.scan_manager.state == "LIVE"
    assert "[LIVE] Resuming live view after measurement." in window.txt_activity_log.toPlainText()

    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=3000)
    assert window.scan_manager.state == "IDLE"
    window.close()


def test_live_view_returns_after_scan_preflight_rejection(qtbot, tmp_path, monkeypatch):
    """Rejecting an unapplied ROI leaves the prior live view available."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window.spn_roi_x0.setValue(148)
    window.spn_roi_x1.setValue(2292)
    warnings = []
    monkeypatch.setattr(camera_panel.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 1, timeout=3000)
    window._toggle_measurement_scan()
    qtbot.waitUntil(lambda: bool(warnings) and window._is_live_active, timeout=5000)
    assert window.active_scan_task is None
    assert "Click Apply ROI" in warnings[0]
    assert window.scan_manager.state == "LIVE"

    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=3000)
    window.close()


def test_stop_cancels_pending_live_to_scan_handoff(qtbot, tmp_path):
    """STOP during live shutdown must not start a queued measurement."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window.txt_storage_dir.setText(str(tmp_path))

    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 1, timeout=3000)
    window._toggle_measurement_scan()
    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=3000)
    assert window.active_scan_task is None
    assert window.scan_manager.state == "IDLE"
    assert not window._resume_live_after_scan
    assert not list(tmp_path.glob("*.h5"))
    window.close()


def test_measurement_rejects_unapplied_roi(qtbot, tmp_path, monkeypatch):
    """A drawn ROI cannot silently produce full-sensor measurement files."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager.camera = camera
    window.txt_storage_dir.setText(str(tmp_path))
    warnings = []
    monkeypatch.setattr(camera_panel.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window.spn_roi_x0.setValue(148)
    window.spn_roi_x1.setValue(2292)
    window.spn_roi_y0.setValue(544)

    window._toggle_measurement_scan()

    assert window.active_scan_task is None
    assert "Click Apply ROI" in warnings[0]
    assert "[SCAN BLOCKED]" in window.txt_activity_log.toPlainText()
    assert not list(tmp_path.glob("*.h5"))
    camera.close()


def test_measurement_rejects_run_larger_than_free_space(qtbot, tmp_path, monkeypatch):
    """Check the full remaining scan size before opening its first HDF5 file."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager.camera = camera
    window.txt_storage_dir.setText(str(tmp_path))
    window.spn_frames.setValue(1000)
    window.spn_num_steps.setValue(10)
    warnings = []
    monkeypatch.setattr(camera_panel.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(camera_panel.shutil, "disk_usage", lambda path: SimpleNamespace(free=1_000_000_000))

    window._toggle_measurement_scan()

    assert window.active_scan_task is None
    assert "only 0.9 GiB is free" in warnings[0]
    assert not list(tmp_path.glob("*.h5"))
    camera.close()


def test_measurement_duration_accumulates_active_time_across_pause(qtbot, monkeypatch):
    """Resume totals exclude time spent waiting for the user between segments."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    times = iter((12.0, 25.0))
    monkeypatch.setattr(camera_panel, "perf_counter", lambda: next(times))

    window._scan_segment_started_at = 10.0
    window._on_scan_aborted(0, 2, 0.0, "Setpoint")
    window._scan_segment_started_at = 20.0
    window._on_scan_finished(2)

    log = window.txt_activity_log.toPlainText()
    assert "Active measurement duration: 2.00 s" in log
    assert "Measurement duration: 7.00 s" in log


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


def test_live_worker_immediate_stop_before_run_stays_stopped(qtbot, tmp_path):
    """A queued worker must not re-enable itself after STOP was requested."""
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    manager = CameraScanManager(camera, tmp_path)
    worker = LiveStreamTask(manager)
    worker.stop()
    with qtbot.waitSignal(worker.finished, timeout=3000):
        worker.start()
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

    assert window.grp_color_scale.objectName() == "colorScaleControls"
    assert window.grp_color_scale.parent() is not window.canvas
    assert window.figure.dpi >= 120
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


def test_bright_scale_endpoints_are_directly_editable_and_axis_labels_fit(qtbot):
    """The color bar touches the image; screen-native coordinate labels fit."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.show()
    window._update_display(
        np.zeros((216, 256), dtype=np.uint16),
        {"roi": (0, 0, 2560, 2160)},
    )
    qtbot.wait(20)

    scale_left = window.grp_color_scale.mapToGlobal(window.grp_color_scale.rect().topLeft()).x()
    canvas_right = window.canvas.mapToGlobal(window.canvas.rect().topRight()).x()
    assert 0 <= scale_left - canvas_right <= 1
    assert abs(window._colorbar.ax.bbox.x0 - window.axis.bbox.x1) < 1
    assert window.canvas.coordinate_axis is window.axis
    assert not window.axis.get_xticklabels()
    assert not window.axis.get_yticklabels()
    assert window.axis.bbox.x0 >= 0.1 * window.canvas.width()
    assert not window.canvas.grab().isNull()
    assert window.spn_clim_high.isVisible()
    assert window.spn_clim_low.isVisible()
    assert len(window._colorbar.get_ticks()) == 0

    qtbot.mouseClick(window.spn_clim_high, Qt.MouseButton.LeftButton)
    window.spn_clim_high.lineEdit().selectAll()
    qtbot.keyClicks(window.spn_clim_high, "5000")
    qtbot.keyPress(window.spn_clim_high, Qt.Key.Key_Return)
    assert window.spn_clim_high.value() == 5000
    assert window._image_artist.get_clim() == (0, 5000)


def test_typing_upper_y_edge_in_start_field_keeps_centered_mock_roi(qtbot):
    """A typed Y=1116 must resolve to valid centered sensor-pixel bounds."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.spn_roi_y0.lineEdit().selectAll()

    qtbot.keyClicks(window.spn_roi_y0, "1116")
    qtbot.keyPress(window.spn_roi_y0, Qt.Key.Key_Return)

    assert (window.spn_roi_y0.value(), window.spn_roi_y1.value()) == (1044, 1116)
    window.camera.validate_roi((0, 1044, 2560, 1116))


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


def test_running_button_is_status_only_and_stop_ends_live(qtbot, tmp_path):
    """RUNNING cannot stop the stream; STOP remains the explicit stop action."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = MockPcoCamera(fast_simulation=True)
    window.camera.connect()
    window.camera.set_roi((800, 980, 1056, 1180))
    window.scan_manager = CameraScanManager(window.camera, tmp_path)
    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 2, timeout=4000)
    live_task = window.active_live_task

    window.btn_go.click()
    assert window.btn_go.text() == "RUNNING"
    assert window._is_live_active
    assert window.active_live_task is live_task
    qtbot.waitUntil(lambda: window._frame_count >= 3, timeout=4000)

    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=4000)
    assert not window._is_live_active
    assert window.cmb_camera_source.isEnabled()
    window.close()


def test_source_can_switch_after_stop_without_touching_real_hardware(qtbot, monkeypatch, tmp_path):
    """Changing source closes the old mock, then GO connects the chosen fake source."""
    from hhg_control.drivers import pco_edge

    class FakePhysicalCamera(MockPcoCamera):
        def __init__(self):
            super().__init__(fast_simulation=True)

    monkeypatch.setattr(pco_edge, "PcoEdgeCamera", FakePhysicalCamera)
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = MockPcoCamera(fast_simulation=True)
    window.camera.connect()
    window.camera.set_roi((800, 980, 1056, 1180))
    window.scan_manager = CameraScanManager(window.camera, tmp_path)
    window._connected_source = "simulated"
    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count >= 2, timeout=4000)
    original = window.camera

    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=4000)
    window.cmb_camera_source.setCurrentIndex(1)
    qtbot.waitUntil(lambda: window.active_disconnect_task is None, timeout=4000)
    assert not original.is_connected
    assert window._image_artist is None
    assert window.cmb_camera_source.currentData() == "physical"

    window.btn_go.click()
    qtbot.waitUntil(lambda: window._is_live_active and window._connected_source == "physical", timeout=4000)
    assert isinstance(window.camera, FakePhysicalCamera)
    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=4000)

    window.cmb_camera_source.setCurrentIndex(0)
    qtbot.waitUntil(lambda: window.active_disconnect_task is None, timeout=4000)
    window.btn_go.click()
    qtbot.waitUntil(lambda: window._is_live_active and window._connected_source == "simulated", timeout=4000)
    assert type(window.camera) is MockPcoCamera
    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_live_task is None, timeout=4000)
    window.close()
