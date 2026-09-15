"""
PyQt6 Graphical User Interface for pco.edge 5.5 sCMOS Camera Control and Scan Sequencer.
Uses dedicated QThread background tasks to prevent UI freezing and ensure thread-safe hardware access.
"""

import sys
import os
import time
import subprocess
from pathlib import Path
from typing import Optional
import numpy as np

from datetime import datetime, timezone
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox,
    QPushButton, QFileDialog, QTextEdit, QMessageBox, QRadioButton,
    QButtonGroup, QScrollArea, QSplitter, QGridLayout, QFrame, QSizePolicy
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from hhg_control.drivers.base_camera import BaseCamera
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


class PreviewTask(QThread):
    """
    Background worker thread for capturing a single live preview frame without writing to disk.
    Keeps the GUI responsive during exposure.
    """
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager) -> None:
        super().__init__()
        self.scan_manager = scan_manager

    def run(self) -> None:
        try:
            self.frame_ready.emit(self.scan_manager.acquire_preview())
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class ScanSequenceTask(QThread):
    """
    Dedicated background worker thread for executing a full multi-step scan sequence.
    Iterates from step 0 to num_steps - 1, recording and saving HDF5 datasets at each step.
    Supports graceful abort requests without corrupting files or hardware state.
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
            steps_done = 0
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
                steps_done += 1
                self.step_completed.emit(
                    step, self.num_steps, val, self.param_name, latest_frame, filepath
                )
                if self._abort_requested:
                    # User requested stop during/after this step; resume retakes this step as requested
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
        self.setWindowTitle("HHG Attosecond Lab - PCO Camera Controller and Sequencer")
        self.resize(1260, 880)

        # Core instrumentation layers
        self.camera: BaseCamera = MockPcoCamera()
        default_storage = Path.cwd() / "data"
        default_storage.mkdir(parents=True, exist_ok=True)
        self.scan_manager = CameraScanManager(camera=self.camera, storage_dir=default_storage)

        # Thread management
        self.active_scan_task: Optional[ScanSequenceTask] = None
        self.active_preview_task: Optional[PreviewTask] = None

        # State tracking
        self._live_requested = False
        self._is_updating_range: bool = False
        self._paused_step: Optional[int] = None
        self._current_executing_step: int = 0

        # Matplotlib display artist cache
        self._image_artist = None
        self._colorbar = None

        self._build_ui()

    def _build_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Root layout with resizable horizontal splitter
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        # =========================================================================
        # Left Panel: Controls & Parameter Forms (Enclosed in a QScrollArea)
        # =========================================================================
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(460)

        left_container = QWidget()
        left_panel = QVBoxLayout(left_container)
        left_panel.setContentsMargins(4, 4, 8, 4)
        left_panel.setSpacing(10)
        left_scroll.setWidget(left_container)

        # --- Section 1: Hardware Connection ---
        grp_connection = QGroupBox("Hardware Connection")
        lay_connection = QVBoxLayout(grp_connection)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setStyleSheet("font-weight: bold; font-size: 13px; padding: 6px;")
        self.btn_connect.setToolTip("Connect to the explicitly selected camera mode.")
        self.btn_connect.clicked.connect(self._toggle_connection)
        self.camera_mode = QComboBox()
        self.camera_mode.addItems(["Simulation", "Real pco.edge 5.5 USB 3.0"])
        lay_connection.addWidget(self.camera_mode)
        lay_connection.addWidget(self.btn_connect)

        self.lbl_hw_status = QLabel("Status: Disconnected")
        self.lbl_hw_status.setStyleSheet("color: #6c757d; font-weight: bold; font-size: 12px;")
        lay_connection.addWidget(self.lbl_hw_status)
        left_panel.addWidget(grp_connection)

        # --- Section 2: Camera Parameters ---
        grp_acq = QGroupBox("Camera Parameters")
        lay_acq = QVBoxLayout(grp_acq)

        lbl_exp = QLabel("Sensor Exposure Duration (0.5 to 10,000.0 ms):")
        lbl_exp.setToolTip("Physical sensor exposure duration per measurement in milliseconds.")
        lay_acq.addWidget(lbl_exp)

        self.spn_exposure = QDoubleSpinBox()
        self.spn_exposure.setRange(0.5, 10000.0)
        self.spn_exposure.setValue(10.0)
        self.spn_exposure.setDecimals(2)
        self.spn_exposure.setMinimumHeight(28)
        self.spn_exposure.valueChanged.connect(self._on_exposure_changed)
        lay_acq.addWidget(self.spn_exposure)

        lbl_frames = QLabel("Measurements per Step:")
        lbl_frames.setToolTip("Number of separate measurements recorded and saved into the HDF5 stack at this step.")
        lay_acq.addWidget(lbl_frames)

        self.spn_frames = QSpinBox()
        self.spn_frames.setRange(1, 1000)
        self.spn_frames.setValue(5)
        self.spn_frames.setMinimumHeight(28)
        lay_acq.addWidget(self.spn_frames)

        self.btn_preview = QPushButton("Capture Single Frame (Preview Only - No File Saved)")
        self.btn_preview.setEnabled(False)
        self.btn_preview.setStyleSheet("padding: 5px;")
        self.btn_preview.setToolTip("Acquire a single frame and refresh the live monitor without saving to disk.")
        self.btn_preview.clicked.connect(self._capture_preview)
        lay_acq.addWidget(self.btn_preview)
        self.btn_live = QPushButton("Start Live")
        self.btn_live.clicked.connect(self._toggle_live)
        lay_acq.addWidget(self.btn_live)
        self.lbl_frame_time = QLabel("Frame received (UTC): ?")
        lay_acq.addWidget(self.lbl_frame_time)
        self.color_scale = QComboBox()
        self.color_scale.addItems(["Auto scale", "Full 16-bit (0?65535)"])
        lay_acq.addWidget(self.color_scale)
        self.roi_group = QGroupBox("Hardware ROI (pixels, upper bounds excluded)")
        roi_layout = QGridLayout(self.roi_group)
        self.roi_bounds = []
        for col, (label, maximum, value) in enumerate(zip(
            ("X start", "Y start", "X end", "Y end"),
            (2560, 2160, 2560, 2160), (0, 0, 2560, 2160),
        )):
            field = QSpinBox()
            field.setRange(0, maximum)
            field.setValue(value)
            roi_layout.addWidget(QLabel(label), 0, col)
            roi_layout.addWidget(field, 1, col)
            self.roi_bounds.append(field)
        apply_roi = QPushButton("Apply Hardware ROI")
        apply_roi.clicked.connect(self._apply_roi)
        roi_layout.addWidget(apply_roi, 2, 0, 1, 2)
        reset_roi = QPushButton("Full Sensor")
        reset_roi.clicked.connect(self._reset_roi)
        roi_layout.addWidget(reset_roi, 2, 2, 1, 2)
        self.lbl_roi = QLabel("Stop live viewing before applying ROI.")
        roi_layout.addWidget(self.lbl_roi, 3, 0, 1, 4)
        lay_acq.addWidget(self.roi_group)
        left_panel.addWidget(grp_acq)

        # --- Section 3: Data Storing ---
        grp_storage = QGroupBox("Data Storing")
        lay_storage = QVBoxLayout(grp_storage)

        lbl_dir = QLabel("Destination Folder:")
        lay_storage.addWidget(lbl_dir)
        lay_dir = QHBoxLayout()
        self.txt_storage_dir = QLineEdit(str(self.scan_manager.storage_dir))
        self.txt_storage_dir.setToolTip("Target directory where all HDF5 scan datasets are saved.")
        self.txt_storage_dir.setMinimumHeight(28)
        self.txt_storage_dir.textChanged.connect(self._on_storage_dir_edited)
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.setStyleSheet("padding: 4px 8px;")
        self.btn_browse.clicked.connect(self._browse_directory)
        self.btn_open_folder = QPushButton("Open Folder")
        self.btn_open_folder.setStyleSheet("padding: 4px 8px;")
        self.btn_open_folder.setToolTip("Open this destination folder in Windows File Explorer.")
        self.btn_open_folder.clicked.connect(self._open_storage_folder)
        lay_dir.addWidget(self.txt_storage_dir)
        lay_dir.addWidget(self.btn_browse)
        lay_dir.addWidget(self.btn_open_folder)
        lay_storage.addLayout(lay_dir)

        lbl_file_header = QLabel("File Header:")
        lay_storage.addWidget(lbl_file_header)
        self.txt_file_header = QLineEdit("HHG Scan")
        self.txt_file_header.setMinimumHeight(28)
        self.txt_file_header.setToolTip("Prefix used for saved HDF5 files and experiment metadata.")
        lay_storage.addWidget(self.txt_file_header)

        lbl_scan_param = QLabel("Scanning Parameter:")
        lay_storage.addWidget(lbl_scan_param)
        self.txt_scan_param = QLineEdit("Delay Stage (mm)")
        self.txt_scan_param.setMinimumHeight(28)
        self.txt_scan_param.setToolTip("Name of the physical variable being scanned.")
        lay_storage.addWidget(self.txt_scan_param)

        left_panel.addWidget(grp_storage)

        # --- Section 4: Experiment Parameters (Responsive Grid Layout) ---
        grp_exp = QGroupBox("Experiment Parameters")
        lay_exp = QVBoxLayout(grp_exp)
        lay_exp.setSpacing(8)

        # Clean 4-Column Grid: Inputs for Start, End, Step Size, and Number of Steps
        grid_exp = QGridLayout()
        grid_exp.setHorizontalSpacing(10)
        grid_exp.setVerticalSpacing(8)

        # Row 0: Start Value & End Value (Both directly editable; auto-syncs)
        lbl_start = QLabel("Start Value:")
        self.spn_start_val = QDoubleSpinBox()
        self.spn_start_val.setRange(-1e6, 1e6)
        self.spn_start_val.setDecimals(4)
        self.spn_start_val.setValue(0.0)
        self.spn_start_val.setMinimumHeight(28)
        self.spn_start_val.valueChanged.connect(self._on_start_val_changed)

        lbl_end = QLabel("End Value:")
        self.spn_end_val = QDoubleSpinBox()
        self.spn_end_val.setRange(-1e6, 1e6)
        self.spn_end_val.setDecimals(4)
        self.spn_end_val.setValue(0.4500)
        self.spn_end_val.setMinimumHeight(28)
        self.spn_end_val.setToolTip("Target end setpoint. With constant Number of Steps, changing this recalculates Step Size.")
        self.spn_end_val.valueChanged.connect(self._on_end_val_changed)

        grid_exp.addWidget(lbl_start, 0, 0)
        grid_exp.addWidget(self.spn_start_val, 0, 1)
        grid_exp.addWidget(lbl_end, 0, 2)
        grid_exp.addWidget(self.spn_end_val, 0, 3)

        # Row 1: Step Size & Number of Steps (Constrained counterparts)
        lbl_step_size = QLabel("Step Size:")
        self.spn_step_size = QDoubleSpinBox()
        self.spn_step_size.setRange(-1e6, 1e6)
        self.spn_step_size.setDecimals(4)
        self.spn_step_size.setValue(0.0500)
        self.spn_step_size.setMinimumHeight(28)
        self.spn_step_size.setToolTip("Step increment. With constant Number of Steps, changing this recalculates End Value.")
        self.spn_step_size.valueChanged.connect(self._on_step_size_changed)

        lbl_num_steps = QLabel("Number of Steps:")
        self.spn_num_steps = QSpinBox()
        self.spn_num_steps.setRange(2, 100000)
        self.spn_num_steps.setValue(10)
        self.spn_num_steps.setMinimumHeight(28)
        self.spn_num_steps.setToolTip("Constant number of steps. Changing this recalculates End Value.")
        self.spn_num_steps.valueChanged.connect(self._on_num_steps_changed)

        grid_exp.addWidget(lbl_step_size, 1, 0)
        grid_exp.addWidget(self.spn_step_size, 1, 1)
        grid_exp.addWidget(lbl_num_steps, 1, 2)
        grid_exp.addWidget(self.spn_num_steps, 1, 3)

        grid_exp.setColumnStretch(1, 1)
        grid_exp.setColumnStretch(3, 1)
        lay_exp.addLayout(grid_exp)

        # Scan Progress Line (Styled identically to the status indicators)
        self.lbl_scan_progress = QLabel("Scan Progress: Ready (0 of 10 steps) | Delay Stage (mm): 0.0000")
        self.lbl_scan_progress.setStyleSheet("font-weight: bold; font-size: 12px; color: #0d6efd; padding: 2px 0px;")
        lay_exp.addWidget(self.lbl_scan_progress)

        # Action Buttons
        lay_buttons = QHBoxLayout()
        lay_buttons.setSpacing(6)

        self.btn_take_measurement = QPushButton("Take Measurement")
        self.btn_take_measurement.setStyleSheet(
            "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
        )
        self.btn_take_measurement.setEnabled(False)
        self.btn_take_measurement.setToolTip("Start automated experiment scan through all steps until the end.")
        self.btn_take_measurement.clicked.connect(self._toggle_measurement_scan)
        lay_buttons.addWidget(self.btn_take_measurement)
        lay_buttons.addWidget(self.btn_take_measurement, stretch=3)

        self.btn_cut_measurement = QPushButton("Cut Measurement Here")
        self.btn_cut_measurement.setStyleSheet(
            "font-weight: bold; font-size: 12px; background-color: #6c757d; color: white; padding: 8px;"
        )
        self.btn_cut_measurement.setToolTip(
            "End the scan session at the current point and reset so the next measurement starts fresh from Step 1."
        )
        self.btn_cut_measurement.setVisible(False)
        self.btn_cut_measurement.clicked.connect(self._cut_measurement)
        lay_buttons.addWidget(self.btn_cut_measurement, stretch=1)

        lay_exp.addLayout(lay_buttons)

        left_panel.addWidget(grp_exp)

        # --- Section 5: Activity Log ---
        grp_log = QGroupBox("Activity Log")
        lay_log = QVBoxLayout(grp_log)
        self.txt_activity_log = QTextEdit()
        self.txt_activity_log.setReadOnly(True)
        self.txt_activity_log.setMinimumHeight(110)
        self.txt_activity_log.setMaximumHeight(170)
        self.txt_activity_log.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        lay_log.addWidget(self.txt_activity_log)
        left_panel.addWidget(grp_log)

        splitter.addWidget(left_scroll)

        # =========================================================================
        # Right Panel: Live Camera Frame Monitor & Intensity Analytics
        # =========================================================================
        right_container = QWidget()
        right_panel = QVBoxLayout(right_container)
        right_panel.setContentsMargins(4, 4, 4, 4)
        right_panel.setSpacing(6)

        self.lbl_task_status = QLabel("System Status: Idle / Ready")
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #198754;")
        right_panel.addWidget(self.lbl_task_status)

        self.figure = Figure(figsize=(7, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.axis = self.figure.add_subplot(111)
        self.axis.set_title("Camera Sensor Monitor (Latest Frame)", fontsize=11, fontweight="bold")
        self.axis.set_xlabel("Sensor X Pixel Index")
        self.axis.set_ylabel("Sensor Y Pixel Index")
        right_panel.addWidget(self.canvas)

        self.lbl_intensity_metrics = QLabel("Pixel Intensity Metrics | Minimum: -- ADU | Maximum: -- ADU | Mean: -- ADU")
        self.lbl_intensity_metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_intensity_metrics.setStyleSheet("font-size: 12px; font-weight: bold; padding: 4px; background: #f8f9fa;")
        right_panel.addWidget(self.lbl_intensity_metrics)

        splitter.addWidget(right_container)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 780])

        self.txt_scan_param.textChanged.connect(lambda _: self._update_progress_display())
        self._update_progress_display()

    # =========================================================================
    # Two-Way Automatic Calculation & Display Sync
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
        """When user inputs Step Size, Number of Steps stays constant, End Value is constrained counterpart."""
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            step_size = self.spn_step_size.value()
            num_steps = self.spn_num_steps.value()
            calc_end = start + (num_steps - 1) * step_size
            self.spn_end_val.setValue(calc_end)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _on_num_steps_changed(self) -> None:
        """When user changes the constant Number of Steps, recalculate End Value counterpart."""
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            step_size = self.spn_step_size.value()
            num_steps = self.spn_num_steps.value()
            calc_end = start + (num_steps - 1) * step_size
            self.spn_end_val.setValue(calc_end)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _on_end_val_changed(self) -> None:
        """When user inputs End Value, Number of Steps stays constant, Step Size is constrained counterpart."""
        if self._is_updating_range:
            return
        self._is_updating_range = True
        try:
            start = self.spn_start_val.value()
            end_val = self.spn_end_val.value()
            num_steps = self.spn_num_steps.value()
            if num_steps > 1:
                calc_step_size = (end_val - start) / (num_steps - 1)
            else:
                calc_step_size = 0.0
            self.spn_step_size.setValue(calc_step_size)
            self._update_progress_display()
        finally:
            self._is_updating_range = False

    def _clear_paused_scan(self, reason: str = "") -> None:
        """Reset any paused scan state when parameters are modified."""
        if self._paused_step is not None:
            self._paused_step = None
            if hasattr(self, "btn_cut_measurement"):
                self.btn_cut_measurement.setVisible(False)
            if hasattr(self, "btn_take_measurement"):
                self.btn_take_measurement.setText("Take Measurement")
                self.btn_take_measurement.setToolTip("Start automated experiment scan through all steps until the end.")
            if reason:
                self._append_log(f"[SCAN RESET] {reason}. Next scan starts fresh from Step 1.")

    def _update_progress_display(self) -> None:
        """Update the scan progress text line when configuration parameters change."""
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            return
        if self._paused_step is not None:
            self._clear_paused_scan(reason="Experiment parameters modified")
        start = self.spn_start_val.value()
        total_steps = self.spn_num_steps.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {total_steps} steps) | {param_name}: {start:.4f}"
        )

    # =========================================================================
    # Storage Directory Handlers
    # =========================================================================
    def _on_storage_dir_edited(self, text: str) -> None:
        """Ensure any manual edit in the Destination Folder line edit directly updates the scan manager."""
        folder = Path(text.strip()).expanduser()
        self.scan_manager.set_storage_dir(folder)

    def _browse_directory(self) -> None:
        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Destination Folder for HDF5 Datasets",
            self.txt_storage_dir.text()
        )
        if chosen_dir:
            self.txt_storage_dir.setText(chosen_dir)
            self.scan_manager.set_storage_dir(chosen_dir)
            self._append_log(f"[STORAGE] Destination folder updated: {chosen_dir}")

    def _open_storage_folder(self) -> None:
        """Open the currently set destination folder in Windows File Explorer."""
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
    # Hardware Connection
    # =========================================================================
    def _toggle_connection(self) -> None:
        """
        Connect or disconnect camera.
        Shows connecting status, measures connection time, and turns GREEN upon connection.
        """
        if not self.camera.is_connected:
            self.lbl_hw_status.setText("Status: Connecting to camera...")
            self.lbl_hw_status.setStyleSheet("color: #d97706; font-weight: bold; font-size: 12px;")
            self.btn_connect.setEnabled(False)
            QApplication.processEvents()

            t_start = time.perf_counter()
            is_simulated = False

            try:
                is_simulated = self.camera_mode.currentIndex() == 0
                if is_simulated:
                    self.camera = MockPcoCamera()
                else:
                    from hhg_control.drivers.pco_edge import PcoEdgeCamera
                    self.camera = PcoEdgeCamera()
                self.camera.connect()
            except Exception as exc:
                self.lbl_hw_status.setText("Status: Connection failed")
                self.btn_connect.setEnabled(True)
                QMessageBox.warning(self, "Camera connection failed", str(exc))
                return

            conn_time = time.perf_counter() - t_start
            self.scan_manager.camera = self.camera
            self._on_exposure_changed(self.spn_exposure.value())

            info = self.camera.get_sensor_info()
            if is_simulated:
                self.lbl_hw_status.setText(f"Status: Connected (Simulated Camera in {conn_time:.2f} s)")
                self.lbl_hw_status.setStyleSheet("color: #198754; font-weight: bold; font-size: 12px;")
                self._append_log(
                    f"[CONNECT] Connected to Simulated Camera (2560x2160 uint16, 2D Gaussian beam) "
                    f"in {conn_time:.2f} s."
                )
            else:
                self.lbl_hw_status.setText(f"Status: Connected ({info.get('model', 'pco.edge 5.5')} in {conn_time:.2f} s)")
                self.lbl_hw_status.setStyleSheet("color: #198754; font-weight: bold; font-size: 12px;")
                self._append_log(
                    f"[CONNECT] Connected to physical camera: {info.get('model')}, "
                    f"Serial #{info.get('serial_number')} in {conn_time:.2f} s."
                )

            self.camera_mode.setEnabled(False)
            self.btn_connect.setText("Disconnect")
            self.btn_connect.setEnabled(True)
            self.btn_preview.setEnabled(True)
            self.btn_take_measurement.setEnabled(True)
        else:
            self.camera.close()
            self.lbl_hw_status.setText("Status: Disconnected")
            self.lbl_hw_status.setStyleSheet("color: #6c757d; font-weight: bold; font-size: 12px;")
            self.camera_mode.setEnabled(True)
            self.btn_connect.setText("Connect")
            self.btn_preview.setEnabled(False)
            self.btn_take_measurement.setEnabled(False)
            self._append_log("[DISCONNECT] Camera disconnected.")

    def _on_exposure_changed(self, value_ms: float) -> None:
        if self.camera.is_connected:
            try:
                self.camera.set_exposure_time(value_ms / 1000.0)
                self._append_log(f"[EXPOSURE] Set to {value_ms:.2f} ms ({value_ms / 1000.0:.6f} s)")
            except Exception as exc:
                self._append_log(f"[ERROR] Invalid exposure setting: {exc}")
                QMessageBox.warning(self, "Safety Limit Violation", str(exc))

    # =========================================================================
    # Acquisition Worker Thread Management
    # =========================================================================
    # =========================================================================
    # Live Preview and Automated Scan Execution
    # =========================================================================
    def _apply_roi(self) -> None:
        """Apply a validated readout rectangle only while all acquisition is idle."""
        if not self.camera.is_connected:
            return
        if self._live_requested or self.active_preview_task is not None or self.active_scan_task is not None:
            self.lbl_roi.setText("Stop acquisition before applying ROI.")
            return
        try:
            self.scan_manager.set_roi(tuple(field.value() for field in self.roi_bounds))
        except Exception as exc:
            QMessageBox.warning(self, "Hardware ROI", str(exc))
        actual = self.camera.get_roi()
        for field, value in zip(self.roi_bounds, actual):
            field.setValue(value)
        self.lbl_roi.setText(f"Applied ROI: {actual}")
        self._clear_paused_scan("Hardware ROI changed")

    def _reset_roi(self) -> None:
        """Select the complete 2560 by 2160 sensor and apply while idle."""
        for field, value in zip(self.roi_bounds, (0, 0, 2560, 2160)):
            field.setValue(value)
        self._apply_roi()

    def _toggle_live(self) -> None:
        """Repeat single-frame acquisitions; stopping finishes the current exposure."""
        if self._live_requested:
            self._live_requested = False
            self.btn_live.setText("Start Live")
            return
        if not self.camera.is_connected or self.active_scan_task is not None:
            return
        self._live_requested = True
        self.btn_live.setText("Stop Live (after current frame)")
        self._capture_preview()

    def _capture_preview(self) -> None:
        """Capture a single preview frame for the live monitor without saving to disk."""
        if not self.camera.is_connected:
            return
        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            return
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            return

        self.btn_preview.setEnabled(False)
        self.btn_connect.setEnabled(False)
        self.spn_exposure.setEnabled(False)
        self.btn_take_measurement.setEnabled(False)
        self.lbl_task_status.setText("System Status: Capturing Live Preview...")
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #0d6efd;")

        self.active_preview_task = PreviewTask(scan_manager=self.scan_manager)
        self.active_preview_task.frame_ready.connect(self._on_preview_frame_ready)
        self.active_preview_task.error_occurred.connect(self._on_preview_error)
        self.active_preview_task.finished.connect(self._on_preview_task_finished)
        self.active_preview_task.start()

    def _on_preview_error(self, message: str) -> None:
        """Stop repeated preview acquisition after an error."""
        self._live_requested = False
        self.btn_live.setText("Start Live")
        self._on_scan_error(message)

    def _on_preview_frame_ready(self, frame: np.ndarray) -> None:
        """Display a frame with host receipt time, not a hardware exposure timestamp."""
        self.lbl_frame_time.setText(f"Frame received (UTC): {datetime.now(timezone.utc).isoformat(timespec='milliseconds')}")
        self._update_display(frame)

    def _on_preview_task_finished(self) -> None:
        if self.active_preview_task is not None:
            self.active_preview_task.deleteLater()
            self.active_preview_task = None
        if self._live_requested:
            QTimer.singleShot(30, self._continue_live)
            return
        self.btn_connect.setEnabled(True)
        self.spn_exposure.setEnabled(True)
        if self.camera.is_connected and (self.active_scan_task is None or not self.active_scan_task.isRunning()):
            self.btn_preview.setEnabled(True)
            self.btn_take_measurement.setEnabled(True)
            self.lbl_task_status.setText("System Status: Idle / Ready")
            self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #198754;")

    def _continue_live(self) -> None:
        """Schedule the next frame only while live viewing is requested."""
        if self._live_requested:
            self._capture_preview()
        else:
            self._on_preview_task_finished()

    def _toggle_measurement_scan(self) -> None:
        """
        Start full multi-step scan sequence until its end,
        continue a stopped measurement from current step,
        or stop an in-progress scan gracefully.
        """
        if not self.camera.is_connected:
            return

        # If scan is already running, this button functions as "Stop Measurement"
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self.btn_take_measurement.setEnabled(False)
            self.btn_take_measurement.setText("Stopping...")
            self.lbl_task_status.setText("System Status: Abort requested. Finalizing current step...")
            self._append_log("[ABORT] User requested scan stop. Finalizing current step before stopping...")
            self.active_scan_task.request_abort()
            return

        # Determine start step: resume from stopped step if available, else start from 0
        start_step = self._paused_step if self._paused_step is not None else 0

        # Prepare storage directory
        target_dir = Path(self.txt_storage_dir.text().strip()).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        self.scan_manager.set_storage_dir(target_dir)

        # Set UI to running scan state
        self._set_ui_scanning_state(is_scanning=True)

        exp_name = self.txt_file_header.text().strip() or "HHG_Scan"
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        start_val = self.spn_start_val.value()
        step_size = self.spn_step_size.value()
        num_steps = self.spn_num_steps.value()
        num_frames = self.spn_frames.value()
        end_val = self.spn_end_val.value()
        curr_val = start_val + start_step * step_size

        # Immediately update progress and status the instant the button is pressed
        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {start_step + 1} of {num_steps} (Acquiring...) | {param_name}: {curr_val:.4f}"
        )
        self.lbl_task_status.setText(
            f"System Status: In Progress [Acquiring Step {start_step + 1} of {num_steps}]..."
        )
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #0d6efd;")

        if start_step > 0:
            self._append_log(
                f"[SCAN RESUME] Continuing scan from Step {start_step} (Step {start_step + 1} of {num_steps}) "
                f"at {param_name} = {curr_val:.4f}.\n"
                f"              Retaking this step from the start and progressing through Step {num_steps}."
            )
        else:
            self._append_log(
                f"[SCAN START] Initiating automated scan: {num_steps} steps from {start_val:.4f} "
                f"to {end_val:.4f} (Step Size: {step_size:.4f})\n"
                f"             Scanning parameter: '{param_name}' | Measurements per step: {num_frames}"
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
        """Update button styles and lock/unlock configuration widgets during scans."""
        if is_scanning:
            self.btn_take_measurement.setText("Stop Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #dc3545; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setToolTip("Safely abort the experiment scan after the current step.")
            self.btn_take_measurement.setToolTip("Safely stop the experiment scan after the current step.")
            self.btn_take_measurement.setEnabled(True)

            self.btn_cut_measurement.setVisible(False)
            self.btn_preview.setEnabled(False)
            self.btn_connect.setEnabled(False)
            self.spn_exposure.setEnabled(False)
            self.spn_frames.setEnabled(False)
            self.spn_start_val.setEnabled(False)
            self.spn_end_val.setEnabled(False)
            self.spn_step_size.setEnabled(False)
            self.spn_num_steps.setEnabled(False)
            self.txt_storage_dir.setEnabled(False)
            self.txt_file_header.setEnabled(False)
            self.txt_scan_param.setEnabled(False)
            self.btn_browse.setEnabled(False)

            self.lbl_task_status.setText("System Status: Scan in Progress...")
            self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #0d6efd;")
        else:
            self.btn_take_measurement.setText("Take Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setToolTip("Start automated experiment scan through all steps until the end.")
            self.btn_take_measurement.setEnabled(self.camera.is_connected)

            self.btn_connect.setEnabled(True)
            self.btn_preview.setEnabled(self.camera.is_connected)
            self.btn_connect.setEnabled(True)
            self.spn_exposure.setEnabled(True)
            self.spn_frames.setEnabled(True)
            self.spn_start_val.setEnabled(True)
            self.spn_end_val.setEnabled(True)
            self.spn_step_size.setEnabled(True)
            self.spn_num_steps.setEnabled(True)
            self.txt_storage_dir.setEnabled(True)
            self.txt_file_header.setEnabled(True)
            self.txt_scan_param.setEnabled(True)
            self.btn_browse.setEnabled(True)

    def _on_scan_step_started(
        self,
        step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str
    ) -> None:
        """Immediately update progress line before camera exposure begins."""
        self._current_executing_step = step_idx
        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {step_idx + 1} of {total_steps} (Acquiring...) | {param_name}: {param_val:.4f}"
        )
        self.lbl_task_status.setText(
            f"System Status: In Progress [Acquiring Step {step_idx + 1} of {total_steps}]..."
        )
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #0d6efd;")

    def _on_scan_step_completed(
        self,
        step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str,
        latest_frame: np.ndarray,
        h5_path: Path
    ) -> None:
        """Handle completion of an individual scan step."""
        file_size_mb = h5_path.stat().st_size / (1024 * 1024)
        self._append_log(
            f"[FILE SAVED] Step {step_idx}/{total_steps - 1} written to: {h5_path.resolve()}\n"
            f"             Size: {file_size_mb:.2f} MB | {self.spn_frames.value()} frames | "
            f"{param_name} = {param_val:.4f}"
        )
        self._update_display(latest_frame)

        self.lbl_scan_progress.setText(
            f"Scan Progress: Step {step_idx + 1} of {total_steps} | {param_name}: {param_val:.4f}"
            f"Scan Progress: Step {step_idx + 1} of {total_steps} (Saved) | {param_name}: {param_val:.4f}"
        )

        if step_idx + 1 < total_steps:
            self.lbl_task_status.setText(
                f"System Status: In Progress [Measuring Step {step_idx + 2} of {total_steps}]..."
            )

    def _on_scan_finished(self, total_steps: int) -> None:
        """Called when all steps in the scan sequence finish."""
        self._paused_step = None
        end_val = self.spn_end_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Completed all {total_steps} steps | {param_name}: {end_val:.4f}"
        )
        self.lbl_task_status.setText(f"System Status: Scan Completed Successfully ({total_steps} steps saved)")
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #198754;")
        self._append_log(f"[SCAN COMPLETE] All {total_steps} planned steps successfully measured and saved to disk.")

    def _on_scan_aborted(self, steps_done: int) -> None:
        """Called when scan is safely stopped before reaching the end."""
        total_steps = self.spn_num_steps.value()
    def _on_scan_aborted(
        self,
        stopped_step_idx: int,
        total_steps: int,
        param_val: float,
        param_name: str
    ) -> None:
        """Called when scan is stopped before reaching the end."""
        self._paused_step = stopped_step_idx
        self.lbl_scan_progress.setText(
            f"Scan Progress: Stopped at {steps_done} of {total_steps} steps"
            f"Scan Progress: Stopped at Step {stopped_step_idx + 1} of {total_steps} | {param_name}: {param_val:.4f}"
        )
        self.lbl_task_status.setText(f"System Status: Scan Stopped by User ({steps_done} steps saved)")
        self.lbl_task_status.setText(
            f"System Status: Scan Stopped at Step {stopped_step_idx + 1} of {total_steps}"
        )
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #d97706;")
        self._append_log(f"[SCAN ABORTED] Scan stopped by user after step {steps_done - 1} ({steps_done} files saved).")
        self._append_log(
            f"[SCAN STOPPED] Scan stopped at Step {stopped_step_idx} (Step {stopped_step_idx + 1} of {total_steps}).\n"
            f"              You can continue to retake Step {stopped_step_idx + 1} from the start, or cut the measurement."
        )

    def _cut_measurement(self) -> None:
        """Finalize the scan at the stopped point and reset so the next scan starts from Step 1."""
        stopped = self._paused_step
        self._paused_step = None
        self.btn_cut_measurement.setVisible(False)
        self.btn_take_measurement.setText("Take Measurement")
        self.btn_take_measurement.setStyleSheet(
            "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
        )
        self.btn_take_measurement.setToolTip("Start automated experiment scan through all steps until the end.")

        total_steps = self.spn_num_steps.value()
        start_val = self.spn_start_val.value()
        param_name = self.txt_scan_param.text().strip() or "Setpoint"
        self.lbl_scan_progress.setText(
            f"Scan Progress: Ready (0 of {total_steps} steps) | {param_name}: {start_val:.4f}"
        )
        self.lbl_task_status.setText("System Status: Idle / Ready")
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #198754;")
        self._append_log(
            f"[SCAN FINALIZED] Scan session cut at Step {stopped}. Saved steps preserved. Next scan will start from Step 1."
        )

    def _on_scan_error(self, err_message: str) -> None:
        """Handle unexpected scan error."""
        self._append_log(f"[ERROR] Scan execution error: {err_message}")
        self.lbl_task_status.setText("System Status: Scan Error")
        self.lbl_task_status.setStyleSheet("font-weight: bold; font-size: 12px; color: #dc3545;")
        QMessageBox.warning(self, "Scan Execution Error", f"Scan error occurred:\n\n{err_message}")

    def _on_scan_task_finished(self) -> None:
        """Cleanup thread and restore UI controls."""
        if self.active_scan_task is not None:
            self.active_scan_task.deleteLater()
            self.active_scan_task = None

        total_steps = self.spn_num_steps.value()
        if self._paused_step is not None:
            # We are in a paused/stopped state: configure Continue button and show Cut button
            self.btn_take_measurement.setText(f"Continue from Step {self._paused_step + 1}")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setToolTip(
                f"Retake Step {self._paused_step + 1} from the start and continue the scan to Step {total_steps}."
            )
            self.btn_take_measurement.setEnabled(self.camera.is_connected)

            self.btn_cut_measurement.setVisible(True)
            self.btn_cut_measurement.setEnabled(True)
        else:
            self.btn_take_measurement.setText("Take Measurement")
            self.btn_take_measurement.setStyleSheet(
                "font-weight: bold; font-size: 13px; background-color: #0d6efd; color: white; padding: 8px;"
            )
            self.btn_take_measurement.setToolTip("Start automated experiment scan through all steps until the end.")
            self.btn_take_measurement.setEnabled(self.camera.is_connected)

            self.btn_cut_measurement.setVisible(False)

        self._set_ui_scanning_state(is_scanning=False)

    def _update_display(self, frame: np.ndarray) -> None:
        """Update live image canvas safely without re-creating axes or leaking memory."""
        # Calculate full-frame scientific metrics directly from raw full-resolution data
        c_min = int(frame.min())
        c_max = int(frame.max())
        c_mean = float(frame.mean())

        sat_warning = " [WARNING: SENSOR SATURATION DETECTED!]" if c_max >= 65530 else ""
        sat_color = "red" if sat_warning else "#212529"
        self.lbl_intensity_metrics.setStyleSheet(
            f"font-size: 12px; font-weight: bold; padding: 4px; background: #f8f9fa; color: {sat_color};"
        )
        self.lbl_intensity_metrics.setText(
            f"Pixel Intensity Metrics | Minimum: {c_min:,} ADU | Maximum: {c_max:,} ADU | Mean: {c_mean:,.1f} ADU{sat_warning}"
        )

        # Downsample strictly for UI display to make screen rendering ~5-10x faster.
        # Extent preserves true physical sensor coordinates (0 to 2560 on X, 0 to 2160 on Y).
        downsample_factor = 2  # 1280 x 1080 display matrix
        display_frame = frame[::downsample_factor, ::downsample_factor]
        h_full, w_full = frame.shape
        x0, y0, x1, y1 = self.camera.get_roi()

        if self._image_artist is None:
            self.axis.clear()
            self.axis.set_title("Camera Sensor Monitor (Latest Frame)", fontsize=11, fontweight="bold")
            self.axis.set_xlabel("Sensor X Pixel Index")
            self.axis.set_ylabel("Sensor Y Pixel Index")
            self._image_artist = self.axis.imshow(
                display_frame,
                cmap="viridis",
                origin="upper",
                aspect="equal",
                extent=[x0, x1, y1, y0]
            )
            self._colorbar = self.figure.colorbar(self._image_artist, ax=self.axis, fraction=0.046, pad=0.04)
            self._colorbar.set_label("16-bit Sensor Counts (ADU)", rotation=270, labelpad=15)
            self.figure.tight_layout()
        else:
            self._image_artist.set_data(display_frame)
            self._image_artist.set_extent([x0, x1, y1, y0])
            self._image_artist.set_clim(vmin=max(0, c_min), vmax=max(c_min + 1, c_max))

        if self.color_scale.currentIndex() == 1:
            self._image_artist.set_clim(0, 65535)
        else:
            self._image_artist.set_clim(c_min, max(c_min + 1, c_max))
        self.canvas.draw_idle()

    def _append_log(self, message: str) -> None:
        self.txt_activity_log.append(message)

    def closeEvent(self, event) -> None:
        """Ensure clean shutdown of active threads and hardware handles before closing."""
        self._live_requested = False
        self.btn_live.setText("Start Live")
        if self.active_scan_task is not None and self.active_scan_task.isRunning():
            self.active_scan_task.request_abort()
            event.ignore()
            return
        if self.active_preview_task is not None and self.active_preview_task.isRunning():
            event.ignore()
            return

        if self.camera.is_connected:
            self.camera.close()

        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = CameraMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
