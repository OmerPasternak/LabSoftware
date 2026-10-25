"""Diagnostics window and asynchronous camera-delay characterization orchestration."""

from datetime import datetime, timezone
import json
from threading import Event
import sys
from uuid import uuid4

from PyQt6.QtCore import QObject, QProcess, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout, QPushButton

from hhg_control.camera.sequencer.delay_characterization import (
    acquire_delay_reference, characterize_delay,
)
from hhg_control.camera.sequencer.scan_manager import AcquisitionAborted
from hhg_control.project_paths import project_root


class DelayTask(QThread):
    """Run one sequencer phase away from Qt's UI thread; STOP is a thread event."""

    log = pyqtSignal(str)

    def __init__(self, operation, stop_event):
        """Store a reference/measurement operation accepting abort and log callbacks."""
        super().__init__()
        self.operation, self.stop_event = operation, stop_event
        self.result = None
        self.error = None
        self.aborted = False

    def run(self):
        """Execute once, retaining outcome until the finished signal is handled."""
        try:
            self.result = self.operation(self.stop_event.is_set, self.log.emit)
        except AcquisitionAborted:
            self.aborted = True
        except Exception as exc:
            self.error = str(exc)


class DelayDiagnostic(QObject):
    """Coordinate reference → existing rectangle-selector script → measurement → plot."""

    def __init__(self, window):
        """Keep worker/process ownership in the camera window's lifetime."""
        super().__init__(window)
        self.window = window
        self.active = False
        self.task = None
        self.selector = None
        self.stop_event = Event()

    def start(self):
        """Start with the current source/settings, requiring only a harmonic rectangle."""
        w = self.window
        if self.active or w._configuration_busy() or w.active_scan_task or w.active_preview_task or w._hardware_state_unknown:
            w._append_log("[DELAY] Finish the active camera operation before starting characterization.")
            return
        if not w.camera.is_connected:
            w._connect_camera(after_connect=self.start)
            return
        self.active = True
        self.stop_event.clear()
        self.count = 2000
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
        self.window_path = w.scan_manager.storage_dir / f"camera_delay_{self.run_id}_window.json"
        w._clear_paused_scan("Starting camera delay characterization")
        w.control_panel.setEnabled(False)
        w.btn_go.setEnabled(False)
        w.cmb_camera_source.setEnabled(False)
        w.btn_camera_delay.setEnabled(False)
        if w._is_live_active or w.active_live_task is not None:
            w._stop_live(after_stop=self._reference)
        else:
            self._reference()

    def _reference(self):
        """Save a fresh reference with the existing acquisition writer."""
        if self.stop_event.is_set() or self.window._closing:
            self._finish()
            return
        self.window.lbl_system_status.setText("Status: Delay diagnostic — reference; STOP cancels")
        self._run(lambda abort, log: acquire_delay_reference(
            self.window.scan_manager, self.run_id, self.count, abort, log,
            start_message=(f"[DELAY][{self.window._scan_source_label()}] Starting camera-delay "
                           f"characterization; 1 measurement step per delay, {self.count} frames/step.")),
            self._select)

    def _run(self, operation, on_success):
        """Hold a worker until it has finished restoring camera state."""
        self.task = DelayTask(operation, self.stop_event)
        self.task.log.connect(self._append_task_log)
        self.task.finished.connect(lambda: self._task_finished(on_success))
        self.task.start()

    def _append_task_log(self, message):
        """Keep the estimate directly above the delay-start marker in the visible log."""
        if message.startswith("Estimated characterization time:") or message.startswith("[DELAY]"):
            self.window._append_log(message)
        else:
            self.window._append_log(f"[DELAY] {message}")

    def _task_finished(self, on_success):
        """Advance only after cleanup; keep failures and STOP from opening a result graph."""
        task, self.task = self.task, None
        task.deleteLater()
        if task.error:
            self.window._append_log(f"[DELAY ERROR] {task.error}")
            self.window._hardware_state_unknown = bool(getattr(self.window.camera, "state_unknown", False))
            self._finish()
        elif task.aborted or self.stop_event.is_set() or self.window._closing:
            self._finish()
        else:
            on_success(task.result)

    def _select(self, reference):
        """Reuse analyze_harmonic_intensity.py selection in an independent Qt process."""
        self.window.lbl_system_status.setText("Status: Draw one harmonic window in the selection window")
        process = QProcess(self)
        self.selector = process
        process.setProgram(sys.executable)
        process.setArguments([str(project_root() / "scripts" / "analyze_harmonic_intensity.py"),
                              str(reference), "--select-only", "--window", str(self.window_path)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self.window._read_analysis_output(process))
        process.finished.connect(self._selected)
        process.errorOccurred.connect(self._selector_error)
        process.start()

    def _selector_error(self, error):
        """Release UI ownership if Python/selector cannot start."""
        if error == QProcess.ProcessError.FailedToStart:
            self.window._append_log(f"[DELAY ERROR] Selector could not start: {self.selector.errorString()}")
            self.selector.deleteLater()
            self.selector = None
            self._finish()

    def _selected(self, exit_code, _status):
        """Load the selected rectangle and automatically start all delay measurements."""
        process, self.selector = self.selector, None
        self.window._read_analysis_output(process)
        process.deleteLater()
        if exit_code or not self.window_path.exists() or self.stop_event.is_set() or self.window._closing:
            self.window._append_log("[DELAY] Window selection cancelled or failed; no delay scan started.")
            self._finish()
            return
        try:
            with self.window_path.open(encoding="utf-8") as handle:
                selected_window = json.load(handle)
        except Exception as exc:
            self.window._append_log(f"[DELAY ERROR] Cannot read selected rectangle: {exc}")
            self._finish()
            return
        self.window.lbl_system_status.setText("Status: Measuring 15 camera delays — STOP cancels")
        self._run(lambda abort, log: characterize_delay(
            self.window.scan_manager, self.run_id, self.count, selected_window, abort, log), self._plot)

    def _plot(self, report):
        """Release camera controls and pop up the saved Python result graph."""
        self.window._append_log(f"[DELAY COMPLETE] Original camera delay/trigger restored. Results: {report}")
        self._finish()
        self.window._launch_analysis_script("plot_camera_delay.py", report)

    def stop(self):
        """Request cooperative capture cancellation or close the selection process."""
        self.stop_event.set()
        if self.selector is not None:
            self.selector.kill()
        elif self.task is None:
            self.window._after_live_stopped = None
            self._finish()

    def _finish(self):
        """Restore UI availability after workers release hardware ownership."""
        self.active = False
        w = self.window
        w.control_panel.setEnabled(True)
        w.btn_go.setEnabled(not w._hardware_state_unknown)
        w.cmb_camera_source.setEnabled(not w._hardware_state_unknown and w.active_live_task is None)
        w.btn_camera_delay.setEnabled(True)
        if w._hardware_state_unknown:
            w._configuration_controls(False)
            w.lbl_system_status.setText("Status: Camera state unknown — disconnect and reconnect")
        else:
            w.lbl_system_status.setText("Status: Delay diagnostic idle")
        if self.stop_event.is_set():
            w._append_log("[DELAY] Stopped. Raw files and completed point results retained.")
        if w._closing:
            QTimer.singleShot(0, w.close)


def build_diagnostics_window(window):
    """Move saved-analysis buttons into an extensible, separate diagnostics window."""
    dialog = QDialog(window)
    dialog.setWindowTitle("Camera Diagnostics")
    dialog.resize(420, 250)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("Saved-data analysis"))
    for button in (window.btn_spectral_covariance, window.btn_mark_harmonics, window.btn_harmonic_g2):
        layout.addWidget(button)
    layout.addWidget(QLabel("Camera characterization"))
    window.btn_camera_delay = QPushButton("Camera delay characterization")
    window.btn_camera_delay.setToolTip("Choose one harmonic rectangle; measure 5 delays at 0–100 ns and 10 at 1900–2100 ns.")
    layout.addWidget(window.btn_camera_delay)
    layout.addStretch(1)
    window.delay_diagnostic = DelayDiagnostic(window)
    window.btn_camera_delay.clicked.connect(window.delay_diagnostic.start)
    return dialog
