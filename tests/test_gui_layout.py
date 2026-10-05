"""Render-only checks for the camera viewer at common window sizes."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtCore import QObject, QEvent
from PyQt6.QtWidgets import QApplication, QLabel

from hhg_control.drivers.base_camera import ReadoutMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.ui.camera.camera_panel import CameraMainWindow


def test_live_metrics_do_not_repaint_right_control_panel(qtbot):
    """Streaming frames must not invalidate the stationary right-hand controls."""
    class PaintCounter(QObject):
        def __init__(self):
            super().__init__()
            self.paints = 0
            self.layouts = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.paints += 1
            if event.type() == QEvent.Type.LayoutRequest:
                self.layouts += 1
            return False

    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.show()
    window._update_display(np.zeros((216, 256), dtype=np.uint16),
                           {"roi": (0, 0, 2560, 2160)})
    qtbot.wait(150)
    counter = PaintCounter()
    window.control_panel.installEventFilter(counter)
    window.btn_take_measurement.installEventFilter(counter)
    layout_counter = PaintCounter()
    window.centralWidget().installEventFilter(layout_counter)
    for value in (10, 100, 65535, 20):
        for _ in range(5):
            window._update_display(np.full((216, 256), value, dtype=np.uint16),
                                   {"roi": (0, 0, 2560, 2160)})
            qtbot.wait(10)
    assert counter.paints == 0
    assert layout_counter.layouts == 0
    window.close()


def test_roi_draw_mode_ends_after_one_completed_rectangle():
    """A completed selection leaves its outline and unchecks Draw ROI."""
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    try:
        window.show()
        window._update_display(np.zeros((216, 256), dtype=np.uint16),
                               {"roi": (0, 0, 2560, 2160)})
        app.processEvents()
        window.btn_draw_roi.click()
        assert window.btn_draw_roi.isChecked()
        window._on_canvas_button_press(SimpleNamespace(
            inaxes=window.axis, xdata=800, ydata=800, button=1))
        window._on_canvas_button_release(SimpleNamespace(xdata=1200, ydata=1300))
        assert not window.btn_draw_roi.isChecked()
        assert window._roi_patch is not None
        window._on_canvas_button_press(SimpleNamespace(
            inaxes=window.axis, xdata=900, ydata=900, button=1))
        assert window._roi_drag_patch is None
    finally:
        window.close()


def test_full_frame_and_color_scale_fit_when_window_resizes():
    """Keep the full sensor extent visible and its editable scale beside the bar."""
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    try:
        window.show()
        assert (window.width(), window.height()) == (1254, 580)
        assert not window.grp_color_scale.isVisible()
        assert not window.spn_clim_high.isVisible()
        assert not window.spn_clim_low.isVisible()
        frame = np.arange(216 * 256, dtype=np.uint16).reshape(216, 256)
        window._update_display(frame, {"roi": (0, 0, 2560, 2160)})
        assert window.grp_color_scale.isVisible()

        for width, height in ((1254, 580), (1400, 650), (1600, 750)):
            window.resize(width, height)
            app.processEvents()
            assert window.width() == width
            assert (window.canvas.width(), window.canvas.height()) == (550, 450)
            assert window.lbl_intensity_metrics.width() == window.canvas.width() + window.grp_color_scale.width()
            assert window.lbl_intensity_metrics.x() == window.canvas.x()
            assert window.lbl_system_status.width() >= 60
            assert window.control_panel.width() == 582
            assert window.control_panel.height() == window.centralWidget().height() - 12
            for field in (window.spn_end_val, window.spn_num_steps,
                          window.txt_storage_dir, window.txt_file_header, window.txt_scan_param):
                assert field.x() >= 0
                assert field.geometry().right() < field.parentWidget().width()
            assert window.axis.get_xlim() == (0, 2560)
            assert window.axis.get_ylim() == (2160, 0)
            assert tuple(window._image_artist.get_extent()) == (0, 2560, 2160, 0)
            sensor_aspect = 2560 / 2160
            display_aspect = window.axis.bbox.width / window.axis.bbox.height
            assert abs(display_aspect / sensor_aspect - 1) < 0.05
            assert abs(window._colorbar.ax.bbox.x0 - window.axis.bbox.x1) < 1
            assert abs(window._colorbar.ax.bbox.x1 - window.canvas.width()) < 1
            assert window.grp_color_scale.x() == window.canvas.geometry().right() + 1
            pixel_ratio = window.canvas.device_pixel_ratio
            bar_top = window.canvas.y() + window.canvas.height() - window._colorbar.ax.bbox.y1 / pixel_ratio
            bar_bottom = window.canvas.y() + window.canvas.height() - window._colorbar.ax.bbox.y0 / pixel_ratio
            high_center = window.grp_color_scale.y() + window.spn_clim_high.geometry().center().y()
            low_center = window.grp_color_scale.y() + window.spn_clim_low.geometry().center().y()
            inset = round(0.75 * window.spn_clim_high.fontMetrics().height())
            assert abs(high_center - (bar_top + inset)) <= 2
            assert abs(low_center - (bar_bottom - inset)) <= 2
            assert window.spn_clim_high.geometry().bottom() < window.btn_auto_clim.y()
            assert window.btn_auto_clim.geometry().bottom() < window.btn_set_clim.y()
            assert window.btn_set_clim.geometry().bottom() < window.spn_clim_low.y()
            assert not window.canvas.grab().isNull()
        window.spn_clim_low.setValue(10)
        window.spn_clim_high.setValue(100)
        window.btn_set_clim.click()
        assert window._image_artist.get_clim() == (10, 100)
        window.btn_auto_clim.click()
        assert window._image_artist.get_clim() == (int(frame.min()), int(frame.max()))
        window._on_camera_disconnected()
        app.processEvents()
        assert not window.grp_color_scale.isVisible()
        window._update_display(frame, {"roi": (0, 0, 2560, 2160)})
        app.processEvents()
        assert window.grp_color_scale.isVisible()
    finally:
        window.close()


def test_gui_offers_actual_global_shutter_mode():
    """The mode selector must not present global reset as global shutter."""
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    try:
        modes = [window.cmb_readout_mode.itemData(i) for i in range(window.cmb_readout_mode.count())]
        assert modes == [ReadoutMode.ROLLING_SHUTTER, ReadoutMode.GLOBAL_SHUTTER]
        assert window.cmb_readout_mode.itemText(1) == "Global Shutter"
        window.show()
        app.processEvents()
        mode_box = window.cmb_readout_mode
        assert mode_box.geometry().right() <= mode_box.parentWidget().width() - 12
        assert all(mode_box.fontMetrics().horizontalAdvance(mode_box.itemText(i)) <= mode_box.width() - 24
                   for i in range(mode_box.count()))
    finally:
        window.close()


def test_camera_controls_name_counts_and_show_exposure_range():
    """Control labels describe frame and scan counts and the active exposure limit."""
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    try:
        window.show()
        app.processEvents()
        labels = {label.text() for label in window.findChildren(QLabel)}
        assert window.lbl_exposure.text() == "Exposure (ms, 0.5–2,000):"
        assert "Frames/step:" in labels
        assert "Steps:" in labels
        assert "N:" not in labels
        assert window.lbl_exposure.geometry().right() < window.spn_exposure.geometry().left()
        assert window.chk_external_trigger.geometry().left() > window.spn_exposure.geometry().right()
        assert not window.chk_external_trigger.isEnabled()
        window.spn_frames.setValue(window.spn_frames.maximum())
        editor = window.spn_frames.lineEdit()
        assert editor.fontMetrics().horizontalAdvance(editor.text()) < editor.width()

        window._set_exposure_mode_limit(ReadoutMode.GLOBAL_SHUTTER)
        assert window.lbl_exposure.text() == "Exposure (ms, 0.5–100):"
        assert window.spn_exposure.maximum() == 100.0
    finally:
        window.close()


def test_gui_connection_reflects_actual_mode_without_implicit_reboot(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    camera = MockPcoCamera()
    camera.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)
    camera.connect()
    started = []
    monkeypatch.setattr(window, "_start_mode_change", lambda *args, **kwargs: started.append(args))
    try:
        window._on_camera_connected(camera, True, 0.0)
        assert window.cmb_readout_mode.currentData() == ReadoutMode.GLOBAL_SHUTTER
        assert window.spn_exposure.maximum() == 100.0
        assert window.lbl_exposure.text() == "Exposure (ms, 0.5–100):"
        assert not started
    finally:
        camera.close()
        window.close()


def test_gui_applies_mode_explicitly_selected_before_connection(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    camera = MockPcoCamera()
    camera.connect()
    started = []
    monkeypatch.setattr(window, "_start_mode_change", lambda *args, **kwargs: started.append(args))
    try:
        window.cmb_readout_mode.setCurrentIndex(1)
        window._on_camera_connected(camera, True, 0.0)
        assert started == [(ReadoutMode.GLOBAL_SHUTTER,)]
    finally:
        camera.close()
        window.close()
