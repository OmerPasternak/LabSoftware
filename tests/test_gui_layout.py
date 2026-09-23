"""Render-only checks for the camera viewer at common window sizes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication

from hhg_control.ui.camera.camera_panel import CameraMainWindow


def test_full_frame_and_color_scale_fit_when_window_resizes():
    """Keep the full sensor extent visible and its editable scale beside the bar."""
    app = QApplication.instance() or QApplication([])
    window = CameraMainWindow()
    try:
        window.show()
        assert (window.width(), window.height()) == (860, 500)
        frame = np.arange(216 * 256, dtype=np.uint16).reshape(216, 256)
        window._update_display(frame, {"roi": (0, 0, 2560, 2160)})

        for width, height in ((900, 520), (1040, 580), (1250, 700)):
            window.resize(width, height)
            app.processEvents()
            assert window.width() == width
            assert (window.canvas.width(), window.canvas.height()) == (350, 300)
            assert window.lbl_system_status.width() >= 60
            if width == 1040:
                panes = window.centralWidget().layout()
                assert panes.itemAt(0).geometry().width() > panes.itemAt(1).geometry().width()
            assert window.axis.get_xlim() == (0, 2560)
            assert window.axis.get_ylim() == (2160, 0)
            assert tuple(window._image_artist.get_extent()) == (0, 2560, 2160, 0)
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
    finally:
        window.close()
