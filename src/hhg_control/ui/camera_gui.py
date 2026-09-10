"""
PyQt6 GUI for pco.edge Camera Control and Scan Sequencer.
Uses a QThread worker so acquisitions never freeze the user interface.
"""

import sys
from pathlib import Path
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QDoubleSpinBox, QSpinBox,
    QPushButton, QFileDialog, QCheckBox, QTextEdit, QMessageBox
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from ..drivers.base_camera import BaseCamera
from ..drivers.mock_camera import MockPcoCamera
from ..sequencer.scan_manager import CameraScanManager


class AcquisitionWorker(QObject):
    finished = pyqtSignal(object, str)
    error = pyqtSignal(str)

    def __init__(self, scan_mgr: CameraScanManager, exp_name: str, step_idx: int,
                 param_name: str, param_val: float, num_frames: int, save_to_disk: bool):
        super().__init__()
        self.scan_mgr = scan_mgr
        self.exp_name = exp_name
        self.step_idx = step_idx
        self.param_name = param_name
        self.param_val = param_val
        self.num_frames = num_frames
        self.save_to_disk = save_to_disk

    def run(self) -> None:
        try:
            if self.save_to_disk:
                path = self.scan_mgr.acquire_and_save_step(
                    self.exp_name, self.step_idx, self.param_name, self.param_val, self.num_frames
                )
                images, _ = self.scan_mgr.camera.acquire_frames(1)
                self.finished.emit(images[0], f"Saved {self.num_frames} frame(s) to: {path.name}")
            else:
                images, _ = self.scan_mgr.camera.acquire_frames(1)
                self.finished.emit(images[0], "Preview frame acquired.")
        except Exception as err:
            self.error.emit(str(err))


class CameraMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HHG Lab - PCO Camera Controller")
        self.resize(1100, 750)

        self.camera: BaseCamera = MockPcoCamera()
        self.scan_manager = CameraScanManager(self.camera, Path.cwd() / "data")
        self.worker_thread: QThread | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        left_panel = QVBoxLayout()
        main_layout.addLayout(left_panel, stretch=1)

        # Hardware Connection Box
        grp_hw = QGroupBox("Hardware Connection")
        lay_hw = QVBoxLayout(grp_hw)
        self.chk_mock = QCheckBox("Use Mock Emulator (Hardware-Free)")
        self.chk_mock.setChecked(True)
        lay_hw.addWidget(self.chk_mock)

        self.btn_connect = QPushButton("Connect Camera")
        self.btn_connect.clicked.connect(self._toggle_connection)
        lay_hw.addWidget(self.btn_connect)
        self.lbl_hw_status = QLabel("Status: Disconnected")
        lay_hw.addWidget(self.lbl_hw_status)
        left_panel.addWidget(grp_hw)

        # Camera Settings Box
        grp_acq = QGroupBox("Camera Parameters")
        lay_acq = QVBoxLayout(grp_acq)
        
        lay_acq.addWidget(QLabel("Exposure Time (ms):"))
        self.spn_exposure = QDoubleSpinBox()
        self.spn_exposure.setRange(0.5, 10000.0)
        self.spn_exposure.setValue(10.0)
        self.spn_exposure.valueChanged.connect(self._on_exposure_changed)
        lay_acq.addWidget(self.spn_exposure)

        lay_acq.addWidget(QLabel("Frames per Acquisition:"))
        self.spn_frames = QSpinBox()
        self.spn_frames.setRange(1, 1000)
        self.spn_frames.setValue(5)
        lay_acq.addWidget(self.spn_frames)

        self.btn_preview = QPushButton("Single Frame Preview")
        self.btn_preview.setEnabled(False)
        self.btn_preview.clicked.connect(lambda: self._start_acquisition(save=False))
        lay_acq.addWidget(self.btn_preview)
        left_panel.addWidget(grp_acq)

        # Scan & Experiment Box
        grp_exp = QGroupBox("Experiment & Step Scan")
        lay_exp = QVBoxLayout(grp_exp)

        lay_exp.addWidget(QLabel("Storage Directory:"))
        lay_dir = QHBoxLayout()
        self.txt_dir = QLineEdit(str(self.scan_manager.storage_dir))
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_dir)
        lay_dir.addWidget(self.txt_dir)
        lay_dir.addWidget(self.btn_browse)
        lay_exp.addLayout(lay_dir)

        lay_exp.addWidget(QLabel("Experiment Name:"))
        self.txt_exp_name = QLineEdit("hhg_scan")
        lay_exp.addWidget(self.txt_exp_name)

        lay_exp.addWidget(QLabel("Scan Parameter Name:"))
        self.txt_param_name = QLineEdit("delay_stage_mm")
        lay_exp.addWidget(self.txt_param_name)

        lay_exp.addWidget(QLabel("Scan Parameter Value:"))
        self.spn_param_val = QDoubleSpinBox()
        self.spn_param_val.setRange(-1e6, 1e6)
        self.spn_param_val.setDecimals(4)
        self.spn_param_val.setValue(0.0)
        lay_exp.addWidget(self.spn_param_val)

        lay_exp.addWidget(QLabel("Step Index:"))
        self.spn_step = QSpinBox()
        self.spn_step.setRange(0, 99999)
        self.spn_step.setValue(0)
        lay_exp.addWidget(self.spn_step)

        self.btn_acquire_save = QPushButton("Acquire & Save Step")
        self.btn_acquire_save.setStyleSheet("font-weight: bold; background-color: #0d6efd; color: white;")
        self.btn_acquire_save.setEnabled(False)
        self.btn_acquire_save.clicked.connect(lambda: self._start_acquisition(save=True))
        lay_exp.addWidget(self.btn_acquire_save)
        left_panel.addWidget(grp_exp)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        left_panel.addWidget(self.txt_log)

        # Image Canvas
        right_panel = QVBoxLayout()
        main_layout.addLayout(right_panel, stretch=2)

        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Camera Frame Monitor")
        right_panel.addWidget(self.canvas)

        self.lbl_metrics = QLabel("Counts: Min: -- | Max: -- | Mean: --")
        self.lbl_metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_panel.addWidget(self.lbl_metrics)

    def _log(self, message: str) -> None:
        self.txt_log.append(message)

    def _browse_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Save Directory", self.txt_dir.text())
        if chosen:
            self.txt_dir.setText(chosen)
            self.scan_manager.set_storage_dir(chosen)

    def _toggle_connection(self) -> None:
        if not self.camera.is_connected:
            use_mock = self.chk_mock.isChecked()
            try:
                if use_mock:
                    self.camera = MockPcoCamera()
                else:
                    from ..drivers.pco_edge import PcoEdgeCamera
                    self.camera = PcoEdgeCamera()
                
                self.camera.connect()
                self.scan_manager.camera = self.camera
                self._on_exposure_changed(self.spn_exposure.value())

                info = self.camera.get_sensor_info()
                self.lbl_hw_status.setText(f"Status: Connected ({info['model']})")
                self.btn_connect.setText("Disconnect Camera")
                self.chk_mock.setEnabled(False)
                self.btn_preview.setEnabled(True)
                self.btn_acquire_save.setEnabled(True)
                self._log(f"Camera connected: {info['model']}, S/N: {info['serial_number']}")
            except Exception as e:
                QMessageBox.critical(self, "Hardware Error", f"Connection failed:\n{e}")
                self._log(f"Connection error: {e}")
        else:
            self.camera.close()
            self.lbl_hw_status.setText("Status: Disconnected")
            self.btn_connect.setText("Connect Camera")
            self.chk_mock.setEnabled(True)
            self.btn_preview.setEnabled(False)
            self.btn_acquire_save.setEnabled(False)
            self._log("Camera disconnected.")

    def _on_exposure_changed(self, value_ms: float) -> None:
        if self.camera.is_connected:
            try:
                self.camera.set_exposure_time(value_ms / 1000.0)
                self._log(f"Exposure time set to {value_ms:.2f} ms")
            except Exception as e:
                self._log(f"Exposure error: {e}")

    def _start_acquisition(self, save: bool) -> None:
        if not self.camera.is_connected:
            return

        self.btn_preview.setEnabled(False)
        self.btn_acquire_save.setEnabled(False)

        self.worker_thread = QThread()
        self.worker = AcquisitionWorker(
            scan_mgr=self.scan_manager,
            exp_name=self.txt_exp_name.text(),
            step_idx=self.spn_step.value(),
            param_name=self.txt_param_name.text(),
            param_val=self.spn_param_val.value(),
            num_frames=self.spn_frames.value(),
            save_to_disk=save
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_acquisition_finished)
        self.worker.error.connect(self._on_acquisition_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker_thread.start()

    def _on_acquisition_finished(self, frame_2d: np.ndarray, msg: str) -> None:
        self.btn_preview.setEnabled(True)
        self.btn_acquire_save.setEnabled(True)
        self._log(msg)

        if "Saved" in msg:
            self.spn_step.setValue(self.spn_step.value() + 1)

        self._update_display(frame_2d)

    def _on_acquisition_error(self, err_msg: str) -> None:
        self.btn_preview.setEnabled(True)
        self.btn_acquire_save.setEnabled(True)
        self._log(f"Error: {err_msg}")
        QMessageBox.warning(self, "Acquisition Failure", err_msg)

    def _update_display(self, frame: np.ndarray) -> None:
        c_min, c_max, c_mean = int(frame.min()), int(frame.max()), float(frame.mean())
        sat = " [SATURATED!]" if c_max >= 65530 else ""
        self.lbl_metrics.setText(f"Counts: Min: {c_min} | Max: {c_max} | Mean: {c_mean:.1f}{sat}")

        self.ax.clear()
        self.ax.imshow(frame, cmap="viridis", origin="upper")
        self.ax.set_title("Latest Camera Frame")
        self.fig.tight_layout()
        self.canvas.draw()

    def closeEvent(self, event) -> None:
        if self.camera.is_connected:
            self.camera.close()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    win = CameraMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

