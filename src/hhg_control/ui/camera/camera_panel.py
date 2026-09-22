"""
PyQt6 Graphical User Interface for pco.edge 5.5 sCMOS Camera Control and Scan Sequencer.
Implements top-left Go/Stop controls, horizontal widescreen layout with side control panel,
manual color scale controls, camera-derived timestamps, and hardware-constrained ROI.
"""

import sys
import os
import time
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime
import numpy as np

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox,
    QPushButton, QFileDialog, QTextEdit, QMessageBox,
    QGridLayout, QFrame, QSizePolicy, QComboBox
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector
import matplotlib.patches as mpatches

from hhg_control.drivers.base_camera import BaseCamera, ReadoutMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


class PreviewTask(QThread):
    """
    Background worker thread for capturing a single live preview frame without writing to disk.
    Keeps the GUI responsive during exposure.
    """
    frame_ready = pyqtSignal(np.ndarray, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager) -> None:
        super().__init__()
        self.scan_manager = scan_manager

    def run(self) -> None:
        try:
            frame, meta = self.scan_manager.acquire_preview()
            self.frame_ready.emit(frame, meta)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class LiveStreamTask(QThread):
    """
    Persistent background worker thread for continuous live camera view.
    Eliminates OS thread creation/destruction churn by running a steady acquisition loop.
    """
    frame_ready = pyqtSignal(np.ndarray, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, target_fps: float = 20.0) -> None:
        super().__init__()
        self.scan_manager = scan_manager
        self.target_fps = target_fps
        self._running = False
        self.gui_ready = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        min_interval = 1.0 / self.target_fps
        while self._running:
            t0 = time.perf_counter()
            try:
                # Flow control: do not acquire if GUI is still displaying the previous frame
                if not self.gui_ready:
                    time.sleep(0.005)
                    continue

                frame, meta = self.scan_manager.acquire_preview()
                if self._running and self.gui_ready:
                    self.gui_ready = False
                    self.frame_ready.emit(frame, meta)
            except Exception as exc:
                if self._running:
                    self.error_occurred.emit(str(exc))
                break

            elapsed = time.perf_counter() - t0
            sleep_time = min_interval - elapsed
            if sleep_time > 0 and self._running:
                time.sleep(sleep_time)


class ScanSequenceTask(QThread):
    """
    Dedicated background worker thread for executing a full multi-step scan sequence.
    Iterates through planned scan steps, saving HDF5 datasets at each step.
    Supports starting from any step index (to continue paused scans), immediate step start
    notifications, and graceful abort requests without corrupting files or hardware state.
    """
    step_started = pyqtSignal(int, int, float, str)  # (step_idx, total_steps, param_val, param_name)
    step_completed = pyqtSignal(int, int, float, str, np.ndarray, Path)
    scan_finished = pyqtSignal(int)
    scan_aborted = pyqtSignal(int, int, float, str)  # (stopped_step_idx, total_steps, param_val, param_name)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        scan_mgr: CameraScanManager,
        exp_name: str,
        param_name: str,
        start_val: float,
        step_size: float,
        num_steps: int,
        num_frames: int,
        start_step: int = 0,
    ) -> None:
        super().__init__()
        self.scan_mgr = scan_mgr
        self.exp_name = exp_name
        self.param_name = param_name
        self.start_val = start_val
        self.step_size = step_size
        self.num_steps = num_steps
        self.num_frames = num_frames
        self.start_step = start_step
        self._abort_requested = False
        self._current_step = start_step

    def request_abort(self) -> None:
        """Signal the scan loop to stop gracefully after completing the current step."""
        self._abort_requested = True

    @property
    def is_abort_requested(self) -> bool:
        return self._abort_requested

    def run(self) -> None:
        try:
            def on_start(step: int, val: float) -> None:
                self._current_step = step
                self.step_started.emit(step, self.num_steps, val, self.param_name)

            for step, val, filepath, latest_frame in self.scan_mgr.execute_scan(
                experiment_name=self.exp_name,
                param_name=self.param_name,
                start_value=self.start_val,
                step_size=self.step_size,
                num_steps=self.num_steps,
                num_frames=self.num_frames,
                start_step=self.start_step,
                on_step_start=on_start,
                abort_check=lambda: self._abort_requested,
            ):
                self.step_completed.emit(
                    step, self.num_steps, val, self.param_name, latest_frame, filepath
                )
                if self._abort_requested:
                    self.scan_aborted.emit(step, self.num_steps, val, self.param_name)
                    return

            if self._abort_requested:
                val = self.start_val + self._current_step * self.step_size
                self.scan_aborted.emit(self._current_step, self.num_steps, val, self.param_name)
            else:
                self.scan_finished.emit(self.num_steps)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CameraMainWindow(QMainWindow):
    """Primary Application Window for HHG Laboratory Camera Control."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HHG Attosecond Lab - pco.edge 5.5 Camera Controller & Sequencer")
        self.resize(920, 560)
        self.resize(1040, 580)

        # Instrumentation layer
        self.camera: BaseCamera = MockPcoCamera()
        default_storage = Path.cwd() / "data"
        default_storage.mkdir(parents=True, exist_ok=True)
        self.scan_manager = CameraScanManager(camera=self.camera, storage_dir=default_storage)

        # Worker tasks
        self.active_scan_task: Optional[ScanSequenceTask] = None
        self.active_preview_task: Optional[PreviewTask] = None
        self.active_live_task: Optional[LiveStreamTask] = None

        # State tracking
        self._is_live_active: bool = False
        self._is_rendering: bool = False
        self._is_updating_range: bool = False
        self._is_updating_roi: bool = False
        self._paused_step: Optional[int] = None
        self._current_executing_step: int = 0
        self._frame_count: int = 0
        self._closing: bool = False

        # Matplotlib display caches
        self._image_artist = None
        self._colorbar = None
        self._timestamp_artist = None
        self._roi_selector: Optional[RectangleSelector] = None
        self._roi_patch = None           # persistent Rectangle patch drawn when ROI is applied or edited
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

        # Top bar: GO / STOP / Status + Color Scale
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

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
        self.lbl_system_status.setStyleSheet(
            "font-weight: bold; font-size: 11px; color: #198754; padding-left: 2px;"
        )
        top_bar.addWidget(self.lbl_system_status)
        top_bar.addStretch()

        # Compact Color Scale docked to top right of image — only applies when pressing Enter
        lbl_clim = QLabel("Scale:")
        lbl_clim.setStyleSheet("font-size: 11px; font-weight: bold; color: #495057;")
        top_bar.addWidget(lbl_clim)

        lbl_min = QLabel("Min")
        lbl_min.setStyleSheet("font-size: 10px; color: #6c757d;")
        top_bar.addWidget(lbl_min)

        self.spn_clim_low = QSpinBox()
        self.spn_clim_low.setRange(0, 65534)
        self.spn_clim_low.setValue(0)
        self.spn_clim_low.setFixedWidth(64)
        self.spn_clim_low.setStyleSheet("font-size: 11px; padding: 1px 2px;")
        self.spn_clim_low.setToolTip("Min ADU. Type value and press Enter to apply.")
        self.spn_clim_low.setKeyboardTracking(False)
        self.spn_clim_low.valueChanged.connect(self._on_clim_changed)
        self.spn_clim_low.editingFinished.connect(self._on_clim_changed)
        top_bar.addWidget(self.spn_clim_low)

        lbl_max = QLabel("Max")
        lbl_max.setStyleSheet("font-size: 10px; color: #6c757d;")
        top_bar.addWidget(lbl_max)

        self.spn_clim_high = QSpinBox()
        self.spn_clim_high.setRange(1, 65535)
        self.spn_clim_high.setValue(65535)
        self.spn_clim_high.setFixedWidth(64)
        self.spn_clim_high.setStyleSheet("font-size: 11px; padding: 1px 2px;")
        self.spn_clim_high.setToolTip("Max ADU. Type value and press Enter to apply.")
        self.spn_clim_high.setKeyboardTracking(False)
        self.spn_clim_high.valueChanged.connect(self._on_clim_changed)
        self.spn_clim_high.editingFinished.connect(self._on_clim_changed)
        top_bar.addWidget(self.spn_clim_high)

        btn_auto_clim = QPushButton("Auto")
        btn_auto_clim.setFixedWidth(38)
        btn_auto_clim.setStyleSheet("padding: 2px 4px; font-size: 10px; font-weight: bold;")
        btn_auto_clim.setToolTip("Auto-scale color limits to current frame min/max")
        btn_auto_clim.clicked.connect(self._on_clim_auto_clicked)
        top_bar.addWidget(btn_auto_clim)

        left_pane.addLayout(top_bar)

        # Canvas — enlarged (550x450 px), tightly cropped margins
        self.figure = Figure(dpi=100)
        self.figure.subplots_adjust(left=0.07, right=0.90, top=0.97, bottom=0.07)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setFixedSize(550, 450)
        self.axis = self.figure.add_subplot(111)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_button_press)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_button_release)
        left_pane.addWidget(self.canvas)

        # Intensity metrics strip below canvas — matched to image width (550 px)
        self.lbl_intensity_metrics = QLabel(
            "Pixel Intensity Metrics | Minimum: -- ADU | Maximum: -- ADU | Mean: -- ADU"
        )
        self.lbl_intensity_metrics.setFixedWidth(550)
        self.lbl_intensity_metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_intensity_metrics.setStyleSheet(
            "font-size: 11px; font-weight: bold; padding: 3px 6px; background: #f8f9fa; "
            "border: 1px solid #dee2e6; border-radius: 3px; color: #212529;"
        )
        left_pane.addWidget(self.lbl_intensity_metrics)
        left_pane.addStretch(1)

        root_layout.addLayout(left_pane, stretch=0)

        # =========================================================================
        # Right Pane: Vertical control panel (Compact, clean spacing)
        # =========================================================================
        right_pane = QVBoxLayout()
        right_pane.setSpacing(6)
        right_pane.setContentsMargins(0, 0, 0, 0)

        # --- Group 1: Camera & Hardware ROI --------------------------------
        grp_camera = QGroupBox("Camera & Hardware ROI")
        lay_cam = QGridLayout(grp_camera)
        lay_cam.setSpacing(4)
        lay_cam.setContentsMargins(6, 8, 6, 6)

        lay_cam.addWidget(QLabel("Exposure (ms):"), 0, 0)
        self.spn_exposure = QDoubleSpinBox()
        self.spn_exposure.setRange(0.5, 10000.0)
        self.spn_exposure.setValue(10.0)
        self.spn_exposure.setDecimals(2)
        self.spn_exposure.valueChanged.connect(self._on_exposure_changed)
        lay_cam.addWidget(self.spn_exposure, 0, 1)

        lay_cam.addWidget(QLabel("Frames/Step:"), 0, 2)
        self.spn_frames = QSpinBox()
        self.spn_frames.setRange(1, 1000)
        self.spn_frames.setValue(5)
        lay_cam.addWidget(self.spn_frames, 0, 3)

        # Readout mode selector (row 1)
        lay_cam.addWidget(QLabel("Readout Mode:"), 1, 0)
        self.cmb_readout_mode = QComboBox()
        self.cmb_readout_mode.addItem("Rolling Shutter", userData=ReadoutMode.ROLLING_SHUTTER)
        self.cmb_readout_mode.addItem("Global Reset (HHG)", userData=ReadoutMode.GLOBAL_RESET)
        self.cmb_readout_mode.setCurrentIndex(0)
        self.cmb_readout_mode.setToolTip(
            "Rolling Shutter: rows exposed sequentially.\n\n"
            "Global Reset: all rows start exposure simultaneously — recommended for HHG.\n"
            "Switching triggers camera reboot (~5 s)."
        )
        self.cmb_readout_mode.currentIndexChanged.connect(self._on_readout_mode_changed)
        lay_cam.addWidget(self.cmb_readout_mode, 1, 1, 1, 3)

        # ROI spinboxes (rows 2 & 3) — value changes immediately redraw ROI rectangle
        lay_cam.addWidget(QLabel("X:"), 2, 0)
        self.spn_roi_x0 = QSpinBox()
        self.spn_roi_x0.setRange(0, 2496)
        self.spn_roi_x0.setSingleStep(4)
        self.spn_roi_x0.setValue(0)
        self.spn_roi_x0.setToolTip("ROI X Start (0–2496, 4-px steps)")
        self.spn_roi_x0.valueChanged.connect(self._on_roi_x0_changed)
        lay_cam.addWidget(self.spn_roi_x0, 2, 1)

        self.spn_roi_x1 = QSpinBox()
        self.spn_roi_x1.setRange(64, 2560)
        self.spn_roi_x1.setSingleStep(4)
        self.spn_roi_x1.setValue(2560)
        self.spn_roi_x1.setToolTip("ROI X End (64–2560, 4-px steps)")
        self.spn_roi_x1.valueChanged.connect(self._on_roi_x1_changed)
        lay_cam.addWidget(self.spn_roi_x1, 2, 2, 1, 2)

        lay_cam.addWidget(QLabel("Y (sym):"), 3, 0)
        self.spn_roi_y0 = QSpinBox()
        self.spn_roi_y0.setRange(0, 1072)
        self.spn_roi_y0.setValue(0)
        self.spn_roi_y0.setToolTip("ROI Y Start — mirrored around sensor centre Y=1080")
        self.spn_roi_y0.valueChanged.connect(self._on_roi_y0_changed)
        lay_cam.addWidget(self.spn_roi_y0, 3, 1)

        self.spn_roi_y1 = QSpinBox()
        self.spn_roi_y1.setRange(1088, 2160)
        self.spn_roi_y1.setValue(2160)
        self.spn_roi_y1.setToolTip("ROI Y End — auto-set symmetrically")
        self.spn_roi_y1.valueChanged.connect(self._on_roi_y1_changed)
        lay_cam.addWidget(self.spn_roi_y1, 3, 2, 1, 2)

        lbl_roi_hint = QLabel("Y centered on 1080 (pco.edge). X in 4-px steps.")
        lbl_roi_hint.setStyleSheet("font-size: 9px; color: #6c757d; font-style: italic;")
        lay_cam.addWidget(lbl_roi_hint, 4, 0, 1, 4)

        roi_btn_row = QHBoxLayout()
        roi_btn_row.setSpacing(4)
        self.btn_apply_roi = QPushButton("Apply ROI")
        self.btn_apply_roi.setStyleSheet("padding: 5px; font-weight: bold;")
        self.btn_apply_roi.clicked.connect(self._apply_roi)
        roi_btn_row.addWidget(self.btn_apply_roi)

        self.btn_full_sensor = QPushButton("Full Sensor")
        self.btn_full_sensor.setStyleSheet("padding: 5px;")
        self.btn_full_sensor.clicked.connect(self._reset_full_sensor)
        roi_btn_row.addWidget(self.btn_full_sensor)

        self.btn_draw_roi = QPushButton("Draw ROI")
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

        lay_exp.addWidget(QLabel("Start:"), 0, 0)
        self.spn_start_val = QDoubleSpinBox()
        self.spn_start_val.setRange(-1e6, 1e6)
        self.spn_start_val.setDecimals(4)
        self.spn_start_val.setValue(0.0)
        self.spn_start_val.valueChanged.connect(self._on_start_val_changed)
        lay_exp.addWidget(self.spn_start_val, 0, 1)

        lay_exp.addWidget(QLabel("End:"), 0, 2)
        self.spn_end_val = QDoubleSpinBox()
        self.spn_end_val.setRange(-1e6, 1e6)
        self.spn_end_val.setDecimals(4)
        self.spn_end_val.setValue(0.4500)
        self.spn_end_val.valueChanged.connect(self._on_end_val_changed)
        lay_exp.addWidget(self.spn_end_val, 0, 3)

        lay_exp.addWidget(QLabel("Step:"), 1, 0)
        self.spn_step_size = QDoubleSpinBox()
        self.spn_step_size.setRange(-1e6, 1e6)
        self.spn_step_size.setDecimals(4)
        self.spn_step_size.setValue(0.0500)
        self.spn_step_size.valueChanged.connect(self._on_step_size_changed)
        lay_exp.addWidget(self.spn_step_size, 1, 1)

        lay_exp.addWidget(QLabel("N Steps:"), 1, 2)
        self.spn_num_steps = QSpinBox()
        self.spn_num_steps.setRange(2, 100000)
        self.spn_num_steps.setValue(10)
        self.spn_num_steps.valueChanged.connect(self._on_num_steps_changed)
        lay_exp.addWidget(self.spn_num_steps, 1, 3)

        self.lbl_scan_progress = QLabel("Scan Progress: Ready (0 of 10 steps) | Delay Stage (mm): 0.0000")
        self.lbl_scan_progress.setStyleSheet(
            "font-weight: bold; font-size: 10px; color: #0d6efd; padding: 2px 0px;"
        )
        self.lbl_scan_progress.setWordWrap(True)
        lay_exp.addWidget(self.lbl_scan_progress, 2, 0, 1, 4)

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

        lay_exp.addLayout(scan_btn_row, 3, 0, 1, 4)
        right_pane.addWidget(grp_exp)

        # --- Group 3: Data Storage & Log (Compact log window) -------------
        grp_storage = QGroupBox("Data Storage & Activity Log")
        lay_storage = QVBoxLayout(grp_storage)
        lay_storage.setSpacing(4)
        lay_storage.setContentsMargins(6, 8, 6, 6)

        store_grid = QGridLayout()
        store_grid.setSpacing(4)

        store_grid.addWidget(QLabel("Folder:"), 0, 0)
        self.txt_storage_dir = QLineEdit(str(self.scan_manager.storage_dir))
        self.txt_storage_dir.textChanged.connect(self._on_storage_dir_edited)
        store_grid.addWidget(self.txt_storage_dir, 0, 1)

        self.btn_browse = QPushButton("Browse")
        self.btn_browse.clicked.connect(self._browse_directory)
        store_grid.addWidget(self.btn_browse, 0, 2)

        self.btn_open_folder = QPushButton("Open")
        self.btn_open_folder.clicked.connect(self._open_storage_folder)
        store_grid.addWidget(self.btn_open_folder, 0, 3)

        store_grid.addWidget(QLabel("Header:"), 1, 0)
        self.txt_file_header = QLineEdit("HHG Scan")
        store_grid.addWidget(self.txt_file_header, 1, 1)

        store_grid.addWidget(QLabel("Param:"), 1, 2)
        self.txt_scan_param = QLineEdit("Delay Stage (mm)")
        self.txt_scan_param.textChanged.connect(lambda _: self._update_progress_display())
        store_grid.addWidget(self.txt_scan_param, 1, 3)

        lay_storage.addLayout(store_grid)

        # Greatly reduced height for activity log so it doesn't take over vertical space
        self.txt_activity_log = QTextEdit()
        self.txt_activity_log.setReadOnly(True)
        self.txt_activity_log.setStyleSheet("font-family: Consolas, monospace; font-size: 10px;")
        self.txt_activity_log.setFixedHeight(75)
        lay_storage.addWidget(self.txt_activity_log)

        right_pane.addWidget(grp_storage)
        right_pane.addStretch(1)

        root_layout.addLayout(right_pane, stretch=0)

        self._update_progress_display()

    # =========================================================================
    # GO and STOP Controls (Top-Left Docked)
    # =========================================================================
    def _set_go_button_style(self, active: bool) -> None:
        if active:
            self.btn_go.setText("RUNNING")
            self.btn_go.setStyleSheet(
                "border: 2px solid #28a745; background-color: #28a745; color: white; "
                "font-weight: bold; font-size: 13px; padding: 6px 20px; border-radius: 4px;"
            )
        else:
            self.btn_go.setText("GO")
            self.btn_go.setStyleSheet(
                "border: 2px solid #28a745; background-color: transparent; color: #28a745; "
                "font-weight: bold; font-size: 13px; padding: 6px 20px; border-radius: 4px;"
            )

    def _on_go_clicked(self) -> None:
        """Connect if disconnected, then start continuous live camera view."""
        if self._is_live_active:
            self._stop_live()
            return

        if not self.camera.is_connected:
            self._connect_camera()

        if self.camera.is_connected:
            self._start_live()

    def _start_live(self) -> None:
        if not self.camera.is_connected:
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._append_log("[LIVE] Cannot start live stream while an experiment scan is running.")
            return

        self._is_live_active = True
        self._set_go_button_style(active=True)
        self.lbl_system_status.setText("Status: Live View Active (Streaming)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")

        if self.active_live_task is None or not self.active_live_task.isRunning():
            self.active_live_task = LiveStreamTask(scan_manager=self.scan_manager, target_fps=20.0)
            self.active_live_task.frame_ready.connect(self._on_live_frame_ready)
            self.active_live_task.error_occurred.connect(self._on_preview_error)
            self.active_live_task.start()

    def _stop_live(self) -> None:
        self._is_live_active = False
        self._set_go_button_style(active=False)
        self.lbl_system_status.setText("Status: Live Paused")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #6c757d; padding-left: 8px;")

        if self.active_live_task is not None:
            self.active_live_task.stop()
            self.active_live_task.wait(400)
            self.active_live_task = None

    def _on_stop_clicked(self) -> None:
        """Global Stop: halts live view or gracefully aborts in-progress scan."""
        self._append_log("[STOP] Stop button pressed.")
        if self._is_live_active:
            self._stop_live()

        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self.lbl_system_status.setText("Status: Aborting scan after current step...")
            self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
            self.btn_take_measurement.setText("Stopping...")
            self.btn_take_measurement.setEnabled(False)
            self.active_scan_task.request_abort()

    def _connect_camera(self) -> None:
        """Seamless camera connection: uses physical pco.edge 5.5 if detected, else mock emulator."""
        self.lbl_system_status.setText("Status: Connecting to camera...")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        QApplication.processEvents()

        t_start = time.perf_counter()
        is_sim = False

        pco_error = None
        try:
            from hhg_control.drivers.pco_edge import PcoEdgeCamera
            cam = PcoEdgeCamera()
            cam.connect()
            self.camera = cam
        except Exception as err:
            pco_error = str(err)
            cam = MockPcoCamera()
            cam.connect()
            self.camera = cam
            is_sim = True

        conn_time = time.perf_counter() - t_start
        self.scan_manager.camera = self.camera
        self._on_exposure_changed(self.spn_exposure.value())

        info = self.camera.get_sensor_info()
        model_name = info.get("model", "pco.edge 5.5")

        if is_sim:
            if pco_error:
                self._append_log(f"[INFO] Physical camera search: {pco_error}")
            self._append_log(f"[CONNECT] Connected to simulated camera in {conn_time:.2f} s.")
            self.lbl_system_status.setText("Status: Connected (Simulated Camera)")
        else:
            self._append_log(f"[CONNECT] Connected to physical {model_name} on USB 3.0 in {conn_time:.2f} s.")
            self.lbl_system_status.setText(f"Status: Connected ({model_name} USB 3.0)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")

        # Apply GUI readout mode to camera, or sync combobox if camera has mode
        desired_mode = self.cmb_readout_mode.itemData(self.cmb_readout_mode.currentIndex())
        if desired_mode is not None and hasattr(self.camera, "set_readout_mode"):
            try:
                self.camera.set_readout_mode(desired_mode)
            except Exception:
                pass

        if hasattr(self.camera, "get_readout_mode"):
            curr_mode = self.camera.get_readout_mode()
            self.cmb_readout_mode.blockSignals(True)
            for idx in range(self.cmb_readout_mode.count()):
                if self.cmb_readout_mode.itemData(idx) == curr_mode:
                    self.cmb_readout_mode.setCurrentIndex(idx)
                    break
            self.cmb_readout_mode.blockSignals(False)

    def _on_readout_mode_changed(self, index: int) -> None:
        """Handle user toggling camera sensor readout mode (Rolling Shutter vs Global Reset)."""
        mode = self.cmb_readout_mode.itemData(index)
        if mode is None:
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
            self._connect_camera()
            return

        if hasattr(self.camera, "get_readout_mode") and self.camera.get_readout_mode() == mode:
            return

        was_live = self._is_live_active
        if was_live:
            self._stop_live()

        self.lbl_system_status.setText(f"Status: Switching to {mode.name}... (Camera reconfiguring)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        self._append_log(f"[READOUT MODE] Switching sensor mode to {mode.name} (PCO setup value {mode.value})...")
        QApplication.processEvents()

        try:
            self.camera.set_readout_mode(mode)
            self._append_log(f"[READOUT MODE] Camera successfully configured to {mode.name}.")
            self.lbl_system_status.setText(f"Status: Mode Active ({mode.name})")
            self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
            self.cmb_readout_mode.blockSignals(True)
            for idx in range(self.cmb_readout_mode.count()):
                if self.cmb_readout_mode.itemData(idx) == mode:
                    self.cmb_readout_mode.setCurrentIndex(idx)
                    break
            self.cmb_readout_mode.blockSignals(False)
        except Exception as exc:
            self._append_log(f"[READOUT MODE ERROR] Failed to set {mode.name}: {exc}")
            QMessageBox.warning(self, "Readout Mode Error", f"Could not switch readout mode to {mode.name}:\n\n{exc}")
            # Revert combo box to current camera mode
            self.cmb_readout_mode.blockSignals(True)
            curr = self.camera.get_readout_mode()
            for idx in range(self.cmb_readout_mode.count()):
                if self.cmb_readout_mode.itemData(idx) == curr:
                    self.cmb_readout_mode.setCurrentIndex(idx)
                    break
            self.cmb_readout_mode.blockSignals(False)
        finally:
            if was_live:
                self._start_live()
            else:
                self._capture_single_preview()

    # =========================================================================
    # Live Preview & Single Capture
    # =========================================================================
    def _capture_single_preview(self) -> None:
        """Acquire a single frame and update display without starting live loop."""
        if not self.camera.is_connected:
            self._connect_camera()
        if self.camera.is_connected and not self._is_live_active:
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
            if self.active_live_task is not None:
                self.active_live_task.gui_ready = True
            return

        try:
            self._update_display(frame, meta=meta)
        finally:
            if self.active_live_task is not None:
                self.active_live_task.gui_ready = True

    def _on_preview_frame_ready(self, frame: np.ndarray, meta: dict) -> None:
        self._update_display(frame, meta=meta)

    def _on_preview_error(self, err_msg: str) -> None:
        self._append_log(f"[ERROR] Live frame error: {err_msg}")
        self._stop_live()

    def _on_preview_task_finished(self) -> None:
        if self.active_preview_task is not None:
            self.active_preview_task.deleteLater()
            self.active_preview_task = None

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
        self._clim_low = low
        self._clim_high = high
        if self._image_artist is not None:
            self._image_artist.set_clim(self._clim_low, self._clim_high)
            self.canvas.draw_idle()

    def _on_clim_apply_clicked(self) -> None:
        self._on_clim_changed()

    def _on_clim_auto_clicked(self) -> None:
        """Auto-scale to the last acquired frame's min and max pixel values."""
        if hasattr(self, "_last_frame") and self._last_frame is not None:
            self._set_clim(int(self._last_frame.min()), int(self._last_frame.max()))

    def _on_canvas_button_press(self, event) -> None:
        """Track when user starts dragging an ROI to pause live frame blitting and eliminate lag."""
        if getattr(self, "btn_draw_roi", None) and self.btn_draw_roi.isChecked():
            self._is_dragging_roi = True

    def _on_canvas_button_release(self, event) -> None:
        """Resume live frame blitting after user finishes dragging an ROI."""
        self._is_dragging_roi = False

    def _on_canvas_click(self, event) -> None:
        pass

    def _update_display(self, frame: np.ndarray, meta: Optional[dict] = None) -> None:
        """Update canvas display with adaptive downsampling, zooming to active ROI with zero dead space."""
        # Never interrupt canvas while the user is actively dragging the ROI rectangle
        if self._is_dragging_roi:
            return

        self._last_frame = frame
        self._frame_count += 1

        # Pixel intensity metrics (fast sub-sampled for instant calculation on large frames)
        sample = frame[::2, ::2] if frame.size > 500000 else frame
        c_min = int(sample.min())
        c_max = int(sample.max())
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

        # Adaptive downsampling to match 550x450 canvas, avoiding wasting CPU rendering millions of invisible pixels
        h, w = frame.shape
        step_y = max(1, h // 450)
        step_x = max(1, w // 550)
        downsample_factor = max(step_y, step_x)
        display_frame = frame[::downsample_factor, ::downsample_factor] if downsample_factor > 1 else frame
        x0, y0, x1, y1 = self.camera.get_roi()

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
            self._colorbar = self.figure.colorbar(self._image_artist, ax=self.axis, fraction=0.046, pad=0.04)
            self._colorbar.ax.tick_params(labelsize=8)

            # Rectangle selector with no fill color, no handles, and clean outline
            self._roi_selector = RectangleSelector(
                self.axis,
                self._on_roi_drawn,
                useblit=True,
                button=[1],
                minspanx=5,
                minspany=5,
                spancoords="data",
                interactive=False,
                props=dict(facecolor="none", edgecolor="red", linewidth=1.5, linestyle="-", fill=False),
                handle_props=dict(alpha=0, marker=""),
            )
            self._roi_selector.set_active(False)
            self._roi_patch = None
            self._current_displayed_roi = (x0, y0, x1, y1)
            self.axis.set_aspect("auto")
            self.axis.set_xlim(x0, x1)
            self.axis.set_ylim(y1, y0)
            self._image_artist.set_clim(self._clim_low, self._clim_high)
        else:
            self._image_artist.set_data(display_frame)
            if self._current_displayed_roi != (x0, y0, x1, y1):
                self._current_displayed_roi = (x0, y0, x1, y1)
                self._image_artist.set_extent([x0, x1, y1, y0])
                self.axis.set_aspect("auto")
                self.axis.set_xlim(x0, x1)
                self.axis.set_ylim(y1, y0)

        # Hardware Camera Timestamp Overlay (Top-Left corner — time only)
        cam_time = ""
        if meta:
            cam_time = meta.get("camera_time_str") or ""
        if not cam_time:
            cam_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        if self._timestamp_artist is None:
            self._timestamp_artist = self.axis.text(
                0.01, 0.98, cam_time,
                transform=self.axis.transAxes,
                fontsize=7, color="#aaaaaa",
                fontweight="normal",
                va="top", ha="left",
                bbox={"facecolor": "black", "alpha": 0.25, "edgecolor": "none", "boxstyle": "round,pad=0.2"}
            )
        else:
            self._timestamp_artist.set_text(cam_time)

        self.canvas.draw_idle()

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
        """Force symmetrical vertical constraint around y=1080 (Y1 = 2160 - Y0) and redraw ROI."""
        if self._is_updating_roi:
            return
        self._is_updating_roi = True
        try:
            val = self.spn_roi_y0.value()
            val = max(0, min(1072, val))
            self.spn_roi_y1.setValue(2160 - val)
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
        if self._roi_selector is not None:
            self._roi_selector.set_active(checked)
            if checked:
                self._append_log("[ROI] Draw mode active: Drag a rectangle on the camera image.")

    def _on_roi_drawn(self, eclick, erelease) -> None:
        """Handle rectangle drawn on image: expand Y symmetrically around 1080 and snap X."""
        self._is_dragging_roi = False
        if eclick.xdata is None or erelease.xdata is None or eclick.ydata is None or erelease.ydata is None:
            return

        x_min, x_max = sorted([float(eclick.xdata), float(erelease.xdata)])
        y_min, y_max = sorted([float(eclick.ydata), float(erelease.ydata)])

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

        # Clear the temporary selector rectangle to remove drag handles
        if self._roi_selector is not None:
            self._roi_selector.clear()

        # Display the persistent minimal symmetric ROI outline
        self._draw_roi_patch(x0, y0, x1, y1)

        self._append_log(
            f"[ROI SELECTED] Minimal bounding symmetric ROI: X:[{x0}, {x1}], Y:[{y0}, {y1}] ({x1-x0}x{y1-y0} px).\n"
            f"               Click 'Apply Hardware ROI' to apply to camera sensor."
        )

    def _deactivate_draw_roi(self) -> None:
        """Deactivate draw-ROI mode and uncheck the button."""
        self._is_dragging_roi = False
        if self._roi_selector is not None:
            self._roi_selector.clear()
            self._roi_selector.set_active(False)
        self.btn_draw_roi.blockSignals(True)
        self.btn_draw_roi.setChecked(False)
        self.btn_draw_roi.blockSignals(False)

    def _draw_roi_patch(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Stamp a persistent thin red outline rectangle onto the axis."""
        if self._roi_patch is not None:
            try:
                self._roi_patch.remove()
            except (ValueError, AttributeError):
                pass
            self._roi_patch = None

        width = x1 - x0
        height = y1 - y0
        self._roi_patch = mpatches.Rectangle(
            (x0, y0), width, height,
            linewidth=1.5, edgecolor="red", facecolor="none",
            linestyle="-", zorder=5, fill=False
        )
        self.axis.add_patch(self._roi_patch)
        if self._image_artist is None:
            self.axis.set_xlim(0, 2560)
            self.axis.set_ylim(2160, 0)
            self.axis.set_aspect("auto")
        self.canvas.draw_idle()

    def _apply_roi(self) -> None:
        """Configure hardware ROI on sensor; pixels outside are shut off and monitor zooms in."""
        if not self.camera.is_connected:
            self._connect_camera()

        was_live = self._is_live_active
        if was_live:
            self._stop_live()

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

        try:
            self.scan_manager.set_roi((x0, y0, x1, y1))
            self._current_displayed_roi = None
            self._append_log(
                f"[ROI APPLIED] Sensor readout set to X:[{x0}, {x1}], Y:[{y0}, {y1}] ({x1-x0}x{y1-y0} px).\n"
                f"              Outside pixels shut off. Display zoomed to ROI."
            )
            self._deactivate_draw_roi()
            self._draw_roi_patch(x0, y0, x1, y1)
            self._clear_paused_scan("Hardware ROI modified")
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Hardware ROI", str(exc))
        finally:
            if was_live:
                self._start_live()
            else:
                self._capture_single_preview()

    def _reset_full_sensor(self) -> None:
        """Switch camera readout to full 2560×2160 sensor without touching the configured ROI."""
        if not self.camera.is_connected:
            self._connect_camera()

        was_live = self._is_live_active
        if was_live:
            self._stop_live()

        try:
            # Send full-sensor ROI to camera hardware
            self.scan_manager.set_roi((0, 0, 2560, 2160))
            self._current_displayed_roi = None
            self._append_log(
                "[FULL SENSOR] Switched readout to full 2560×2160. "
                "Configured ROI preserved — click 'Apply Hardware ROI' to reactivate it."
            )
            # Retain and display the configured ROI patch on the full sensor
            self._sync_roi_patch_from_spinboxes()
        except Exception as exc:
            QMessageBox.warning(self, "Error switching to full sensor", str(exc))
        finally:
            if was_live:
                self._start_live()
            else:
                self._capture_single_preview()

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
            calc_step_size = (end_val - start) / (num_steps - 1) if num_steps > 1 else 0.0
            self.spn_step_size.setValue(calc_step_size)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _clear_paused_scan(self, reason: str = "") -> None:
        if self._paused_step is not None:
            self._paused_step = None
            self.btn_cut_measurement.setVisible(False)
            self.btn_take_measurement.setText("Take Measurement")
            if reason:
                self._append_log(f"[SCAN RESET] {reason}. Next scan starts from Step 1.")

    def _update_progress_display(self) -> None:
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            return
        if self._paused_step is not None:
            self._clear_paused_scan(reason="Parameters modified")
        start = self.spn_start_val.value()
        total_steps = self.spn_num_steps.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {total_steps} steps) | {param_name}: {start:.4f}"
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
    def _on_storage_dir_edited(self, text: str) -> None:
        folder = Path(text.strip()).expanduser()
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
    def _toggle_measurement_scan(self) -> None:
        """Start scan, stop in-progress scan, or continue paused scan."""
        if not self.camera.is_connected:
            self._connect_camera()

        # If scan is already running, this button functions as Stop
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._on_stop_clicked()
            return

        # Stop live stream cleanly before starting multi-step scan
        if self._is_live_active:
            self._stop_live()

        start_step = self._paused_step if self._paused_step is not None else 0

        target_dir = Path(self.txt_storage_dir.text().strip()).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        self.scan_manager.set_storage_dir(target_dir)

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
                f"[SCAN START] Initiating scan: {num_steps} steps from {start_val:.4f} to "
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
        )
        self.active_scan_task.step_started.connect(self._on_scan_step_started)
        self.active_scan_task.step_completed.connect(self._on_scan_step_completed)
        self.active_scan_task.scan_finished.connect(self._on_scan_finished)
        self.active_scan_task.scan_aborted.connect(self._on_scan_aborted)
        self.active_scan_task.error_occurred.connect(self._on_scan_error)
        self.active_scan_task.finished.connect(self._on_scan_task_finished)
        self.active_scan_task.start()

    def _set_ui_scanning_state(self, is_scanning: bool) -> None:
        if is_scanning:
            self.btn_take_measurement.setText("Stop Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #dc3545; color: white; padding: 8px;"
            )
            self.btn_cut_measurement.setVisible(False)
            self.btn_go.setEnabled(False)
            self.spn_exposure.setEnabled(False)
            self.spn_frames.setEnabled(False)
            self.spn_start_val.setEnabled(False)
            self.spn_end_val.setEnabled(False)
            self.spn_step_size.setEnabled(False)
            self.spn_num_steps.setEnabled(False)
            self.btn_apply_roi.setEnabled(False)
            self.btn_full_sensor.setEnabled(False)
            self.btn_draw_roi.setEnabled(False)
        else:
            self.btn_go.setEnabled(True)
            self.spn_exposure.setEnabled(True)
            self.spn_frames.setEnabled(True)
            self.spn_start_val.setEnabled(True)
            self.spn_end_val.setEnabled(True)
            self.spn_step_size.setEnabled(True)
            self.spn_num_steps.setEnabled(True)
            self.btn_apply_roi.setEnabled(True)
            self.btn_full_sensor.setEnabled(True)
            self.btn_draw_roi.setEnabled(True)

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
            f"[SAVED] Step {step_idx + 1}/{total_steps} -> {h5_path.name} ({file_size_mb:.2f} MB, {param_name}={param_val:.4f})"
        )
        self._update_display(latest_frame)
        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {step_idx + 1} of {total_steps} (Saved) | {param_name}: {param_val:.4f}"
        )

    def _on_scan_finished(self, total_steps: int) -> None:
        self._paused_step = None
        end_val = self.spn_end_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Completed all {total_steps} steps | {param_name}: {end_val:.4f}"
        )
        self.lbl_system_status.setText(f"Status: Scan Completed Successfully ({total_steps} steps saved)")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
        self._append_log(f"[SCAN COMPLETE] All {total_steps} steps saved to disk.")

    def _on_scan_aborted(
        self,
        stopped_step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str
    ) -> None:
        self._paused_step = stopped_step_idx
        self.lbl_scan_progress.setText(
            f"Scan Progress: Stopped at Step {stopped_step_idx + 1} of {total_steps} | {param_name}: {param_val:.4f}"
        )
        self.lbl_system_status.setText(f"Status: Scan Stopped at Step {stopped_step_idx + 1} of {total_steps}")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #d97706; padding-left: 8px;")
        self._append_log(
            f"[SCAN STOPPED] Stopped at Step {stopped_step_idx + 1} of {total_steps}.\n"
            f"              Click 'Continue from Step {stopped_step_idx + 1}' or 'Cut Measurement'."
        )

    def _cut_measurement(self) -> None:
        stopped = self._paused_step
        self._paused_step = None
        self.btn_cut_measurement.setVisible(False)
        self.btn_take_measurement.setText("Take Measurement")
        self.btn_take_measurement.setStyleSheet(
            "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
        )
        total_steps = self.spn_num_steps.value()
        start_val = self.spn_start_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {total_steps} steps) | {param_name}: {start_val:.4f}"
        )
        self.lbl_system_status.setText("Status: Idle / Ready")
        self.lbl_system_status.setStyleSheet("font-weight: bold; font-size: 13px; color: #198754; padding-left: 8px;")
        self._append_log(f"[SCAN FINALIZED] Scan cut at Step {stopped}. Next scan will start from Step 1.")

    def _on_scan_error(self, err_msg: str) -> None:
        self._append_log(f"[ERROR] Scan execution error: {err_msg}")
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

    def _append_log(self, message: str) -> None:
        self.txt_activity_log.append(message)

    def closeEvent(self, event) -> None:
        """Graceful shutdown: stop live, abort scan, wait for threads, then disconnect camera."""
        self._is_live_active = False

        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self._closing = True
            self.active_scan_task.request_abort()
            event.ignore()
            return

        if self.active_live_task is not None:
            self.active_live_task.stop()
            self.active_live_task.wait(400)
            self.active_live_task = None

        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            self.active_preview_task.wait(500)

        try:
            if self.camera.is_connected:
                self.camera.close()
                self._append_log("[CLOSE] Camera disconnected cleanly.")
        except Exception as exc:
            self._append_log(f"[CLOSE] Warning during camera disconnect: {exc}")

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
