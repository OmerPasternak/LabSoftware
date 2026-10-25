"""Separate Qt scan window; every instrument operation belongs to its worker."""

import sys
import json
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from hhg_control.camera.drivers.base_camera import ReadoutMode, TriggerMode
from hhg_control.camera.drivers.camera_defaults import DEFAULT_CAMERA_SERIAL
from .plan import ScanPlan, Visit
from .session import ScanSession, ScanSettings


class ScanWorker(QThread):
    """Own both instruments; STOP only sets events across threads, never performs I/O."""

    event = pyqtSignal(object)
    message = pyqtSignal(str)
    cleanup = pyqtSignal(bool)
    failed = pyqtSignal()

    def __init__(self, settings, plan, resume_directory=None, *, session_factory=ScanSession, parent=None):
        """Copy immutable run inputs without connecting instruments."""
        super().__init__(parent)
        self.settings, self.plan, self.resume_directory = settings, plan, resume_directory
        self.session_factory = session_factory
        self.cancel = Event()
        self.session = None

    def request_stop(self):
        """Latch STOP immediately, including construction/connection/startup races."""
        self.cancel.set()
        session = self.session
        if session is not None:
            session.scan.request_stop()

    def run(self):
        """Connect, scan and clean up on this thread; retain the session if cleanup is uncertain."""
        safe = False
        try:
            self.session = self.session_factory(self.settings)
            if self.cancel.is_set():
                self.session.scan.request_stop()
            self.session.connect(self.cancel.is_set)
            directory = self.session.scan.run(self.plan, resume_directory=self.resume_directory,
                                              on_event=self.event.emit)
            with open(directory / "run.json", encoding="utf-8") as handle:
                performance = json.load(handle)["performance"]
            self.message.emit(
                f"COMPLETE: {directory}\nWhole-scan effective rate: {performance['effective_fps']:.3f} fps "
                f"({performance['verified_frames']} verified frames / "
                f"{performance['active_execution_s']:.2f} s, including motor actions)"
            )
        except Exception as error:
            self.failed.emit()
            self.message.emit(f"STOPPED / FAILED: {error}")
            if self.session is not None and self.session.scan.last_run_dir:
                self.message.emit(f"Retained run: {self.session.scan.last_run_dir}")
        finally:
            if self.session is not None:
                try:
                    self.session.close()
                    safe = True
                except Exception as error:
                    self.message.emit(str(error))
            else:
                safe = True
            self.cleanup.emit(safe)


class CleanupWorker(QThread):
    """Retry retained-session cleanup without blocking Qt or opening a new session."""

    cleanup = pyqtSignal(bool)
    message = pyqtSignal(str)

    def __init__(self, session, parent=None):
        """Retain the original instrument owners until cleanup has succeeded."""
        super().__init__(parent)
        self.session = session

    def run(self):
        """Retry stop/disconnect serially after the scan worker has ended."""
        try:
            self.session.close()
        except Exception as error:
            self.message.emit(str(error))
            self.cleanup.emit(False)
        else:
            self.cleanup.emit(True)


class ScanPanel(QWidget):
    """Standalone scan controls; the camera and motor windows keep their existing layouts."""

    def __init__(self, *, session_factory=ScanSession, parent=None):
        """Build disconnected in Mock mode; constructing the window never touches hardware."""
        super().__init__(parent)
        if sys.platform == "win32":
            self.setFont(QFont("Segoe UI", 10))
        self.setWindowTitle("Scan — motor and camera")
        self.resize(790, 900)
        self.session_factory, self.worker, self.retry_worker = session_factory, None, None
        self.safe, self.closing = True, False
        self.stop_latched = False
        layout = QVBoxLayout(self)
        self.banner = QLabel("SIMULATION — project motor and camera mocks. Values below are a demonstration.")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.mode = QComboBox()
        self.mode.addItems(["Mock", "Physical"])
        self.resource, self.profile = QLineEdit(), QLineEdit()
        self.resource.setPlaceholderText("Explicit VISA resource, e.g. ASRL21::INSTR")
        self.profile.setPlaceholderText("Reviewed motor safety profile JSON")
        self.axis = QSpinBox()
        self.axis.setRange(1, 31)
        self.axis.setValue(1)
        self.serial = QLineEdit(DEFAULT_CAMERA_SERIAL)
        self.reviewed = QCheckBox("Reviewed setup, calibration and mounted limits")
        for label, field in (("Mode", self.mode), ("Motor resource", self.resource),
            ("Motor axis", self.axis), ("Motor safety profile", self.profile), ("Camera serial", self.serial),
            ("Physical review", self.reviewed)):
            form.addRow(label, field)
        self.positions = QPlainTextEdit("0.0, reference\n0.1, signal\n0.2, signal\n0.1, signal")
        self.positions.setMaximumHeight(115)
        form.addRow("Ordered visits: absolute mm, label\nOne per line; reverse/repeat visits are retained", self.positions)
        self.frames, self.repeats = QSpinBox(), QSpinBox()
        self.frames.setRange(1, 9_999_999)
        self.frames.setValue(10)
        self.repeats.setRange(1, 10_000)
        self.repeats.setValue(1)
        form.addRow("Frames per position", self.frames)
        form.addRow("Repeat entire ordered path", self.repeats)
        self.exposure = self._number(.5, 2000, 10, " ms", 3)
        self.trigger, self.readout = QComboBox(), QComboBox()
        for value in TriggerMode:
            self.trigger.addItem(value.value, value)
        self.trigger.setCurrentIndex(self.trigger.findData(TriggerMode.EXTERNAL_EXPOSURE_START))
        self.enable_input = QCheckBox("Enable input (keep edge polarity)")
        self.enable_input.setChecked(True)
        for value in ReadoutMode:
            self.readout.addItem(value.name, value)
        self.roi = QLineEdit("0,1072,2560,1088")
        self.roi.setPlaceholderText("x0,y0,x1,y1; blank keeps the connected ROI")
        form.addRow("Exposure", self.exposure)
        form.addRow("Trigger mode (external requires input pulses)", self.trigger)
        form.addRow("External input", self.enable_input)
        form.addRow("Shutter", self.readout)
        form.addRow("Raw hardware ROI pixels", self.roi)
        self.settle = self._number(0, 3600, .1, " s")
        self.tolerance = self._number(.000001, 50, .001, " mm", 6)
        self.motion = self._number(.1, 86400, 30, " s")
        self.capture = self._number(.1, 86400, 60, " s")
        self.storage = self._number(.1, 86400, 120, " s")
        self.cleanup_time = self._number(.1, 600, 10, " s")
        for label, field in (("Stationary settling", self.settle), ("Arrival/readback tolerance", self.tolerance),
            ("Motion + settle timeout", self.motion), ("Recording timeout", self.capture),
            ("Sync/verification timeout", self.storage), ("Camera cleanup timeout", self.cleanup_time)):
            form.addRow(label, field)
        self.zero, self.scale = QLineEdit(), QLineEdit()
        self.zero.setPlaceholderText("Optional calibrated zero position in mm")
        self.scale.setPlaceholderText("Optional signed fs/mm; geometry is not assumed")
        form.addRow("Optical zero (optional)", self.zero)
        form.addRow("Optical calibration (optional)", self.scale)
        self.output, self.resume = QLineEdit(), QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output)
        output_row.addWidget(browse)
        form.addRow("Output parent directory", output_row)
        form.addRow("Recover run folder (optional; identical settings)", self.resume)
        form.addRow(QLabel("The motor remains at its last position. No home, zero or automatic return."))
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and isinstance(item.widget(), QLabel):
                label = item.widget()
                label.setFixedWidth(250)
                label.setWordWrap(True)
                label.setMinimumHeight(label.heightForWidth(250))
        self.start, self.stop, self.retry = QPushButton("Start scan"), QPushButton("STOP"), QPushButton("Retry cleanup")
        self.acknowledge = QPushButton("Acknowledge STOP / failure")
        self.acknowledge.clicked.connect(self._acknowledge)
        self.acknowledge.setEnabled(False)
        self.start.clicked.connect(self._start)
        self.stop.clicked.connect(self._stop)
        self.retry.clicked.connect(self._retry)
        self.stop.setEnabled(False)
        self.retry.setEnabled(False)
        actions = QHBoxLayout()
        for button in (self.start, self.stop, self.acknowledge, self.retry):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log)
        self.body = body
        self.mode.currentTextChanged.connect(self._mode)

    @staticmethod
    def _number(low, high, value, suffix, decimals=3):
        """Build an explicit unit editor; editing never sends a device command."""
        field = QDoubleSpinBox()
        field.setDecimals(decimals)
        field.setRange(low, high)
        field.setValue(value)
        field.setSuffix(suffix)
        field.setKeyboardTracking(False)
        return field

    def _mode(self, mode):
        """Clear demonstration coordinates before a physical plan can be entered."""
        self.reviewed.setChecked(False)
        self.enable_input.setChecked(False)
        self.positions.clear()
        self.zero.clear()
        self.scale.clear()
        self.roi.clear()
        self.banner.setText("PHYSICAL — enter reviewed positions/settings and a calibrated motor safety profile. "
                            "Close other instrument-control sessions before Start."
                            if mode == "Physical" else "SIMULATION — project mocks; no physical delay response.")

    def _browse(self):
        """Select the output parent directory without starting a scan."""
        path = QFileDialog.getExistingDirectory(self, "Scan output directory")
        if path:
            self.output.setText(path)

    def inputs(self):
        """Parse explicit ordered visits and visible settings without device I/O."""
        visits = []
        for line in self.positions.toPlainText().splitlines():
            if line.strip():
                position, _, label = line.partition(",")
                visits.append(Visit(float(position.strip()), label.strip() or "signal"))
        plan = ScanPlan(tuple(visits), self.frames.value(), self.settle.value(), self.tolerance.value(),
            repeats=self.repeats.value(), motion_timeout_s=self.motion.value(), capture_timeout_s=self.capture.value(),
            storage_timeout_s=self.storage.value(), cleanup_timeout_s=self.cleanup_time.value(),
            zero_mm=float(self.zero.text()) if self.zero.text().strip() else None,
            fs_per_mm=float(self.scale.text()) if self.scale.text().strip() else None)
        roi = tuple(int(value.strip()) for value in self.roi.text().split(",")) if self.roi.text().strip() else None
        if roi is not None and len(roi) != 4:
            raise ValueError("ROI requires exactly x0,y0,x1,y1.")
        settings = ScanSettings(self.output.text().strip(), mode=self.mode.currentText(),
            motor_resource=self.resource.text().strip(), motor_axis=self.axis.value(),
            motor_profile=self.profile.text().strip(), camera_serial=self.serial.text().strip(),
            exposure_s=self.exposure.value() / 1000, trigger=self.trigger.currentData(),
            readout=self.readout.currentData(), roi=roi, physical_reviewed=self.reviewed.isChecked(),
            enable_external_input=self.enable_input.isChecked())
        return settings, plan

    def _start(self):
        """Launch one worker from reviewed form inputs; edits remain disabled through cleanup."""
        if not self.safe or self.worker is not None and self.worker.isRunning():
            return
        try:
            settings, plan = self.inputs()
            if not settings.output_directory:
                raise ValueError("Select an output parent directory.")
        except Exception as error:
            self.log.appendPlainText(str(error))
            return
        self.safe = False
        self.body.setEnabled(False)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker = ScanWorker(settings, plan, self.resume.text().strip() or None,
                                 session_factory=self.session_factory, parent=self)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.failed.connect(self._latch)
        self.worker.event.connect(lambda event: self.log.appendPlainText(str(event)))
        self.worker.cleanup.connect(self._cleaned)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _stop(self):
        """Latch STOP while the worker retains ownership and drains/cleans up."""
        if self.worker is not None:
            self._latch()
            self.worker.request_stop()
            self.log.appendPlainText("STOP latched; retaining returned frames and waiting for cleanup.")

    def _latch(self):
        """Require explicit user acknowledgement after STOP/failure and successful cleanup."""
        self.stop_latched = True

    def _acknowledge(self):
        """Permit a fresh session only after all previous instrument cleanup succeeded."""
        if self.safe and not (self.worker is not None and self.worker.isRunning()):
            self.stop_latched = False
            self.acknowledge.setEnabled(False)
            self.start.setEnabled(True)
            self.log.appendPlainText("STOP acknowledged; the next Start performs fresh preflight.")

    def _cleaned(self, safe):
        """Retain cleanup uncertainty; only a finished worker can re-enable Start or close."""
        self.safe = safe

    def _finished(self):
        """Update controls after the owner thread ended; never destroy a running QThread."""
        self.stop.setEnabled(False)
        self.body.setEnabled(self.safe)
        self.start.setEnabled(self.safe and not self.stop_latched)
        self.acknowledge.setEnabled(self.safe and self.stop_latched)
        self.retry.setEnabled(not self.safe)
        if self.safe and self.closing:
            self.close()

    def _retry(self):
        """Retry cleanup for the retained session, preserving the close guard on failure."""
        if self.worker is None or self.worker.session is None or self.worker.isRunning():
            return
        self.retry.setEnabled(False)
        self.retry_worker = CleanupWorker(self.worker.session, self)
        self.retry_worker.message.connect(self.log.appendPlainText)
        self.retry_worker.cleanup.connect(self._cleaned)
        self.retry_worker.finished.connect(self._finished)
        self.retry_worker.start()

    def closeEvent(self, event):
        """Keep the window alive until all worker and instrument cleanup has succeeded."""
        running = (self.worker is not None and self.worker.isRunning()) or (
            self.retry_worker is not None and self.retry_worker.isRunning())
        if running or not self.safe:
            self.closing = True
            self._stop()
            event.ignore()
        else:
            event.accept()


def main():
    """Open the separate disconnected scan window without instrument discovery."""
    app = QApplication.instance() or QApplication(sys.argv)
    panel = ScanPanel()
    panel.show()
    return app.exec()
