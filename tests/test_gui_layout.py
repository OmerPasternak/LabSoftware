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
        frame = np.arange(216 * 256, dtype=np.uint16).reshape(216, 256)
        window._update_display(frame, {"roi": (0, 0, 2560, 2160)})

        for width, height in ((900, 520), (1040, 580), (1250, 700)):
            window.resize(width, height)
            app.processEvents()
            assert window.width() == width
            assert window.canvas.width() >= 350
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
            assert abs(high_center - bar_top) <= 2
            assert abs(low_center - bar_bottom) <= 2
            assert not window.canvas.grab().isNull()
    finally:
        window.close()
