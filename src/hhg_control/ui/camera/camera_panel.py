"""
PyQt6 Graphical User Interface for pco.edge 5.5 sCMOS Camera Control and Scan Sequencer.
Implements top-left Go/Stop controls, horizontal widescreen layout with side control panel,
bright, directly editable color-scale limits beside the image,
source-labelled frame timestamps, and hardware-constrained symmetrical ROI.
"""

import sys
import os
import shutil
import subprocess
from time import perf_counter
from collections.abc import Callable
from pathlib import Path
from typing import Optional
from datetime import datetime
import numpy as np

from PyQt6 import sip
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox,
    QAbstractSpinBox, QPushButton, QFileDialog, QTextEdit, QMessageBox,
    QGridLayout, QFrame, QSizePolicy, QComboBox, QCheckBox
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

from hhg_control.drivers.base_camera import BaseCamera, ReadoutMode, TriggerMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from .workers import (
    CameraConnectTask,
    CameraDisconnectTask,
    CameraModeTask,
    CameraTriggerTask,
    CameraRoiTask,
    LiveStreamTask,
    PreviewTask,
    ScanSequenceTask,
)

IMAGE_AXIS_RECT = (0.12, 0.08, 0.83, 0.86)
COLOR_AXIS_RECT = (0.95, 0.08, 0.05, 0.86)


class CameraFigureCanvas(FigureCanvasQTAgg):
    """Paint coordinate numbers as native Qt text over the fast Agg image canvas."""

    def __init__(self, figure: Figure) -> None:
        super().__init__(figure)
        self.coordinate_axis = None

    def _draw_idle(self) -> None:
        """Discard a queued Matplotlib draw after Qt destroys this canvas."""
        if sip.isdeleted(self):
            self._draw_pending = False
            return
        super()._draw_idle()

    def draw(self) -> None:
        """Finish pending Agg work safely if the window closes mid-redraw."""
        if sip.isdeleted(self):
            return
        try:
            super().draw()
        except RuntimeError:
            if not sip.isdeleted(self):
                raise

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        axis = self.coordinate_axis
        if axis is None or not hasattr(self, "renderer"):
            return

        # Agg rasterizes plot text at the canvas resolution. Qt draws these
        # labels at the screen's actual pixel density (including HiDPI screens).
        ratio = self.device_pixel_ratio
        bounds = axis.bbox
        left = bounds.x0 / ratio
        right = bounds.x1 / ratio
        top = self.height() - bounds.y1 / ratio
        bottom = self.height() - bounds.y0 / ratio
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setPen(QColor("#263542"))
            font = painter.font()
            font.setPointSizeF(9)
            painter.setFont(font)
            metrics = painter.fontMetrics()

            x_formatter = axis.xaxis.get_major_formatter()
            for value in axis.get_xticks():
                x = axis.transData.transform((value, axis.get_ylim()[0]))[0] / ratio
                if left - 0.5 <= x <= right + 0.5:
                    label = x_formatter(value)
                    painter.drawText(
                        QPointF(x - metrics.horizontalAdvance(label) / 2, bottom + 5 + metrics.ascent()),
                        label,
                    )

            y_formatter = axis.yaxis.get_major_formatter()
            for value in axis.get_yticks():
                y = self.height() - axis.transData.transform((axis.get_xlim()[0], value))[1] / ratio
                if top - 0.5 <= y <= bottom + 0.5:
                    label = y_formatter(value)
                    painter.drawText(
                        QPointF(left - 7 - metrics.horizontalAdvance(label), y + metrics.ascent() / 2),
                        label,
                    )
        finally:
            painter.end()


class ColorScaleControls(QFrame):
    """Center editable ADU limits beside the corresponding gradient endpoints."""

    def resizeEvent(self, event) -> None:
        """Reposition the limit fields whenever the image row changes height."""
        # Keep each limit three quarters of a text height inside the gradient.
        if self.layout() is not None and hasattr(self, "high_spin"):
            inset = round(0.75 * self.high_spin.fontMetrics().height())
            top_fraction = 1 - COLOR_AXIS_RECT[1] - COLOR_AXIS_RECT[3]
            bottom_fraction = COLOR_AXIS_RECT[1]
            top = max(0, round(self.height() * top_fraction + inset - self.high_spin.height() / 2))
            bottom = max(0, round(self.height() * bottom_fraction + inset - self.low_spin.height() / 2))
            self.layout().setContentsMargins(2, top, 2, bottom)
        super().resizeEvent(event)


class CameraMainWindow(QMainWindow):
    """Primary Application Window for HHG Laboratory Camera Control."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HHG Attosecond Lab - pco.edge 5.5 Camera Controller & Sequencer")
        self.resize(1254, 580)

        # Instrumentation layer
        self.camera: BaseCamera = MockPcoCamera()
        default_storage = Path.cwd() / "data"
        default_storage.mkdir(parents=True, exist_ok=True)
        self.scan_manager = CameraScanManager(camera=self.camera, storage_dir=default_storage)

        # Worker tasks
        self.active_scan_task: Optional[ScanSequenceTask] = None
        self.active_preview_task: Optional[PreviewTask] = None
        self.active_live_task: Optional[LiveStreamTask] = None
        self.active_connect_task: Optional[CameraConnectTask] = None
        self.active_disconnect_task: Optional[CameraDisconnectTask] = None
        self.active_mode_task: Optional[CameraModeTask] = None
        self.active_trigger_task: Optional[CameraTriggerTask] = None
        self.active_roi_task: Optional[CameraRoiTask] = None

        # State tracking
        self._is_live_active: bool = False
        self._is_rendering: bool = False
        self._is_updating_range: bool = False
        self._is_updating_roi: bool = False
        self._paused_step: Optional[int] = None
        self._paused_run_id: Optional[str] = None
        self._current_run_id: Optional[str] = None
        self._scan_active_elapsed_s: float = 0.0
        self._scan_segment_started_at: Optional[float] = None
        self._current_executing_step: int = 0
        self._frame_count: int = 0
        self._closing: bool = False
        self._after_live_stopped: Optional[Callable[[], None]] = None
        self._after_connect: Optional[Callable[[], None]] = None
        self._resume_live_after_mode: bool = False
        self._resume_live_after_trigger: bool = False
        self._trigger_change_succeeded: bool = False
        self._trigger_change_pending: bool = False
        self._resume_live_after_scan: bool = False
        self._scan_completed_successfully: bool = False

        # Matplotlib display caches
        self._image_artist = None
        self._colorbar = None
        self._timestamp_artist = None
        self._plot_background = None
        self._roi_patch = None           # persistent Rectangle patch drawn when ROI is applied or edited
        self._roi_drag_patch = None
        self._roi_drag_start: Optional[tuple[float, float]] = None
        self._roi_change_pending = False
        self._resume_live_after_roi = False
        self._roi_change_full_sensor = False
        self._connected_source: Optional[str] = None
        self._mode_requested_before_connect = False
        self._source_switch_pending = False
        self._clim_low: int = 0          # current lower color limit (ADU)
        self._clim_high: int = 65535     # current upper color limit (ADU)
        self._current_displayed_roi: Optional[tuple[int, int, int, int]] = None
        self._is_dragging_roi: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Root layout: Horizontal side-by-side (Image on Left, Controls on Right)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(8)

        # =========================================================================
        # Left Pane: Top bar + Canvas + Intensity metrics
        # =========================================================================
        left_pane = QVBoxLayout()
        left_pane.setSpacing(4)

        # Top bar: GO / STOP / Status
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.cmb_camera_source = QComboBox()
        self.cmb_camera_source.setFixedWidth(165)
        self.cmb_camera_source.addItem("Simulated", userData="simulated")
        self.cmb_camera_source.addItem("Physical pco.edge", userData="physical")
        self.cmb_camera_source.setToolTip(
            "Simulated generates synthetic frames without hardware. Physical pco.edge "
            "tries the USB 3.0 camera through the pco SDK when GO is pressed; "
            "connection failures never fall back to simulated data."
        )
        self.cmb_camera_source.currentIndexChanged.connect(self._on_camera_source_changed)
        top_bar.addWidget(self.cmb_camera_source)

        self.btn_go = QPushButton("GO")
        self._set_go_button_style(active=False)
        self.btn_go.setToolTip("Connect and start continuous live camera view.")
        self.btn_go.clicked.connect(self._on_go_clicked)
        top_bar.addWidget(self.btn_go)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(
            "background-color: #dc3545; border: 2px solid #dc3545; color: white; "
            "font-weight: bold; font-size: 12px; padding: 4px 14px; border-radius: 4px;"
        )
        self.btn_stop.setToolTip("Stop all operations immediately.")
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        top_bar.addWidget(self.btn_stop)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        top_bar.addWidget(sep)

        self.lbl_system_status = QLabel("Status: Idle / Ready")
        self.lbl_system_status.setMinimumWidth(0)
        self.lbl_system_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_system_status.setWordWrap(True)
        self.lbl_system_status.setStyleSheet(
            "font-weight: bold; font-size: 11px; color: #198754; padding-left: 2px;"
        )
        top_bar.addWidget(self.lbl_system_status)

        left_pane.addLayout(top_bar)

        # Keep the image and its color bar in one canvas. The bar occupies the
        # right edge, immediately before the editable limit controls.
        self.figure = Figure(figsize=(3.5, 3), dpi=120)
        image_row = QHBoxLayout()
        image_row.setContentsMargins(0, 0, 0, 0)
        image_row.setSpacing(0)

        self.canvas = CameraFigureCanvas(self.figure)
        self.canvas.setFixedSize(550, 450)
        self.axis = self.figure.add_axes(IMAGE_AXIS_RECT)
        self.canvas.coordinate_axis = self.axis
        self.axis.tick_params(axis="both", labelbottom=False, labelleft=False, colors="#263542")
        self.canvas.mpl_connect("button_press_event", self._on_canvas_button_press)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_button_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_canvas_motion)
        self.canvas.mpl_connect("draw_event", self._on_canvas_draw)
        image_row.addWidget(self.canvas)

        # The numbers themselves are editable and align with the bar endpoints.
        self.grp_color_scale = ColorScaleControls()
        self.grp_color_scale.setObjectName("colorScaleControls")
        self.grp_color_scale.setFixedSize(86, 450)
        self.grp_color_scale.setStyleSheet("""
            QFrame#colorScaleControls {
                background-color: #ffffff;
                border: none;
            }
            QSpinBox {
                font-size: 11px;
                font-weight: bold;
                color: #213547;
                padding: 2px 3px;
                background: #f8fbff;
                border: 1px solid #b8cad9;
                border-radius: 3px;
            }
            QPushButton {
                font-size: 10px;
                color: #28445a;
                padding: 2px;
                background-color: #eef4f8;
                border: 1px solid #c6d5df;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #dcecf7;
            }
        """)
        lay_scale = QVBoxLayout(self.grp_color_scale)
        lay_scale.setContentsMargins(2, 25, 2, 32)
        lay_scale.setSpacing(5)

        self.spn_clim_high = QSpinBox()
        self.spn_clim_high.setRange(1, 65535)
        self.spn_clim_high.setValue(65535)
        self.spn_clim_high.setFixedSize(80, 26)
        self.spn_clim_high.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_clim_high.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spn_clim_high.setToolTip("Color scale maximum (ADU). Click this number to edit it.")
        self.spn_clim_high.setKeyboardTracking(False)
        self.spn_clim_high.valueChanged.connect(self._on_clim_changed)
        self.spn_clim_high.editingFinished.connect(self._on_clim_changed)
        lay_scale.addWidget(self.spn_clim_high)

        self.spn_clim_low = QSpinBox()
        self.spn_clim_low.setRange(0, 65534)
        self.spn_clim_low.setValue(0)
        self.spn_clim_low.setFixedSize(80, 26)
        self.spn_clim_low.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_clim_low.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spn_clim_low.setToolTip("Color scale minimum (ADU). Click this number to edit it.")
        self.spn_clim_low.setKeyboardTracking(False)
        self.spn_clim_low.valueChanged.connect(self._on_clim_changed)
        self.spn_clim_low.editingFinished.connect(self._on_clim_changed)
        self.grp_color_scale.high_spin = self.spn_clim_high
        self.grp_color_scale.low_spin = self.spn_clim_low


        self.btn_auto_clim = QPushButton("Auto")
        self.btn_auto_clim.setFixedWidth(80)
        self.btn_auto_clim.setToolTip("Auto-scale color limits to current frame min/max")
        self.btn_auto_clim.clicked.connect(self._on_clim_auto_clicked)
        lay_scale.addWidget(self.btn_auto_clim)

        self.btn_set_clim = QPushButton("Set")
        self.btn_set_clim.setFixedWidth(80)
        self.btn_set_clim.setToolTip("Apply the edited color limits")
        self.btn_set_clim.clicked.connect(self._on_clim_apply_clicked)
        lay_scale.addWidget(self.btn_set_clim)
        lay_scale.addStretch(1)
        lay_scale.addWidget(self.spn_clim_low)
        image_row.addWidget(self.grp_color_scale)
        left_pane.addLayout(image_row)
        left_pane.setAlignment(image_row, Qt.AlignmentFlag.AlignLeft)
        self.grp_color_scale.hide()

        # Intensity metrics strip below canvas
        self.lbl_intensity_metrics = QLabel(
            "Pixel Intensity Metrics | Minimum: -- ADU | Maximum: -- ADU | Mean: -- ADU"
        )
        self.lbl_intensity_metrics.setFixedWidth(636)
        self.lbl_intensity_metrics.setWordWrap(True)
        self.lbl_intensity_metrics.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.lbl_intensity_metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_intensity_metrics.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 3px 6px; background: #f8f9fa; "
            "border: 1px solid #dee2e6; border-radius: 3px; color: #212529;"
        )
        left_pane.addWidget(self.lbl_intensity_metrics)
        left_pane.addStretch(1)
        root_layout.addLayout(left_pane, stretch=2)

        # =========================================================================
        # Right Pane: Vertical control panel (Compact, clean spacing)
        # =========================================================================
        self.control_panel = QWidget()
        self.control_panel.setFixedWidth(582)
        right_pane = QVBoxLayout(self.control_panel)
        right_pane.setSpacing(6)
        right_pane.setContentsMargins(0, 0, 0, 0)

        # --- Group 1: Camera & Hardware ROI --------------------------------
        grp_camera = QGroupBox("Camera & Hardware ROI")
        lay_cam = QGridLayout(grp_camera)
        lay_cam.setSpacing(4)
        lay_cam.setContentsMargins(6, 8, 12, 6)

        self.lbl_exposure = QLabel("Exposure (ms, 0.5–10,000):")
        lay_cam.addWidget(self.lbl_exposure, 0, 0)
        self.spn_exposure = QDoubleSpinBox()
        self.spn_exposure.setMinimumWidth(84)
        self.spn_exposure.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_exposure.setRange(0.5, 10000.0)
        self.spn_exposure.setValue(10.0)
        self.spn_exposure.setDecimals(2)
        self.spn_exposure.valueChanged.connect(self._on_exposure_changed)
        lay_cam.addWidget(self.spn_exposure, 0, 1)

        self.chk_external_trigger = QCheckBox("External trigger")
        self.chk_external_trigger.setEnabled(False)
        self.chk_external_trigger.setToolTip(
            "One fixed-duration exposure starts on each accepted external pulse. "
            "Connect the camera trigger input; pulses above the camera's maximum "
            "frame rate may be missed. Live view waits for pulses when enabled."
        )
        self.chk_external_trigger.toggled.connect(self._on_external_trigger_toggled)
        lay_cam.addWidget(self.chk_external_trigger, 0, 2, 1, 2)

        lay_cam.addWidget(QLabel("Frames/step:"), 1, 0)
        self.spn_frames = QSpinBox()
        self.spn_frames.setMinimumWidth(104)
        self.spn_frames.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_frames.setRange(1, 9_999_999)
        self.spn_frames.setValue(5)
        self.spn_frames.setToolTip(
            "1–9,999,999 frames per scan step; storage space is checked before acquisition."
        )
        lay_cam.addWidget(self.spn_frames, 1, 1)

        # Readout mode selector
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        mode_label = QLabel("Mode")
        mode_label.setStyleSheet("font-size: 11px;")
        mode_label.setFixedWidth(44)
        mode_row.addWidget(mode_label)
        self.cmb_readout_mode = QComboBox()
        self.cmb_readout_mode.setFixedWidth(200)
        self.cmb_readout_mode.setStyleSheet("font-size: 11px;")
        self.cmb_readout_mode.addItem("Rolling Shutter", userData=ReadoutMode.ROLLING_SHUTTER)
        self.cmb_readout_mode.addItem("Global Shutter", userData=ReadoutMode.GLOBAL_SHUTTER)
        self.cmb_readout_mode.setCurrentIndex(0)
        self.cmb_readout_mode.setToolTip(
            "Rolling Shutter: rows exposed sequentially.\n\n"
            "Global Shutter: all rows start and stop exposure together.\n"
            "Switching triggers camera reboot (~5 s)."
        )
        self.cmb_readout_mode.currentIndexChanged.connect(self._on_readout_mode_changed)
        mode_row.addWidget(self.cmb_readout_mode)
        mode_row.addStretch(1)
        lay_cam.addLayout(mode_row, 1, 2, 1, 2)

        # ROI spinboxes — value changes immediately redraw ROI rectangle
        lay_cam.addWidget(QLabel("X:"), 2, 0)
        self.spn_roi_x0 = QSpinBox()
        self.spn_roi_x0.setMinimumWidth(60)
        self.spn_roi_x0.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_roi_x0.setRange(0, 2496)
        self.spn_roi_x0.setSingleStep(4)
        self.spn_roi_x0.setValue(0)
        self.spn_roi_x0.setKeyboardTracking(False)
        self.spn_roi_x0.setToolTip("ROI X Start (0–2496, 4-px steps)")
        self.spn_roi_x0.valueChanged.connect(self._on_roi_x0_changed)
        lay_cam.addWidget(self.spn_roi_x0, 2, 1)

        self.spn_roi_x1 = QSpinBox()
        self.spn_roi_x1.setMinimumWidth(60)
        self.spn_roi_x1.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_roi_x1.setRange(64, 2560)
        self.spn_roi_x1.setSingleStep(4)
        self.spn_roi_x1.setValue(2560)
        self.spn_roi_x1.setKeyboardTracking(False)
        self.spn_roi_x1.setToolTip("ROI X End (64–2560, 4-px steps)")
        self.spn_roi_x1.valueChanged.connect(self._on_roi_x1_changed)
        lay_cam.addWidget(self.spn_roi_x1, 2, 2, 1, 2)

        lay_cam.addWidget(QLabel("Y (sym):"), 3, 0)
        self.spn_roi_y0 = QSpinBox()
        self.spn_roi_y0.setMinimumWidth(60)
        self.spn_roi_y0.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        # Accept either sensor-side edge here. An upper-half coordinate is
        # normalized to the matching lower edge after editing.
        self.spn_roi_y0.setRange(0, 2160)
        self.spn_roi_y0.setValue(0)
        self.spn_roi_y0.setKeyboardTracking(False)
        self.spn_roi_y0.setToolTip("ROI Y lower edge; entering an upper edge (e.g. 1116) sets its symmetric pair.")
        self.spn_roi_y0.valueChanged.connect(self._on_roi_y0_changed)
        lay_cam.addWidget(self.spn_roi_y0, 3, 1)

        self.spn_roi_y1 = QSpinBox()
        self.spn_roi_y1.setMinimumWidth(60)
        self.spn_roi_y1.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_roi_y1.setRange(1088, 2160)
        self.spn_roi_y1.setValue(2160)
        self.spn_roi_y1.setKeyboardTracking(False)
        self.spn_roi_y1.setToolTip("ROI Y End — auto-set symmetrically")
        self.spn_roi_y1.valueChanged.connect(self._on_roi_y1_changed)
        lay_cam.addWidget(self.spn_roi_y1, 3, 2, 1, 2)

        lbl_roi_hint = QLabel("Y edges mirror around 1080; enter either edge. X in 4-px steps.")
        lbl_roi_hint.setWordWrap(True)
        lbl_roi_hint.setStyleSheet("font-size: 9px; color: #6c757d; font-style: italic;")
        lay_cam.addWidget(lbl_roi_hint, 4, 0, 1, 4)

        roi_btn_row = QHBoxLayout()
        roi_btn_row.setSpacing(4)
        self.btn_apply_roi = QPushButton("Apply ROI")
        self.btn_apply_roi.setMinimumWidth(70)
        self.btn_apply_roi.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_apply_roi.setStyleSheet("padding: 5px; font-weight: bold;")
        self.btn_apply_roi.clicked.connect(self._apply_roi)
        roi_btn_row.addWidget(self.btn_apply_roi)

        self.btn_full_sensor = QPushButton("Full Sensor")
        self.btn_full_sensor.setMinimumWidth(80)
        self.btn_full_sensor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_full_sensor.setStyleSheet("padding: 5px;")
        self.btn_full_sensor.clicked.connect(self._reset_full_sensor)
        roi_btn_row.addWidget(self.btn_full_sensor)

        self.btn_draw_roi = QPushButton("Draw ROI")
        self.btn_draw_roi.setMinimumWidth(70)
        self.btn_draw_roi.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_draw_roi.setCheckable(True)
        self.btn_draw_roi.setStyleSheet("padding: 5px;")
        self.btn_draw_roi.setToolTip("Drag a rectangle on the image to select ROI.")
        self.btn_draw_roi.toggled.connect(self._toggle_draw_roi)
        roi_btn_row.addWidget(self.btn_draw_roi)

        lay_cam.addLayout(roi_btn_row, 5, 0, 1, 4)
        right_pane.addWidget(grp_camera)

        # --- Group 2: Experiment Parameters --------------------------------
        grp_exp = QGroupBox("Experiment Parameters")
        lay_exp = QGridLayout(grp_exp)
        lay_exp.setSpacing(4)
        lay_exp.setContentsMargins(6, 8, 6, 6)

        lay_exp.addWidget(QLabel("Start"), 0, 0)
        self.spn_start_val = QDoubleSpinBox()
        self.spn_start_val.setMinimumWidth(95)
        self.spn_start_val.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_start_val.setRange(-1e6, 1e6)
        self.spn_start_val.setDecimals(4)
        self.spn_start_val.setValue(0.0)
        self.spn_start_val.valueChanged.connect(self._on_start_val_changed)
        lay_exp.addWidget(self.spn_start_val, 1, 0)

        lay_exp.addWidget(QLabel("End:"), 0, 1)
        self.spn_end_val = QDoubleSpinBox()
        self.spn_end_val.setMinimumWidth(95)
        self.spn_end_val.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_end_val.setRange(-1e6, 1e6)
        self.spn_end_val.setDecimals(4)
        self.spn_end_val.setValue(0.4500)
        self.spn_end_val.valueChanged.connect(self._on_end_val_changed)
        lay_exp.addWidget(self.spn_end_val, 1, 1)

        lay_exp.addWidget(QLabel("Step:"), 2, 0)
        self.spn_step_size = QDoubleSpinBox()
        self.spn_step_size.setMinimumWidth(95)
        self.spn_step_size.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_step_size.setRange(-1e6, 1e6)
        self.spn_step_size.setDecimals(4)
        self.spn_step_size.setValue(0.0500)
        self.spn_step_size.valueChanged.connect(self._on_step_size_changed)
        lay_exp.addWidget(self.spn_step_size, 3, 0)

        lay_exp.addWidget(QLabel("Steps:"), 2, 1)
        self.spn_num_steps = QSpinBox()
        self.spn_num_steps.setMinimumWidth(70)
        self.spn_num_steps.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spn_num_steps.setRange(1, 100000)
        self.spn_num_steps.setValue(10)
        self.spn_num_steps.valueChanged.connect(self._on_num_steps_changed)
        lay_exp.addWidget(self.spn_num_steps, 3, 1)

        self.lbl_scan_progress = QLabel("Scan Progress: Ready (0 of 10 steps) | Delay Stage (mm): 0.0000")
        self.lbl_scan_progress.setStyleSheet(
            "font-weight: bold; font-size: 10px; color: #0d6efd; padding: 2px 0px;"
        )
        self.lbl_scan_progress.setWordWrap(True)
        lay_exp.addWidget(self.lbl_scan_progress, 4, 0, 1, 2)

        scan_btn_row = QHBoxLayout()
        scan_btn_row.setSpacing(4)
        self.btn_take_measurement = QPushButton("Take Measurement")
        self.btn_take_measurement.setStyleSheet(
            "font-weight: bold; font-size: 12px; background-color: #0d6efd; color: white; padding: 6px;"
        )
        self.btn_take_measurement.setToolTip("Start automated scan sequence.")
        self.btn_take_measurement.clicked.connect(self._toggle_measurement_scan)
        scan_btn_row.addWidget(self.btn_take_measurement, stretch=3)

        self.btn_cut_measurement = QPushButton("Cut")
        self.btn_cut_measurement.setStyleSheet(
            "font-weight: bold; font-size: 11px; background-color: #6c757d; color: white; padding: 6px;"
        )
        self.btn_cut_measurement.setVisible(False)
        self.btn_cut_measurement.clicked.connect(self._cut_measurement)
        scan_btn_row.addWidget(self.btn_cut_measurement, stretch=1)

        lay_exp.addLayout(scan_btn_row, 5, 0, 1, 2)
        right_pane.addWidget(grp_exp)

        # --- Group 3: Data Storage & Log (Compact log window) -------------
        grp_storage = QGroupBox("Storage & Log")
        lay_storage = QVBoxLayout(grp_storage)
        lay_storage.setSpacing(4)
        lay_storage.setContentsMargins(6, 8, 6, 6)

        store_grid = QGridLayout()
        store_grid.setSpacing(4)

        store_grid.addWidget(QLabel("Folder:"), 0, 0)
        self.txt_storage_dir = QLineEdit(str(self.scan_manager.storage_dir))
        self.txt_storage_dir.editingFinished.connect(self._on_storage_dir_edited)
        store_grid.addWidget(self.txt_storage_dir, 0, 1)

        self.btn_browse = QPushButton("Browse")
        self.btn_browse.clicked.connect(self._browse_directory)

        self.btn_open_folder = QPushButton("Open")
        self.btn_open_folder.clicked.connect(self._open_storage_folder)
        folder_buttons = QHBoxLayout()
        folder_buttons.addStretch(1)
        folder_buttons.addWidget(self.btn_browse)
        folder_buttons.addWidget(self.btn_open_folder)
        store_grid.addLayout(folder_buttons, 1, 0, 1, 2)

        store_grid.addWidget(QLabel("Header:"), 2, 0)
        self.txt_file_header = QLineEdit("HHG Scan")
        store_grid.addWidget(self.txt_file_header, 2, 1)

        store_grid.addWidget(QLabel("Param:"), 3, 0)
        self.txt_scan_param = QLineEdit("Delay Stage (mm)")
        self.txt_scan_param.textChanged.connect(lambda _: self._update_progress_display())
        store_grid.addWidget(self.txt_scan_param, 3, 1)

        lay_storage.addLayout(store_grid)

        # Greatly reduced height for activity log so it doesn't take over vertical space
        self.txt_activity_log = QTextEdit()
        self.txt_activity_log.setReadOnly(True)
        self.txt_activity_log.setStyleSheet("font-family: Consolas, monospace; font-size: 10px;")
        self.txt_activity_log.setFixedHeight(60)
        lay_storage.addWidget(self.txt_activity_log)

        right_pane.addWidget(grp_storage)
        right_pane.addStretch(1)

        root_layout.addWidget(self.control_panel)

        self._update_progress_display()

    # =========================================================================
    # GO and STOP Controls (Top-Left Docked)
    # =========================================================================
    def _set_go_button_style(self, active: bool) -> None:
        if active:
            self.btn_go.setText("RUNNING")
            self.btn_go.setToolTip("Live view is running. Use STOP to end it.")
            self.btn_go.setStyleSheet(
                "border: 2px solid #28a745; background-color: #28a745; color: white; "
                "font-weight: bold; font-size: 13px; padding: 6px 20px; border-radius: 4px;"
            )
        else:
            self.btn_go.setText("GO")
            self.btn_go.setToolTip("Connect the selected camera and start live view.")
            self.btn_go.setStyleSheet(
                "border: 2px solid #28a745; background-color: transparent; color: #28a745; "
                "font-weight: bold; font-size: 13px; padding: 6px 20px; border-radius: 4px;"
            )

    def _on_go_clicked(self) -> None:
        """Connect if disconnected, then start continuous live camera view."""
        if self._is_live_active:
            return
        if (self.active_live_task is not None or self.active_disconnect_task is not None
                or self.active_trigger_task is not None):
            return

        if not self.camera.is_connected:
            self._connect_camera(after_connect=self._start_live)
            return
        self._start_live()

    def _start_live(self) -> None:
        if not self.camera.is_connected:
            return
        if self.active_live_task is not None or self._source_switch_pending or self.active_trigger_task is not None:
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._append_log("[LIVE] Cannot start live stream while an experiment scan is running.")
            return

        self._is_live_active = True
        self._set_go_button_style(active=True)
        self.cmb_camera_source.setEnabled(False)
        self.lbl_system_status.setText("Status: Live View Active (Streaming)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")

        self.active_live_task = LiveStreamTask(scan_manager=self.scan_manager, target_fps=20.0)
        self.active_live_task.frame_ready.connect(self._on_live_frame_ready)
        self.active_live_task.error_occurred.connect(self._on_preview_error)
        self.active_live_task.finished.connect(self._on_live_task_finished)
        self.active_live_task.start()

    def _stop_live(self, after_stop: Optional[Callable[[], None]] = None) -> None:
        """Request live stop and continue only after the worker confirms completion."""
        self._is_live_active = False
        if after_stop is not None:
            self._after_live_stopped = after_stop
        self._set_go_button_style(active=False)
        self.lbl_system_status.setText("Status: Stopping live acquisition...")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #6c757d; padding-left: 8px;")

        if self.active_live_task is not None:
            self.active_live_task.stop()
        else:
            self._on_live_task_finished()

    def _on_live_task_finished(self) -> None:
        task = self.active_live_task
        self.active_live_task = None
        if task is not None:
            task.deleteLater()
        callback = self._after_live_stopped
        self._after_live_stopped = None
        if self._closing:
            self.close()
        elif callback is not None:
            callback()
        else:
            self.lbl_system_status.setText("Status: Live Paused")
            self.cmb_camera_source.setEnabled(not self._roi_change_pending)

    def _on_stop_clicked(self) -> None:
        """Global Stop: halts live view or gracefully aborts in-progress scan."""
        self._append_log("[STOP] Stop button pressed.")
        self._resume_live_after_roi = False
        self._resume_live_after_trigger = False
        if self._trigger_change_pending:
            self._trigger_change_pending = False
            self._after_live_stopped = None
            self.chk_external_trigger.setEnabled(self.camera.is_connected)
            self.chk_external_trigger.blockSignals(True)
            self.chk_external_trigger.setChecked(not self.chk_external_trigger.isChecked())
            self.chk_external_trigger.blockSignals(False)
        if self._resume_live_after_scan:
            self._resume_live_after_scan = False
            self._after_live_stopped = None
        self._after_connect = None
        if self._is_live_active:
            self._stop_live()

        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self.lbl_system_status.setText("Status: Aborting scan at next safe frame batch...")
            self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
            self.btn_take_measurement.setText("Stopping...")
            self.btn_take_measurement.setEnabled(False)
            self.active_scan_task.request_abort()

    def _connect_camera(self, after_connect: Optional[Callable[[], None]] = None) -> None:
        """Connect the explicitly selected camera source in a worker thread."""
        if self.active_disconnect_task is not None or self._source_switch_pending:
            return
        if self.active_connect_task is not None and self.active_connect_task.isRunning():
            return
        self._after_connect = after_connect
        self.lbl_system_status.setText("Status: Connecting to camera...")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        self.btn_go.setEnabled(False)
        self.cmb_camera_source.setEnabled(False)
        source = self.cmb_camera_source.currentData()
        self.active_connect_task = CameraConnectTask(source, self.spn_exposure.value() / 1000.0)
        self.active_connect_task.connected.connect(self._on_camera_connected)
        self.active_connect_task.error_occurred.connect(self._on_camera_connection_error)
        self.active_connect_task.finished.connect(self._on_camera_connection_finished)
        self.active_connect_task.start()

    def _on_camera_connected(self, camera: BaseCamera, is_sim: bool, connection_time: float) -> None:
        self.camera = camera
        self.scan_manager.camera = self.camera
        self._connected_source = "simulated" if is_sim else "physical"
        self._sync_trigger_checkbox()
        self.chk_external_trigger.setEnabled(True)
        info = self.camera.get_sensor_info()
        model_name = info.get("model", "pco.edge 5.5")
        if is_sim:
            self._append_log(f"[CONNECT] Connected to explicitly selected simulated camera in {connection_time:.2f} s.")
            self.lbl_system_status.setText("Status: Connected (Simulated Camera)")
        else:
            self._append_log(f"[CONNECT] Connected to physical {model_name} on USB 3.0 in {connection_time:.2f} s.")
            self.lbl_system_status.setText(f"Status: Connected ({model_name} USB 3.0)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
        actual_mode = self.camera.get_readout_mode()
        desired_mode = self.cmb_readout_mode.currentData()
        requested = self._mode_requested_before_connect
        self._mode_requested_before_connect = False
        if requested and desired_mode is not None and actual_mode != desired_mode:
            callback = self._after_connect
            self._after_connect = None
            self._start_mode_change(desired_mode, resume_live=False, after_mode=callback)
        else:
            index = self.cmb_readout_mode.findData(actual_mode)
            self.cmb_readout_mode.blockSignals(True)
            if index < 0:
                self.cmb_readout_mode.setPlaceholderText(f"{actual_mode.name.replace('_', ' ').title()} (current)")
            self.cmb_readout_mode.setCurrentIndex(index)
            self.cmb_readout_mode.blockSignals(False)
            self._set_exposure_mode_limit(actual_mode)

    def _on_camera_connection_error(self, error: str) -> None:
        source_name = self.cmb_camera_source.currentText()
        self._append_log(f"[CONNECT ERROR] {source_name}: {error}")
        self.lbl_system_status.setText(f"Status: {source_name} connection failed")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #dc3545; padding-left: 8px;")
        QMessageBox.warning(
            self,
            "Camera Connection Failed",
            f"Could not connect to {source_name}:\n\n{error}\n\nNo simulated-camera fallback was performed.",
        )
        self._after_connect = None

    def _on_camera_connection_finished(self) -> None:
        task = self.active_connect_task
        self.active_connect_task = None
        if task is not None:
            task.deleteLater()
        self.btn_go.setEnabled(True)
        if self._closing:
            self.close()
            return
        if not self.camera.is_connected:
            self.cmb_camera_source.setEnabled(True)
            return
        callback = self._after_connect
        self._after_connect = None
        if callback is not None and self.active_mode_task is None:
            callback()
        if not self._is_live_active and self.active_mode_task is None:
            self.cmb_camera_source.setEnabled(True)

    def _on_camera_source_changed(self, index: int) -> None:
        """Release a stopped camera before GO can connect a different source."""
        del index
        selected = self.cmb_camera_source.currentData()
        if not self.camera.is_connected or selected == self._connected_source:
            return
        if self._is_live_active or self.active_live_task is not None:
            self._restore_connected_source_selection()
            return
        if (self.active_connect_task is not None or self.active_disconnect_task is not None
                or self.active_mode_task is not None or self.active_roi_task is not None
                or self.active_trigger_task is not None):
            self._restore_connected_source_selection()
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._restore_connected_source_selection()
            return
        self._source_switch_pending = True
        self.btn_go.setEnabled(False)
        self.cmb_camera_source.setEnabled(False)
        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            self.lbl_system_status.setText("Status: Waiting for preview before switching camera...")
        else:
            self._begin_camera_disconnect()

    def _restore_connected_source_selection(self) -> None:
        """Keep the selector aligned with the still-connected camera."""
        if self._connected_source is None:
            return
        index = self.cmb_camera_source.findData(self._connected_source)
        if index >= 0:
            self.cmb_camera_source.blockSignals(True)
            self.cmb_camera_source.setCurrentIndex(index)
            self.cmb_camera_source.blockSignals(False)

    def _begin_camera_disconnect(self) -> None:
        """Close the previous source in a worker, with no live or preview read active."""
        self.lbl_system_status.setText("Status: Disconnecting previous camera...")
        self.active_disconnect_task = CameraDisconnectTask(self.camera)
        self.active_disconnect_task.disconnected.connect(self._on_camera_disconnected)
        self.active_disconnect_task.error_occurred.connect(self._on_camera_disconnect_error)
        self.active_disconnect_task.finished.connect(self._on_camera_disconnect_finished)
        self.active_disconnect_task.start()

    def _on_camera_disconnected(self) -> None:
        """Clear old-source pixels so simulated frames cannot look like real data."""
        self._connected_source = None
        self.chk_external_trigger.setEnabled(False)
        self.chk_external_trigger.blockSignals(True)
        self.chk_external_trigger.setChecked(False)
        self.chk_external_trigger.blockSignals(False)
        self.camera = MockPcoCamera()
        self.scan_manager.camera = self.camera
        self.figure.clear()
        self.axis = self.figure.add_axes(IMAGE_AXIS_RECT)
        self.canvas.coordinate_axis = self.axis
        self.axis.tick_params(axis="both", labelbottom=False, labelleft=False, colors="#263542")
        self._image_artist = None
        self._colorbar = None
        self.grp_color_scale.hide()
        self._timestamp_artist = None
        self._roi_patch = None
        self._roi_drag_patch = None
        self._roi_drag_start = None
        self._plot_background = None
        self._current_displayed_roi = None
        self._last_frame = None
        self.lbl_intensity_metrics.setText(
            "Pixel Intensity Metrics | Minimum: -- ADU | Maximum: -- ADU | Mean: -- ADU"
        )
        self.canvas.draw_idle()
        self._append_log("[CAMERA] Previous source disconnected. Press GO to connect the selected source.")
        self.lbl_system_status.setText("Status: Source selected / Ready")

    def _on_camera_disconnect_error(self, error: str) -> None:
        """Retain the prior source when it cannot be safely closed."""
        self._restore_connected_source_selection()
        self.lbl_system_status.setText("Status: Camera disconnect failed")
        self._append_log(f"[CAMERA DISCONNECT ERROR] {error}")
        QMessageBox.warning(self, "Camera Disconnect Failed", error)

    def _on_camera_disconnect_finished(self) -> None:
        """Unlock source selection after the disconnect worker has exited."""
        task = self.active_disconnect_task
        self.active_disconnect_task = None
        if task is not None:
            task.deleteLater()
        self._source_switch_pending = False
        if self._closing:
            self.close()
            return
        self.btn_go.setEnabled(True)
        self.cmb_camera_source.setEnabled(True)

    def _on_readout_mode_changed(self, index: int) -> None:
        """Handle switching between rolling and global shutter modes."""
        mode = self.cmb_readout_mode.itemData(index)
        if mode is None:
            return
        if self.active_trigger_task is not None or self._trigger_change_pending:
            self._append_log("[READOUT MODE] Wait for the trigger mode change to finish.")
            return

        # Block mode changes while an automated scan is actively writing datasets
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            QMessageBox.warning(
                self,
                "Scan in Progress",
                "Cannot change sensor readout mode while an experiment scan is executing."
            )
            self.cmb_readout_mode.blockSignals(True)
            curr = self.camera.get_readout_mode()
            for idx in range(self.cmb_readout_mode.count()):
                if self.cmb_readout_mode.itemData(idx) == curr:
                    self.cmb_readout_mode.setCurrentIndex(idx)
                    break
            self.cmb_readout_mode.blockSignals(False)
            return

        if not self.camera.is_connected:
            self._mode_requested_before_connect = True
            return

        if hasattr(self.camera, "get_readout_mode") and self.camera.get_readout_mode() == mode:
            return

        was_live = self._is_live_active
        if was_live:
            self._stop_live(after_stop=lambda: self._start_mode_change(mode, resume_live=True))
            return
        self._start_mode_change(mode, resume_live=False)

    def _start_mode_change(
        self,
        mode: ReadoutMode,
        resume_live: bool,
        after_mode: Optional[Callable[[], None]] = None,
    ) -> None:
        if self.active_mode_task is not None and self.active_mode_task.isRunning():
            return
        self.lbl_system_status.setText(f"Status: Switching to {mode.name}... (Camera reconfiguring)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        self._append_log(f"[READOUT MODE] Switching sensor mode to {mode.name} (PCO setup value {mode.value})...")
        self._resume_live_after_mode = resume_live
        self._after_connect = after_mode
        self.cmb_readout_mode.setEnabled(False)
        self.chk_external_trigger.setEnabled(False)
        self.cmb_camera_source.setEnabled(False)
        self.active_mode_task = CameraModeTask(self.camera, mode)
        self.active_mode_task.mode_applied.connect(self._on_mode_applied)
        self.active_mode_task.error_occurred.connect(self._on_mode_error)
        self.active_mode_task.finished.connect(self._on_mode_task_finished)
        self.active_mode_task.start()

    def _on_mode_applied(self, mode: ReadoutMode) -> None:
        self._set_exposure_mode_limit(mode)
        self._append_log(f"[READOUT MODE] Camera successfully configured to {mode.name}.")
        self.lbl_system_status.setText(f"Status: Mode Active ({mode.name})")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")

    def _set_exposure_mode_limit(self, mode: ReadoutMode) -> None:
        """Show the active readout mode's allowed exposure range in milliseconds."""
        global_shutter = mode == ReadoutMode.GLOBAL_SHUTTER
        self.spn_exposure.setMaximum(100.0 if global_shutter else 10000.0)
        maximum_label = "100" if global_shutter else "10,000"
        self.lbl_exposure.setText(f"Exposure (ms, 0.5–{maximum_label}):")

    def _on_mode_error(self, error: str) -> None:
        self._append_log(f"[READOUT MODE ERROR] {error}")
        QMessageBox.warning(self, "Readout Mode Error", f"Could not switch readout mode:\n\n{error}")
        current = self.camera.get_readout_mode()
        self.cmb_readout_mode.blockSignals(True)
        for index in range(self.cmb_readout_mode.count()):
            if self.cmb_readout_mode.itemData(index) == current:
                self.cmb_readout_mode.setCurrentIndex(index)
                break
        self.cmb_readout_mode.blockSignals(False)

    def _on_mode_task_finished(self) -> None:
        task = self.active_mode_task
        self.active_mode_task = None
        if task is not None:
            task.deleteLater()
        self.cmb_readout_mode.setEnabled(True)
        if self._closing:
            self.close()
            return
        self._sync_trigger_checkbox()
        self.chk_external_trigger.setEnabled(True)
        callback = self._after_connect
        self._after_connect = None
        if callback is not None:
            callback()
        elif self._resume_live_after_mode:
            self._resume_live_after_mode = False
            self._start_live()
        else:
            self._capture_single_preview()
        if not self._is_live_active:
            self.cmb_camera_source.setEnabled(True)

    def _sync_trigger_checkbox(self) -> None:
        """Display the camera's verified trigger mode without issuing a change."""
        mode = self.scan_manager.get_trigger_mode()
        self.chk_external_trigger.blockSignals(True)
        self.chk_external_trigger.setChecked(mode == TriggerMode.EXTERNAL_EXPOSURE_START)
        self.chk_external_trigger.blockSignals(False)

    def _on_external_trigger_toggled(self, checked: bool) -> None:
        """Serialize a user trigger change with live and scan camera ownership."""
        if not self.camera.is_connected:
            return
        if (self.active_scan_task is not None and self.active_scan_task.isRunning()
                or self._roi_change_pending or self.active_mode_task is not None
                or self.active_trigger_task is not None or self.active_preview_task is not None):
            self._sync_trigger_checkbox()
            self._append_log("[TRIGGER] Wait for the current camera operation to finish.")
            return
        mode = TriggerMode.EXTERNAL_EXPOSURE_START if checked else TriggerMode.AUTO_SEQUENCE
        if self.scan_manager.get_trigger_mode() == mode:
            return
        if self._is_live_active:
            self._trigger_change_pending = True
            self.chk_external_trigger.setEnabled(False)
            self._stop_live(after_stop=lambda: self._start_trigger_change(mode, resume_live=True))
            return
        self._start_trigger_change(mode, resume_live=False)

    def _start_trigger_change(self, mode: TriggerMode, *, resume_live: bool) -> None:
        """Apply the selected trigger in a worker after the live reader stops."""
        self._trigger_change_pending = False
        if self._closing:
            self.close()
            return
        self._resume_live_after_trigger = resume_live
        self._trigger_change_succeeded = False
        self.chk_external_trigger.setEnabled(False)
        self.cmb_readout_mode.setEnabled(False)
        self.cmb_camera_source.setEnabled(False)
        self.lbl_system_status.setText("Status: Configuring camera trigger...")
        self.active_trigger_task = CameraTriggerTask(self.scan_manager, mode)
        self.active_trigger_task.trigger_applied.connect(self._on_trigger_applied)
        self.active_trigger_task.error_occurred.connect(self._on_trigger_error)
        self.active_trigger_task.finished.connect(self._on_trigger_task_finished)
        self.active_trigger_task.start()

    def _on_trigger_applied(self, mode: TriggerMode) -> None:
        """Acknowledge a trigger change only after the worker's hardware readback."""
        self._trigger_change_succeeded = True
        self._sync_trigger_checkbox()
        self._append_log(f"[TRIGGER] Camera confirmed {mode.value}.")
        self.lbl_system_status.setText(f"Status: Trigger mode active ({mode.value})")

    def _on_trigger_error(self, error: str) -> None:
        self._trigger_change_succeeded = False
        self._append_log(f"[TRIGGER ERROR] {error}")
        try:
            self._sync_trigger_checkbox()
        except Exception:
            pass
        QMessageBox.warning(self, "Trigger Mode Error", error)

    def _on_trigger_task_finished(self) -> None:
        task = self.active_trigger_task
        self.active_trigger_task = None
        if task is not None:
            task.deleteLater()
        if self._closing:
            self.close()
            return
        self.chk_external_trigger.setEnabled(self.camera.is_connected)
        self.cmb_readout_mode.setEnabled(True)
        resume = self._resume_live_after_trigger and self._trigger_change_succeeded
        self._resume_live_after_trigger = False
        if resume:
            self._start_live()
        elif not self._is_live_active:
            self.cmb_camera_source.setEnabled(True)

    # =========================================================================
    # Live Preview & Single Capture
    # =========================================================================
    def _capture_single_preview(self) -> None:
        """Acquire a single frame and update display without starting live loop."""
        if not self.camera.is_connected:
            self._connect_camera(after_connect=self._capture_next_preview)
            return
        if not self._is_live_active:
            self._capture_next_preview()

    def _capture_next_preview(self) -> None:
        if not self.camera.is_connected or self._is_live_active:
            return
        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            return

        self.active_preview_task = PreviewTask(scan_manager=self.scan_manager)
        self.active_preview_task.frame_ready.connect(self._on_preview_frame_ready)
        self.active_preview_task.error_occurred.connect(self._on_preview_error)
        self.active_preview_task.finished.connect(self._on_preview_task_finished)
        self.active_preview_task.start()

    def _on_live_frame_ready(self, frame: np.ndarray, meta: dict) -> None:
        """Handle incoming live stream frame from persistent LiveStreamTask."""
        if not self._is_live_active or self._is_dragging_roi:
            return

        try:
            self._update_display(frame, meta=meta)
        finally:
            if self.active_live_task is not None:
                self.active_live_task.acknowledge_frame()

    def _on_preview_frame_ready(self, frame: np.ndarray, meta: dict) -> None:
        self._update_display(frame, meta=meta)

    def _on_preview_error(self, err_msg: str) -> None:
        self._append_log(f"[ERROR] Live frame error: {err_msg}")
        self._stop_live()

    def _on_preview_task_finished(self) -> None:
        if self.active_preview_task is not None:
            self.active_preview_task.deleteLater()
            self.active_preview_task = None
        if self._closing:
            self.close()
        elif self._source_switch_pending:
            self._begin_camera_disconnect()

    # =========================================================================
    # Display & Color Scale
    # =========================================================================
    def _set_clim(self, low: int, high: int) -> None:
        """Set color limits programmatically and sync spinbox values.

        Args:
            low:  Lower intensity bound in ADU (0–65534).
            high: Upper intensity bound in ADU (1–65535, must be > low).
        """
        self._clim_low = max(0, low)
        self._clim_high = min(65535, max(low + 1, high))
        # Sync spinboxes without triggering extra redraws
        self.spn_clim_low.blockSignals(True)
        self.spn_clim_high.blockSignals(True)
        self.spn_clim_low.setValue(self._clim_low)
        self.spn_clim_high.setValue(self._clim_high)
        self.spn_clim_low.blockSignals(False)
        self.spn_clim_high.blockSignals(False)
        if self._image_artist is not None:
            self._image_artist.set_clim(self._clim_low, self._clim_high)
            self._colorbar.set_ticks([])
            self._plot_background = None
            self.canvas.draw_idle()

    def _on_clim_changed(self) -> None:
        """Apply color limits immediately whenever spinbox values change."""
        low = self.spn_clim_low.value()
        high = self.spn_clim_high.value()
        if high <= low:
            high = min(65535, low + 1)
            self.spn_clim_high.blockSignals(True)
            self.spn_clim_high.setValue(high)
            self.spn_clim_high.blockSignals(False)
        if low == self._clim_low and high == self._clim_high:
            return
        self._clim_low = low
        self._clim_high = high
        if self._image_artist is not None:
            self._image_artist.set_clim(self._clim_low, self._clim_high)
            self._colorbar.set_ticks([])
            self._plot_background = None
            self.canvas.draw_idle()

    def _on_clim_apply_clicked(self) -> None:
        self._on_clim_changed()

    def _on_clim_auto_clicked(self) -> None:
        """Auto-scale to the last acquired frame's min and max pixel values."""
        if hasattr(self, "_last_frame") and self._last_frame is not None:
            self._set_clim(int(self._last_frame.min()), int(self._last_frame.max()))

    def _on_canvas_button_press(self, event) -> None:
        """Start a lightweight ROI overlay while pausing live-frame delivery."""
        if not self.btn_draw_roi.isChecked() or event.inaxes is not self.axis:
            return
        if event.xdata is None or event.ydata is None or event.button != 1:
            return
        self._roi_drag_start = (float(event.xdata), float(event.ydata))
        self._is_dragging_roi = True
        self._roi_drag_patch = mpatches.Rectangle(
            self._roi_drag_start, 0, 0, linewidth=1.5,
            edgecolor="red", facecolor="none", linestyle="--", zorder=6,
        )
        self._roi_drag_patch.set_animated(True)
        self.axis.add_patch(self._roi_drag_patch)
        self._blit_frame()

    def _on_canvas_motion(self, event) -> None:
        """Redraw only the changing drag outline over the current image."""
        if not self._is_dragging_roi or self._roi_drag_start is None:
            return
        if event.xdata is None or event.ydata is None:
            return
        start_x, start_y = self._roi_drag_start
        end_x, end_y = float(event.xdata), float(event.ydata)
        self._roi_drag_patch.set_bounds(
            min(start_x, end_x), min(start_y, end_y),
            abs(end_x - start_x), abs(end_y - start_y),
        )
        self._blit_frame()

    def _on_canvas_button_release(self, event) -> None:
        """Commit a drawn ROI and resume live acquisition after the overlay clears."""
        start = self._roi_drag_start
        end = (event.xdata, event.ydata) if event is not None else (None, None)
        self._is_dragging_roi = False
        self._roi_drag_start = None
        if self._roi_drag_patch is not None:
            self._roi_drag_patch.remove()
            self._roi_drag_patch = None
        if start is not None and end[0] is not None and end[1] is not None:
            self._on_roi_drawn(start[0], start[1], float(end[0]), float(end[1]))
            self._uncheck_draw_roi()
        else:
            self._blit_frame()
        if self.active_live_task is not None:
            self.active_live_task.acknowledge_frame()

    def _on_canvas_click(self, event) -> None:
        pass

    def _on_canvas_draw(self, event) -> None:
        """Cache the static plot after a full draw, then paint the current frame."""
        if event.canvas is self.canvas:
            self._plot_background = self.canvas.copy_from_bbox(self.axis.bbox)
            self._blit_frame()

    def _blit_frame(self) -> None:
        """Redraw only the image and timestamp over the cached plot background."""
        if self._plot_background is None:
            return
        self.canvas.restore_region(self._plot_background)
        if self._image_artist is not None:
            self.axis.draw_artist(self._image_artist)
        if self._roi_patch is not None:
            self.axis.draw_artist(self._roi_patch)
        if self._roi_drag_patch is not None:
            self.axis.draw_artist(self._roi_drag_patch)
        if self._timestamp_artist is not None:
            self.axis.draw_artist(self._timestamp_artist)
        self.canvas.blit(self.axis.bbox)

    def _update_display(self, frame: np.ndarray, meta: Optional[dict] = None) -> None:
        """Display one sensor frame using bounded downsampling and cached plot blitting."""
        # Never interrupt canvas while the user is actively dragging the ROI rectangle
        if self._is_dragging_roi:
            return

        self._last_frame = frame
        self._frame_count += 1

        # Metrics are intentionally throttled; saturation still checks every pixel.
        if self._frame_count == 1 or self._frame_count % 5 == 0:
            sample = frame[::2, ::2] if frame.size > 500000 else frame
            c_min = int(sample.min())
            c_max = int(frame.max())
            c_mean = float(sample.mean())
            sat_warning = " [WARNING: SENSOR SATURATION DETECTED!]" if c_max >= 65530 else ""
            sat_color = "#dc3545" if sat_warning else "#212529"
            self.lbl_intensity_metrics.setStyleSheet(
                f"font-size: 11px; font-weight: bold; padding: 3px 6px; background: #f8f9fa; "
                f"border: 1px solid #dee2e6; border-radius: 3px; color: {sat_color};"
            )
            self.lbl_intensity_metrics.setText(
                f"Pixel Intensity Metrics | Minimum: {c_min:,} ADU | Maximum: {c_max:,} ADU | Mean: {c_mean:,.1f} ADU{sat_warning}"
            )

        # Downsample to the current image axes so display work tracks the viewer size.
        h, w = frame.shape
        target_height = max(1, int(self.axis.bbox.height))
        target_width = max(1, int(self.axis.bbox.width))
        step_y = max(1, (h + target_height - 1) // target_height)
        step_x = max(1, (w + target_width - 1) // target_width)
        downsample_factor = max(step_y, step_x)
        display_frame = frame[::downsample_factor, ::downsample_factor] if downsample_factor > 1 else frame
        reported_roi = meta.get("roi") if meta else None
        if isinstance(reported_roi, (tuple, list)) and len(reported_roi) == 4:
            x0, y0, x1, y1 = map(int, reported_roi)
        else:
            x0, y0, x1, y1 = self.camera.get_roi()

        full_draw_needed = self._plot_background is None

        if self._image_artist is None:
            self.axis.clear()
            # aspect="auto" ensures the image fills the entire viewport without leaving blank space
            self._image_artist = self.axis.imshow(
                display_frame,
                cmap="viridis",
                origin="upper",
                aspect="auto",
                extent=[x0, x1, y1, y0]
            )
            self._image_artist.set_animated(True)
            color_axis = self.figure.add_axes(COLOR_AXIS_RECT)
            self._colorbar = self.figure.colorbar(self._image_artist, cax=color_axis)
            # The scale endpoints are the adjacent editable spinboxes, not raster labels.
            self._colorbar.set_ticks([])
            self.grp_color_scale.show()
            self.axis.tick_params(axis="both", labelbottom=False, labelleft=False, colors="#263542")

            if self._roi_patch is not None:
                self._roi_patch = None
                self._sync_roi_patch_from_spinboxes()
            self._current_displayed_roi = (x0, y0, x1, y1)
            self.axis.set_aspect("auto")
            self.axis.set_xlim(x0, x1)
            self.axis.set_ylim(y1, y0)
            self._image_artist.set_clim(self._clim_low, self._clim_high)
            full_draw_needed = True
        else:
            self._image_artist.set_data(display_frame)
            if self._current_displayed_roi != (x0, y0, x1, y1):
                self._current_displayed_roi = (x0, y0, x1, y1)
                self._image_artist.set_extent([x0, x1, y1, y0])
                self.axis.set_aspect("auto")
                self.axis.set_xlim(x0, x1)
                self.axis.set_ylim(y1, y0)
                full_draw_needed = True

        # The SDK may omit sensor time; label the source instead of implying it.
        cam_time = meta.get("camera_time_str") if meta else None
        timestamp_source = meta.get("timestamp_source") if meta else None
        if not cam_time:
            cam_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            timestamp_source = "host_fallback"
        source_label = {
            "pco_sdk": "SDK",
            "simulated_host_clock": "Sim",
            "host_fallback": "Host",
        }.get(timestamp_source, "Time")
        display_time = f"{source_label} {cam_time}"

        if self._timestamp_artist is None:
            self._timestamp_artist = self.axis.text(
                0.01, 0.98, display_time,
                transform=self.axis.transAxes,
                fontsize=7, color="#aaaaaa",
                fontweight="normal",
                va="top", ha="left",
                bbox={"facecolor": "black", "alpha": 0.25, "edgecolor": "none", "boxstyle": "round,pad=0.2"}
            )
            self._timestamp_artist.set_animated(True)
        else:
            self._timestamp_artist.set_text(display_time)

        if full_draw_needed:
            self.canvas.draw()
        else:
            self._blit_frame()

    # =========================================================================
    # Hardware ROI Symmetrical Constraints & Dynamic Redraw
    # =========================================================================
    def _sync_roi_patch_from_spinboxes(self) -> None:
        """Redraw the persistent ROI rectangle patch directly from current spinbox values."""
        x0 = self.spn_roi_x0.value()
        x1 = self.spn_roi_x1.value()
        y0 = self.spn_roi_y0.value()
        y1 = self.spn_roi_y1.value()
        self._draw_roi_patch(x0, y0, x1, y1)

    def _on_roi_x0_changed(self) -> None:
        """Ensure minimum span of 64 px (push X End up if needed) and redraw ROI."""
        if self._is_updating_roi:
            return
        self._is_updating_roi = True
        try:
            x0 = self.spn_roi_x0.value()
            x1 = self.spn_roi_x1.value()
            if x1 - x0 < 64:
                x1 = min(2560, x0 + 64)
                if x1 == 2560:
                    x0 = 2560 - 64
                self.spn_roi_x0.setValue(x0)
                self.spn_roi_x1.setValue(x1)
        finally:
            self._is_updating_roi = False
        self._sync_roi_patch_from_spinboxes()

    def _on_roi_x1_changed(self) -> None:
        """Ensure minimum span of 64 px (push X Start down if needed) and redraw ROI."""
        if self._is_updating_roi:
            return
        self._is_updating_roi = True
        try:
            x1 = self.spn_roi_x1.value()
            x0 = self.spn_roi_x0.value()
            if x1 - x0 < 64:
                x0 = max(0, x1 - 64)
                if x0 == 0:
                    x1 = 64
                self.spn_roi_x0.setValue(x0)
                self.spn_roi_x1.setValue(x1)
        finally:
            self._is_updating_roi = False
        self._sync_roi_patch_from_spinboxes()

    def _on_roi_y0_changed(self) -> None:
        """Map either typed Y edge to a centered ROI with at least 16 px height."""
        if self._is_updating_roi:
            return
        self._is_updating_roi = True
        try:
            edge = self.spn_roi_y0.value()
            lower = min(edge, 2160 - edge, 1072)
            self.spn_roi_y0.setValue(lower)
            self.spn_roi_y1.setValue(2160 - lower)
        finally:
            self._is_updating_roi = False
        self._sync_roi_patch_from_spinboxes()

    def _on_roi_y1_changed(self) -> None:
        """Force symmetrical vertical constraint around y=1080 (Y0 = 2160 - Y1) and redraw ROI."""
        if self._is_updating_roi:
            return
        self._is_updating_roi = True
        try:
            val = self.spn_roi_y1.value()
            val = max(1088, min(2160, val))
            self.spn_roi_y0.setValue(2160 - val)
        finally:
            self._is_updating_roi = False
        self._sync_roi_patch_from_spinboxes()

    def _toggle_draw_roi(self, checked: bool) -> None:
        if checked:
            self._append_log("[ROI] Draw mode active: Drag a rectangle on the camera image.")
        else:
            self._on_canvas_button_release(None)

    def _on_roi_drawn(self, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
        """Handle rectangle drawn on image: expand Y symmetrically around 1080 and snap X."""
        x_min, x_max = sorted([start_x, end_x])
        y_min, y_max = sorted([start_y, end_y])

        # Outer bound to 4-pixel steps so no user-drawn area is clipped
        x0 = int(np.floor(x_min / 4.0) * 4)
        x1 = int(np.ceil(x_max / 4.0) * 4)
        x0 = max(0, min(2496, x0))
        x1 = max(x0 + 64, min(2560, x1))

        # Enforce pco.edge 5.5 vertical symmetry centered at 1080 (minimal bounding)
        center_y = 1080
        max_dist = max(abs(center_y - y_min), abs(y_max - center_y))
        max_dist = max(8.0, max_dist)

        y0 = int(round(center_y - max_dist))
        y1 = int(round(center_y + max_dist))
        y0 = max(0, min(1072, y0))
        y1 = min(2160, max(1088, y1))

        self._is_updating_roi = True
        try:
            self.spn_roi_x0.setValue(x0)
            self.spn_roi_x1.setValue(x1)
            self.spn_roi_y0.setValue(y0)
            self.spn_roi_y1.setValue(y1)
        finally:
            self._is_updating_roi = False

        # Display the persistent minimal symmetric ROI outline
        self._draw_roi_patch(x0, y0, x1, y1)

        self._append_log(
            f"[ROI SELECTED] Minimal bounding symmetric ROI: X:[{x0}, {x1}], Y:[{y0}, {y1}] ({x1-x0}x{y1-y0} px).\n"
            f"               Click 'Apply Hardware ROI' to apply to camera sensor."
        )

    def _deactivate_draw_roi(self) -> None:
        """Deactivate draw-ROI mode and uncheck the button."""
        self._is_dragging_roi = False
        if self.active_live_task is not None:
            self.active_live_task.acknowledge_frame()
        self._on_canvas_button_release(None)
        self._uncheck_draw_roi()

    def _uncheck_draw_roi(self) -> None:
        """Leave drawing mode without recursively handling the button toggle."""
        was_blocked = self.btn_draw_roi.blockSignals(True)
        self.btn_draw_roi.setChecked(False)
        self.btn_draw_roi.blockSignals(was_blocked)

    def _draw_roi_patch(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Stamp a persistent thin red outline rectangle onto the axis."""
        width = x1 - x0
        height = y1 - y0
        if self._roi_patch is None:
            self._roi_patch = mpatches.Rectangle(
                (x0, y0), width, height,
                linewidth=1.5, edgecolor="red", facecolor="none",
                linestyle="-", zorder=5, fill=False
            )
            self._roi_patch.set_animated(True)
            self.axis.add_patch(self._roi_patch)
        else:
            self._roi_patch.set_bounds(x0, y0, width, height)
        if self._image_artist is None:
            self.axis.set_xlim(0, 2560)
            self.axis.set_ylim(2160, 0)
            self.axis.set_aspect("auto")
            self.canvas.draw_idle()
        else:
            self._blit_frame()

    def _apply_roi(self) -> None:
        """Queue a valid sensor-pixel ROI change after live acquisition stops."""
        # Snap to valid hardware constraints
        x0 = (self.spn_roi_x0.value() // 4) * 4
        x1 = ((self.spn_roi_x1.value() + 3) // 4) * 4
        x0 = max(0, min(2496, x0))
        x1 = max(x0 + 64, min(2560, x1))

        y0 = self.spn_roi_y0.value()
        y1 = 2160 - y0

        self._is_updating_roi = True
        try:
            self.spn_roi_x0.setValue(x0)
            self.spn_roi_x1.setValue(x1)
            self.spn_roi_y0.setValue(y0)
            self.spn_roi_y1.setValue(y1)
        finally:
            self._is_updating_roi = False

        self._request_roi_change((x0, y0, x1, y1), full_sensor=False)

    def _reset_full_sensor(self) -> None:
        """Queue full-sensor readout while preserving the configured ROI controls."""
        self._request_roi_change((0, 0, 2560, 2160), full_sensor=True)

    def _request_roi_change(self, roi: tuple[int, int, int, int], *, full_sensor: bool) -> None:
        """Serialize ROI changes with live acquisition and reject duplicate clicks."""
        if self._roi_change_pending:
            return
        if self.active_trigger_task is not None:
            self._append_log("[ROI] Wait for the trigger mode change to finish.")
            return
        if not self.camera.is_connected:
            self._append_log("[ROI] Connect the selected camera with GO before changing sensor ROI.")
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._append_log("[ROI] Cannot change sensor ROI during a measurement scan.")
            return

        self._roi_change_pending = True
        self._roi_change_full_sensor = full_sensor
        self._resume_live_after_roi = self._is_live_active
        self.btn_apply_roi.setEnabled(False)
        self.btn_full_sensor.setEnabled(False)
        self.btn_draw_roi.setEnabled(False)
        self.cmb_camera_source.setEnabled(False)
        if self.active_live_task is not None:
            self._stop_live(after_stop=lambda: self._start_roi_task(roi))
        else:
            self._start_roi_task(roi)

    def _start_roi_task(self, roi: tuple[int, int, int, int]) -> None:
        """Apply a ROI in a worker after the live camera buffer is idle."""
        if self._closing:
            self._roi_change_pending = False
            self.close()
            return
        self.lbl_system_status.setText("Status: Changing sensor ROI...")
        self.active_roi_task = CameraRoiTask(self.scan_manager, roi)
        self.active_roi_task.roi_applied.connect(self._on_roi_task_applied)
        self.active_roi_task.error_occurred.connect(self._on_roi_task_error)
        self.active_roi_task.finished.connect(self._on_roi_task_finished)
        self.active_roi_task.start()

    def _on_roi_task_applied(self, roi: tuple[int, int, int, int]) -> None:
        """Update the display outline after the camera confirms the new ROI."""
        self._current_displayed_roi = None
        if self._roi_change_full_sensor:
            self._append_log(
                "[FULL SENSOR] Switched readout to full 2560x2160. "
                "Configured ROI preserved; Apply ROI reactivates it."
            )
            self._sync_roi_patch_from_spinboxes()
        else:
            x0, y0, x1, y1 = roi
            self._append_log(
                f"[ROI APPLIED] Sensor readout set to X:[{x0}, {x1}], "
                f"Y:[{y0}, {y1}] ({x1-x0}x{y1-y0} px)."
            )
            self._deactivate_draw_roi()
            self._draw_roi_patch(x0, y0, x1, y1)
            self._clear_paused_scan("Hardware ROI modified")
        self.lbl_system_status.setText("Status: Sensor ROI updated")

    def _on_roi_task_error(self, error: str) -> None:
        """Report a rejected ROI while leaving the camera's prior setting intact."""
        self._append_log(f"[ROI ERROR] {error}")
        self.lbl_system_status.setText("Status: Sensor ROI change failed")
        QMessageBox.warning(self, "Sensor ROI Change Failed", error)

    def _on_roi_task_finished(self) -> None:
        """Restore controls and preview only after the ROI worker has exited."""
        task = self.active_roi_task
        self.active_roi_task = None
        if task is not None:
            task.deleteLater()
        self._roi_change_pending = False
        self.btn_apply_roi.setEnabled(True)
        self.btn_full_sensor.setEnabled(True)
        self.btn_draw_roi.setEnabled(True)
        resume_live = self._resume_live_after_roi
        self._resume_live_after_roi = False
        if self._closing:
            self.close()
        elif resume_live:
            self._start_live()
        else:
            self._capture_single_preview()
        if not self._is_live_active:
            self.cmb_camera_source.setEnabled(True)

    # =========================================================================
    # Two-Way Automatic Calculation & Display Sync for Experiment Parameters
    # =========================================================================
    def _on_start_val_changed(self) -> None:
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            step_size = self.spn_step_size.value()
            num_steps = self.spn_num_steps.value()
            self.spn_end_val.setValue(start + (num_steps - 1) * step_size)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _on_step_size_changed(self) -> None:
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            step_size = self.spn_step_size.value()
            num_steps = self.spn_num_steps.value()
            self.spn_end_val.setValue(start + (num_steps - 1) * step_size)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _on_num_steps_changed(self) -> None:
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            step_size = self.spn_step_size.value()
            num_steps = self.spn_num_steps.value()
            self.spn_end_val.setValue(start + (num_steps - 1) * step_size)
            self.spn_step_size.setEnabled(num_steps > 1)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _on_end_val_changed(self) -> None:
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            end_val = self.spn_end_val.value()
            num_steps = self.spn_num_steps.value()
            if num_steps == 1:
                # Either endpoint edits the one setpoint; spacing is unused.
                self.spn_start_val.setValue(end_val)
            else:
                self.spn_step_size.setValue((end_val - start) / (num_steps - 1))
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _clear_paused_scan(self, reason: str = "") -> None:
        if self._paused_step is not None:
            self._paused_step = None
            self._paused_run_id = None
            self._current_run_id = None
            self._scan_active_elapsed_s = 0.0
            self._scan_segment_started_at = None
            self.btn_cut_measurement.setVisible(False)
            self.btn_take_measurement.setText("Take Measurement")
            if reason:
                self._append_log(f"[SCAN RESET] {reason}. Next scan starts from Step 1.")

    @staticmethod
    def _step_count_label(count: int) -> str:
        """Format a scan-point count for status text."""
        return f"{count} {'step' if count == 1 else 'steps'}"

    def _update_progress_display(self) -> None:
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            return
        if self._paused_step is not None:
            self._clear_paused_scan(reason="Parameters modified")
        start = self.spn_start_val.value()
        total_steps = self.spn_num_steps.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {self._step_count_label(total_steps)}) | {param_name}: {start:.4f}"
        )

    def _on_exposure_changed(self, value_ms: float) -> None:
        if self.camera.is_connected:
            try:
                self.camera.set_exposure_time(value_ms / 1000.0)
                self._append_log(f"[EXPOSURE] Set to {value_ms:.2f} ms ({value_ms / 1000.0:.6f} s)")
            except Exception as exc:
                QMessageBox.warning(self, "Safety Limit Violation", str(exc))

    # =========================================================================
    # Storage Directory Handlers
    # =========================================================================
    def _on_storage_dir_edited(self) -> None:
        folder = Path(self.txt_storage_dir.text().strip()).expanduser()
        self.scan_manager.set_storage_dir(folder)

    def _browse_directory(self) -> None:
        chosen_dir = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder for HDF5 Datasets", self.txt_storage_dir.text()
        )
        if chosen_dir:
            self.txt_storage_dir.setText(chosen_dir)
            self.scan_manager.set_storage_dir(chosen_dir)
            self._append_log(f"[STORAGE] Destination folder set to: {chosen_dir}")

    def _open_storage_folder(self) -> None:
        folder = Path(self.txt_storage_dir.text().strip()).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as err:
            QMessageBox.warning(self, "Unable to Open Folder", f"Could not open directory:\n{err}")

    # =========================================================================
    # Automated Scan Execution & Pause / Resume Coordination
    # =========================================================================
    def _scan_source_label(self) -> str:
        """Label scan logs by the selected physical or simulated camera source."""
        if isinstance(self.camera, MockPcoCamera):
            return "SIMULATED"
        if self._connected_source == "physical":
            return "PHYSICAL"
        return "CAMERA"

    def _toggle_measurement_scan(self) -> None:
        """Start scan, stop in-progress scan, or continue paused scan."""
        if not self.camera.is_connected:
            self._connect_camera(after_connect=self._toggle_measurement_scan)
            return

        # If scan is already running, this button functions as Stop
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._on_stop_clicked()
            return

        if self.active_trigger_task is not None or self._trigger_change_pending:
            self._append_log("[SCAN] Wait for the trigger mode change to finish.")
            return

        if self._roi_change_pending:
            self._append_log("[SCAN] Wait for the hardware ROI change to finish before measuring.")
            self._resume_live_if_scan_not_started()
            return

        # Stop live stream cleanly before starting multi-step scan
        if self._is_live_active:
            self._resume_live_after_scan = True
            self._stop_live(after_stop=self._toggle_measurement_scan)
            return

        selected_roi = (
            self.spn_roi_x0.value(), self.spn_roi_y0.value(),
            self.spn_roi_x1.value(), self.spn_roi_y1.value(),
        )
        try:
            active_roi = tuple(self.scan_manager.get_roi())
        except Exception as exc:
            message = f"Cannot verify the camera's active ROI: {exc}"
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "ROI Readback Failed", message)
            self._resume_live_if_scan_not_started()
            return
        if selected_roi != active_roi:
            message = (
                f"ROI controls show {selected_roi}, but the camera is using {active_roi}. "
                "Click Apply ROI before measuring, or set the controls to the active full sensor."
            )
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "ROI Not Applied", message)
            self._resume_live_if_scan_not_started()
            return

        requested_trigger = (
            TriggerMode.EXTERNAL_EXPOSURE_START
            if self.chk_external_trigger.isChecked() else TriggerMode.AUTO_SEQUENCE
        )
        try:
            actual_trigger = self.scan_manager.get_trigger_mode()
        except Exception as exc:
            message = f"Cannot verify the camera's active trigger mode: {exc}"
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "Trigger Readback Failed", message)
            self._resume_live_if_scan_not_started()
            return
        if actual_trigger != requested_trigger:
            message = (
                f"Trigger checkbox requests {requested_trigger.value}, but camera reports "
                f"{actual_trigger.value}. Reapply the trigger selection before measuring."
            )
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "Trigger Mode Mismatch", message)
            self._resume_live_if_scan_not_started()
            return

        start_step = self._paused_step if self._paused_step is not None else 0
        if start_step == 0:
            self._current_run_id = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
            self._scan_active_elapsed_s = 0.0
        else:
            self._current_run_id = self._paused_run_id

        target_dir = Path(self.txt_storage_dir.text().strip()).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        remaining_steps = self.spn_num_steps.value() - start_step
        raw_bytes = (
            (active_roi[2] - active_roi[0]) * (active_roi[3] - active_roi[1])
            * 2 * self.spn_frames.value() * remaining_steps
        )
        required_bytes = int(raw_bytes * 1.1) + 100_000_000
        try:
            free_bytes = shutil.disk_usage(target_dir).free
        except OSError as exc:
            message = f"Cannot check free space in {target_dir}: {exc}"
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "Storage Check Failed", message)
            self._resume_live_if_scan_not_started()
            return
        if required_bytes > free_bytes:
            message = (
                f"This measurement needs about {required_bytes / 1024**3:.1f} GiB "
                f"for {remaining_steps} remaining steps; only {free_bytes / 1024**3:.1f} GiB "
                "is free. Reduce ROI, frames, or steps, or choose a drive with more space."
            )
            self._append_log(f"[SCAN BLOCKED] {message}")
            QMessageBox.warning(self, "Insufficient Storage", message)
            self._resume_live_if_scan_not_started()
            return
        self.scan_manager.set_storage_dir(target_dir)

        self._scan_completed_successfully = False
        self._set_ui_scanning_state(is_scanning=True)

        exp_name = self.txt_file_header.text().strip() or "HHG_Scan"
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        start_val = self.spn_start_val.value()
        step_size = self.spn_step_size.value()
        num_steps = self.spn_num_steps.value()
        num_frames = self.spn_frames.value()
        curr_val = start_val + start_step * step_size

        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {start_step + 1} of {num_steps} (Acquiring...) | {param_name}: {curr_val:.4f}"
        )
        self.lbl_system_status.setText(
            f"Status: Scan in Progress [Step {start_step + 1} of {num_steps}]..."
        )
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #0d6efd; padding-left: 8px;")

        if start_step > 0:
            self._append_log(
                f"[SCAN RESUME] Continuing scan from Step {start_step} (Step {start_step + 1} of {num_steps}) "
                f"at {param_name} = {curr_val:.4f}."
            )
        else:
            self._append_log(
                f"[SCAN START][{self._scan_source_label()}] Initiating scan: "
                f"{self._step_count_label(num_steps)} from {start_val:.4f} to "
                f"{self.spn_end_val.value():.4f} | Param: '{param_name}' | Frames/step: {num_frames}"
            )

        self.active_scan_task = ScanSequenceTask(
            scan_mgr=self.scan_manager,
            exp_name=exp_name,
            param_name=param_name,
            start_val=start_val,
            step_size=step_size,
            num_steps=num_steps,
            num_frames=num_frames,
            start_step=start_step,
            run_id=self._current_run_id,
        )
        self.active_scan_task.step_started.connect(self._on_scan_step_started)
        self.active_scan_task.step_completed.connect(self._on_scan_step_completed)
        self.active_scan_task.scan_finished.connect(self._on_scan_finished)
        self.active_scan_task.scan_aborted.connect(self._on_scan_aborted)
        self.active_scan_task.error_occurred.connect(self._on_scan_error)
        self.active_scan_task.finished.connect(self._on_scan_task_finished)
        self._scan_segment_started_at = perf_counter()
        self.active_scan_task.start()

    def _resume_live_if_scan_not_started(self) -> None:
        """Restore an interrupted live view when scan preflight blocks acquisition."""
        if self._resume_live_after_scan and not self._closing:
            self._resume_live_after_scan = False
            self._start_live()

    def _finish_scan_segment(self) -> float:
        """Return active scan seconds, excluding time spent paused between segments."""
        if self._scan_segment_started_at is not None:
            self._scan_active_elapsed_s += max(0.0, perf_counter() - self._scan_segment_started_at)
            self._scan_segment_started_at = None
        return self._scan_active_elapsed_s

    def _set_ui_scanning_state(self, is_scanning: bool) -> None:
        if is_scanning:
            self.btn_take_measurement.setText("Stop Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #dc3545; color: white; padding: 8px;"
            )
            self.btn_cut_measurement.setVisible(False)
            self.btn_go.setEnabled(False)
            self.spn_exposure.setEnabled(False)
            self.chk_external_trigger.setEnabled(False)
            self.spn_frames.setEnabled(False)
            self.spn_start_val.setEnabled(False)
            self.spn_end_val.setEnabled(False)
            self.spn_step_size.setEnabled(False)
            self.spn_num_steps.setEnabled(False)
            self.btn_apply_roi.setEnabled(False)
            self.btn_full_sensor.setEnabled(False)
            self.btn_draw_roi.setEnabled(False)
            self.cmb_camera_source.setEnabled(False)
        else:
            self.btn_go.setEnabled(True)
            self.spn_exposure.setEnabled(True)
            self.chk_external_trigger.setEnabled(self.camera.is_connected and self.active_trigger_task is None)
            self.spn_frames.setEnabled(True)
            self.spn_start_val.setEnabled(True)
            self.spn_end_val.setEnabled(True)
            self.spn_step_size.setEnabled(self.spn_num_steps.value() > 1)
            self.spn_num_steps.setEnabled(True)
            self.btn_apply_roi.setEnabled(not self._roi_change_pending)
            self.btn_full_sensor.setEnabled(not self._roi_change_pending)
            self.btn_draw_roi.setEnabled(not self._roi_change_pending)
            self.cmb_camera_source.setEnabled(
                not self._roi_change_pending and not self._is_live_active
                and self.active_mode_task is None and not self._source_switch_pending
            )

    def _on_scan_step_started(
        self,
        step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str
    ) -> None:
        self._current_executing_step = step_idx
        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {step_idx + 1} of {total_steps} (Acquiring...) | {param_name}: {param_val:.4f}"
        )
        self.lbl_system_status.setText(
            f"Status: Scan in Progress [Step {step_idx + 1} of {total_steps}]..."
        )

    def _on_scan_step_completed(
        self,
        step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str,
        latest_frame: np.ndarray,
        h5_path: Path
    ) -> None:
        file_size_mb = h5_path.stat().st_size / (1024 * 1024)
        self._append_log(
            f"[SAVED][{self._scan_source_label()}] Step {step_idx + 1}/{total_steps} -> "
            f"{h5_path.name} ({file_size_mb:.2f} MB, {param_name}={param_val:.4f})"
        )
        self._update_display(latest_frame)
        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {step_idx + 1} of {total_steps} (Saved) | {param_name}: {param_val:.4f}"
        )

    def _on_scan_finished(self, total_steps: int) -> None:
        self._scan_completed_successfully = True
        elapsed_s = self._finish_scan_segment()
        self._paused_step = None
        self._paused_run_id = None
        self._current_run_id = None
        end_val = self.spn_end_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Completed all {self._step_count_label(total_steps)} | {param_name}: {end_val:.4f}"
        )
        self.lbl_system_status.setText(f"Status: Scan Completed Successfully ({self._step_count_label(total_steps)} saved)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
        self._append_log(
            f"[SCAN COMPLETE][{self._scan_source_label()}] All {self._step_count_label(total_steps)} saved to disk. "
            f"Measurement duration: {elapsed_s:.2f} s."
        )
        self._scan_active_elapsed_s = 0.0

    def _on_scan_aborted(
        self,
        stopped_step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str
    ) -> None:
        self._scan_completed_successfully = False
        elapsed_s = self._finish_scan_segment()
        self._paused_step = stopped_step_idx
        self._paused_run_id = self._current_run_id
        self.lbl_scan_progress.setText(
            f"Scan Progress: Stopped at Step {stopped_step_idx + 1} of {total_steps} | {param_name}: {param_val:.4f}"
        )
        self.lbl_system_status.setText(f"Status: Scan Stopped at Step {stopped_step_idx + 1} of {total_steps}")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        self._append_log(
            f"[SCAN STOPPED] Stopped at Step {stopped_step_idx + 1} of {total_steps}. "
            f"Active measurement duration: {elapsed_s:.2f} s.\n"
            f"              Click 'Continue from Step {stopped_step_idx + 1}' or 'Cut Measurement'."
        )

    def _cut_measurement(self) -> None:
        stopped = self._paused_step
        self._paused_step = None
        self._paused_run_id = None
        self._current_run_id = None
        self._scan_active_elapsed_s = 0.0
        self._scan_segment_started_at = None
        self.btn_cut_measurement.setVisible(False)
        self.btn_take_measurement.setText("Take Measurement")
        self.btn_take_measurement.setStyleSheet(
            "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
        )
        total_steps = self.spn_num_steps.value()
        start_val = self.spn_start_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {self._step_count_label(total_steps)}) | {param_name}: {start_val:.4f}"
        )
        self.lbl_system_status.setText("Status: Idle / Ready")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
        self._append_log(f"[SCAN FINALIZED] Scan cut at Step {stopped}. Next scan will start from Step 1.")

    def _on_scan_error(self, err_msg: str) -> None:
        self._scan_completed_successfully = False
        elapsed_s = self._finish_scan_segment()
        self._append_log(
            f"[ERROR] Scan execution error after {elapsed_s:.2f} s active measurement: {err_msg}"
        )
        self.lbl_system_status.setText("Status: Scan Error")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #dc3545; padding-left: 8px;")
        QMessageBox.warning(self, "Scan Execution Error", f"Scan error occurred:\n\n{err_msg}")

    def _on_scan_task_finished(self) -> None:
        if self.active_scan_task is not None:
            self.active_scan_task.deleteLater()
            self.active_scan_task = None

        if self._paused_step is not None:
            self.btn_take_measurement.setText(f"Continue from Step {self._paused_step + 1}")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setEnabled(True)
            self.btn_cut_measurement.setVisible(True)
            self.btn_cut_measurement.setEnabled(True)
        else:
            self.btn_take_measurement.setText("Take Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setEnabled(True)
            self.btn_cut_measurement.setVisible(False)

        self._set_ui_scanning_state(is_scanning=False)

        # If user closed window while scan was aborting, finish close now
        if self._closing:
            self.close()
        elif self._resume_live_after_scan and self._scan_completed_successfully:
            self._resume_live_after_scan = False
            self._append_log("[LIVE] Resuming live view after measurement.")
            self._start_live()
        else:
            self._resume_live_after_scan = False
        self._scan_completed_successfully = False

    def _append_log(self, message: str) -> None:
        self.txt_activity_log.append(message)

    def closeEvent(self, event) -> None:
        """Cooperatively stop workers before disconnecting the camera."""
        self._is_live_active = False
        self._resume_live_after_scan = False
        self._after_live_stopped = None

        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._closing = True
            self.active_scan_task.request_abort()
            event.ignore()
            return

        if self.active_live_task is not None and self.active_live_task.isRunning():
            self._closing = True
            self._stop_live()
            event.ignore()
            return

        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            self._closing = True
            event.ignore()
            return

        if self.active_connect_task is not None and self.active_connect_task.isRunning():
            self._closing = True
            event.ignore()
            return

        if self.active_mode_task is not None and self.active_mode_task.isRunning():
            self._closing = True
            event.ignore()
            return

        if self.active_trigger_task is not None and self.active_trigger_task.isRunning():
            self._closing = True
            event.ignore()
            return

        if self.active_roi_task is not None and self.active_roi_task.isRunning():
            self._closing = True
            event.ignore()
            return

        if self.active_disconnect_task is not None and self.active_disconnect_task.isRunning():
            self._closing = True
            event.ignore()
            return

        try:
            if self.camera.is_connected:
                self.camera.close()
                self._append_log("[CLOSE] Camera disconnected cleanly.")
        except Exception as exc:
            self._append_log(f"[CLOSE] Warning during camera disconnect: {exc}")

        # A queued Matplotlib draw_idle callback can otherwise try to repaint
        # this canvas after Qt deletes its native widget.
        self.canvas._draw_pending = False
        event.accept()


# Re-export CameraPanel alias for backward compatibility
CameraPanel = CameraMainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = CameraMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
