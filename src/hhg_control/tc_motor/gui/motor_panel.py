"""Independent LTA-HS / SMC100 window with explicit mock/physical selection."""

import sys
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from .workers import ConnectionSettings, ConnectionState, MotorWorker, create_sequencer


class MotorPanel(QWidget):
    """Thin controls over a background sequencer; never commands a driver itself."""

    def __init__(self, *, sequencer_factory=create_sequencer, parent=None):
        """Build disconnected in mock mode; no hardware discovery or connection."""
        super().__init__(parent)
        self.setWindowTitle("TC motor — Newport LTA-HS / SMC100")
        self.resize(720, 780)
        self._busy = False
        self._connection = ConnectionState(False, False, False, None)
        self._status = None
        self._stop_latched = False
        self._closing = False
        self._safe_to_close = False
        self._cleanup_uncertain = False
        self._units_verified = False
        self._build_ui()
        self.worker = MotorWorker(sequencer_factory, self)
        self.worker.status_ready.connect(self._show_status)
        self.worker.info_ready.connect(self._show_info)
        self.worker.connection_changed.connect(self._show_connection)
        self.worker.busy_changed.connect(self._show_busy)
        self.worker.stop_latched.connect(self._show_stop_latch)
        self.worker.activity.connect(self._show_activity)
        self.worker.error.connect(self._show_error)
        self.worker.cleanup_done.connect(self._cleanup_finished)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self._mode_changed()

    @staticmethod
    def _spin(decimals, minimum, maximum, value, suffix):
        """Create an explicitly unit-labelled numeric editor without implicit moves."""
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setKeyboardTracking(False)
        return widget

    def _build_ui(self) -> None:
        """Construct the separate motor interface without importing camera UI."""
        layout = QVBoxLayout(self)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("padding: 10px; font-weight: bold; background: #fff2b2; color: #222;")
        layout.addWidget(self.banner)

        connection_group = QGroupBox("Connection")
        form = QFormLayout(connection_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Mock", "Physical"])
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        form.addRow("Mode", self.mode_combo)
        self.com_port_spin = QSpinBox()
        self.com_port_spin.setRange(1, 65535)
        self.com_port_spin.setValue(21)
        self.com_port_spin.setToolTip("Enter only the Windows COM port number, for example 21 for COM21.")
        form.addRow("COM port number", self.com_port_spin)
        self.axis_spin = QSpinBox()
        self.axis_spin.setRange(1, 31)
        self.axis_spin.setValue(1)
        form.addRow("Controller address", self.axis_spin)
        profile_row = QHBoxLayout()
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("Optional reviewed JSON profile; absent = inspection only")
        self.profile_edit.textChanged.connect(self._mode_changed)
        self.profile_button = QPushButton("Browse…")
        self.profile_button.clicked.connect(self._browse_profile)
        profile_row.addWidget(self.profile_edit)
        profile_row.addWidget(self.profile_button)
        form.addRow("Physical safety profile", profile_row)
        connection_buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_motor)
        self.disconnect_button = QPushButton("Stop && disconnect")
        self.disconnect_button.clicked.connect(self._disconnect_motor)
        connection_buttons.addWidget(self.connect_button)
        connection_buttons.addWidget(self.disconnect_button)
        form.addRow(connection_buttons)
        self.recovery_button = QPushButton("Release COM port after physical standstill check…")
        self.recovery_button.clicked.connect(self._release_after_manual_verification)
        self.recovery_button.setVisible(False)
        form.addRow(self.recovery_button)
        self.report_button = QPushButton("Save setup report…")
        self.report_button.clicked.connect(self._save_setup_report)
        form.addRow(self.report_button)
        layout.addWidget(connection_group)

        self.position_label = QLabel("— mm")
        self.position_label.setFont(QFont("Consolas", 23, QFont.Weight.Bold))
        self.position_label.setWordWrap(True)
        layout.addWidget(self.position_label)
        self.identity_label = QLabel("Disconnected")
        self.identity_label.setWordWrap(True)
        self.state_label = QLabel("No session")
        self.state_label.setWordWrap(True)
        self.limits_label = QLabel("Mock demonstration limits: 0–10 mm; maximum move 2 mm.")
        self.limits_label.setWordWrap(True)
        layout.addWidget(self.identity_label)
        layout.addWidget(self.state_label)
        layout.addWidget(self.limits_label)

        motion_group = QGroupBox("Mechanical position — mm")
        motion_form = QFormLayout(motion_group)
        self.target_spin = self._spin(6, -1e6, 1e6, 0, " mm")
        self.move_button = QPushButton("Move to target")
        self.move_button.clicked.connect(lambda: self._move("move_to", self.target_spin.value()))
        absolute_row = QHBoxLayout()
        absolute_row.addWidget(self.target_spin)
        absolute_row.addWidget(self.move_button)
        motion_form.addRow("Absolute target", absolute_row)
        self.step_spin = self._spin(6, .000001, 50, .1, " mm")
        self.minus_button = QPushButton("− Step")
        self.plus_button = QPushButton("+ Step")
        self.minus_button.clicked.connect(lambda: self._move("move_relative", -self.step_spin.value()))
        self.plus_button.clicked.connect(lambda: self._move("move_relative", self.step_spin.value()))
        relative_row = QHBoxLayout()
        relative_row.addWidget(self.step_spin)
        relative_row.addWidget(self.minus_button)
        relative_row.addWidget(self.plus_button)
        motion_form.addRow("Relative step", relative_row)
        self.timeout_spin = self._spin(1, .1, 600, 30, " s")
        self.settle_spin = self._spin(2, 0, 60, 0, " s")
        motion_form.addRow("Completion timeout", self.timeout_spin)
        motion_form.addRow("Stationary settling time", self.settle_spin)
        layout.addWidget(motion_group)

        self.stop_button = QPushButton("STOP — keep servo holding")
        self.stop_button.setMinimumHeight(48)
        self.stop_button.setStyleSheet("font-weight: bold; font-size: 16px; background: #b42318; color: white;")
        self.stop_button.clicked.connect(self._stop_motor)
        self.resume_button = QPushButton("Resume after STOP")
        self.resume_button.clicked.connect(lambda: self.worker.submit("acknowledge_stop"))
        layout.addWidget(self.stop_button)
        layout.addWidget(self.resume_button)
        self.activity_label = QLabel("Ready to connect")
        self.activity_label.setWordWrap(True)
        layout.addWidget(self.activity_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMinimumHeight(100)
        layout.addWidget(self.log)

    def _mode_changed(self, *_args) -> None:
        """Show explicit mode and prohibit substituting mock limits for physical ones."""
        physical = self.mode_combo.currentText() == "Physical"
        if physical:
            self.banner.setText(
                "PHYSICAL — connect only to the specified controller. Motion requires a reviewed "
                "apparatus safety profile and verified mm calibration. No homing or zeroing."
                if self.profile_edit.text().strip() else
                "PHYSICAL — INSPECTION ONLY. No safety profile: motion is locked and native "
                "controller positions are not assumed to be mm."
            )
            if not self._connection.has_session:
                self.position_label.setText("— controller units (mm unverified)")
                self.limits_label.setText("Physical limits: not loaded. Mock settings do not apply.")
        else:
            self.banner.setText("MOCK — synthetic motor only. Demonstration limits: 0–10 mm, "
                                "1 mm/s, 2 mm/s², maximum 2 mm per move.")
            if not self._connection.has_session:
                self.position_label.setText("— mm (simulation)")
                self.limits_label.setText("Mock demonstration limits: 0–10 mm; maximum move 2 mm.")
        self._refresh_controls()

    def _browse_profile(self) -> None:
        """Choose an existing reviewed profile; never create physical defaults."""
        path, _ = QFileDialog.getOpenFileName(self, "Select reviewed motor profile", "", "JSON (*.json)")
        if path:
            self.profile_edit.setText(path)

    def _connect_motor(self) -> None:
        """Submit the explicit selection, with no device discovery or auto-connect."""
        settings = ConnectionSettings(self.mode_combo.currentText(), f"COM{self.com_port_spin.value()}",
                                      self.axis_spin.value(), self.profile_edit.text())
        self.worker.submit("connect", settings)

    def _move(self, operation, value) -> None:
        """Submit one mm move; authoritative validation stays in the sequencer/driver."""
        self.worker.submit(operation, value, self.timeout_spin.value(), self.settle_spin.value())

    def _save_setup_report(self) -> None:
        """Choose a local diagnostic JSON file; collect/save through the I/O owner."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save motor setup report", "TC_motor_setup_report.json", "JSON (*.json)"
        )
        if path and not self.worker.submit("save_setup_report", path):
            self._show_error("Motor is busy; retry saving the setup report when idle.")

    def _stop_motor(self) -> None:
        """Latch STOP synchronously; controller I/O remains in the worker thread."""
        self.worker.request_stop()

    def _disconnect_motor(self) -> None:
        """Stop and disconnect asynchronously, preserving cleanup uncertainty."""
        self.worker.request_disconnect()

    def _release_after_manual_verification(self) -> None:
        """Require a deliberate physical confirmation before releasing a failed port."""
        if not (self._connection.connected and self._connection.communication_uncertain
                and self._cleanup_uncertain and not self._busy):
            return
        answer, accepted = QInputDialog.getText(
            self,
            "Manual motor recovery",
            "Software cannot verify STOP through this serial connection.\n"
            "Check the stage directly and confirm it is stationary and holding.\n"
            "Type STATIONARY to release the COM port without another motor command:",
        )
        if accepted and answer == "STATIONARY":
            if not self.worker.submit("release_after_manual_verification"):
                self._show_error("Motor is busy; manual release was not started.")

    def _poll(self) -> None:
        """Request bounded idle polling; never accumulate snapshots or moves."""
        if (self._connection.connected and not self._connection.polling_paused
                and not self._connection.communication_uncertain
                and not self._busy and not self._closing):
            self.worker.submit("snapshot")

    def _show_status(self, status) -> None:
        """Display measured state, respecting the unit-verification boundary."""
        self._status = status
        position = status.position_mm
        if position is None or not self._units_verified:
            native = getattr(status, "native_position", None)
            if native is None:
                native = position
            self.position_label.setText(
                f"{native:.6f} controller units (mm unverified)" if native is not None
                else "— controller units (mm unverified)"
            )
        else:
            self.position_label.setText(f"{position:.6f} mm")
        self.state_label.setText(f"{status.state}  |  code {status.state_code}  |  error {status.error_code}")
        self._refresh_controls()

    def _show_info(self, info) -> None:
        """Display identity returned by the sequencer, including simulation provenance."""
        prefix = "SIMULATION" if info.simulation else "PHYSICAL"
        self._units_verified = info.units_verified
        units = "mm verified" if info.units_verified else "mm unverified; inspection only"
        self.identity_label.setText(f"{prefix} | {info.controller_version} | {info.stage_reference} | "
                                    f"address {info.axis} | {units}")
        self._append_log(self.identity_label.text())

    def _show_connection(self, state) -> None:
        """Apply cached connection/limit state without querying a driver in the GUI."""
        was_faulted = self._connection.polling_paused or self._connection.communication_uncertain
        self._connection = state
        if state.polling_paused or state.communication_uncertain:
            self.poll_timer.stop()
            self.activity_label.setText(
                "Automatic reads paused after a status error. Check the log before disconnecting."
            )
            self.position_label.setText("Position unknown — status read failed")
            self.state_label.setText("Controller state unverified")
            if state.communication_uncertain:
                self.banner.setText("PHYSICAL — SERIAL FAULT. Motion is locked; use Disconnect or the "
                                    "physical standstill recovery control.")
        elif state.connected and not self._closing and not self.poll_timer.isActive():
            self.poll_timer.start()
        if state.limits is not None:
            lim = state.limits
            self.limits_label.setText(
                f"Limits {lim.min_position_mm:g} … {lim.max_position_mm:g} mm; "
                f"maximum move {lim.max_move_mm:g} mm; "
                f"velocity ≤ {lim.max_velocity_mm_s:g} mm/s; "
                f"acceleration ≤ {lim.max_acceleration_mm_s2:g} mm/s²."
            )
        if not state.has_session:
            self._status = None
            self._units_verified = False
            self.identity_label.setText("Disconnected")
            self.state_label.setText("No session")
            if was_faulted:
                self._mode_changed()
        self._refresh_controls()

    def _show_busy(self, busy) -> None:
        """Disable new motion while preserving responsive STOP/disconnect."""
        self._busy = self.worker.is_busy
        self._refresh_controls()

    def _show_stop_latch(self, latched) -> None:
        """Require a separate user acknowledgement after every STOP/disconnect."""
        self._stop_latched = self.worker.is_stop_latched
        self._refresh_controls()

    def _show_activity(self, message) -> None:
        """Display worker progress without filling the log with periodic polling."""
        if message != "Snapshot":
            self.activity_label.setText(message)
            if message.startswith("Inspected native controller settings:"):
                self._append_log(message)
            elif message.startswith("Setup report saved:"):
                self._append_log(message)

    def _show_error(self, message) -> None:
        """Keep failures visible and bounded; never silently substitute a target."""
        self.activity_label.setText(message)
        self._append_log("ERROR: " + message)

    def _append_log(self, message) -> None:
        """Append at most 250 timestamped log lines."""
        self.log.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def _refresh_controls(self) -> None:
        """Set GUI availability from cached state only."""
        state = self._connection
        editable = not self._busy and not state.has_session and not self._closing
        physical = self.mode_combo.currentText() == "Physical"
        for widget in (self.mode_combo, self.com_port_spin, self.axis_spin,
                       self.profile_edit, self.profile_button):
            widget.setEnabled(editable and (widget is self.mode_combo or physical))
        self.connect_button.setEnabled(editable)
        self.disconnect_button.setEnabled(state.has_session and not self._closing)
        self.report_button.setEnabled(state.connected and not state.communication_uncertain
                                      and not self._busy and not self._closing)
        self.recovery_button.setVisible(state.connected and state.communication_uncertain
                                        and self._cleanup_uncertain)
        self.recovery_button.setEnabled(not self._busy and not self._closing)
        ready = self._status is not None and self._status.is_ready
        permitted = (state.connected and state.motion_enabled and self._units_verified
                     and not state.polling_paused and not state.communication_uncertain
                     and not self._stop_latched and not self._closing and not self._cleanup_uncertain)
        # Periodic readback must not steal keyboard focus from numeric editors.
        for widget in (self.target_spin, self.step_spin, self.timeout_spin, self.settle_spin):
            widget.setEnabled(permitted)
        for widget in (self.move_button, self.plus_button, self.minus_button):
            widget.setEnabled(permitted and ready and not self._busy)
        self.stop_button.setEnabled(state.has_session or self._busy)
        self.resume_button.setEnabled(state.connected and self._stop_latched and not self._busy
                                      and not self._closing and not self._cleanup_uncertain)

    def _cleanup_finished(self, success, message) -> None:
        """Only successful safe cleanup allows a requested window close to finish."""
        self._append_log(message)
        self.activity_label.setText(message)
        self._cleanup_uncertain = not success
        if not success:
            self._closing = False
            if not self._connection.polling_paused and not self._connection.communication_uncertain:
                self.poll_timer.start()
        self._refresh_controls()

    def _worker_finished(self) -> None:
        """Complete the deferred close after the worker has exited safely."""
        if self._closing and not self._cleanup_uncertain:
            self._safe_to_close = True
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Keep the window open until asynchronous stop/disconnect succeeds."""
        if self._safe_to_close:
            event.accept()
            return
        event.ignore()
        if self._cleanup_uncertain and self._connection.communication_uncertain:
            self.activity_label.setText(
                "STOP could not be verified. Check the stage physically, then use the COM release control."
            )
            return
        if not self._closing:
            self._closing = True
            self.poll_timer.stop()
            self.activity_label.setText("Stopping and disconnecting before closing…")
            self._refresh_controls()
            self.worker.request_disconnect(close_window=True)


def main() -> int:
    """Launch the independent motor GUI, initially disconnected in mock mode."""
    app = QApplication.instance() or QApplication(sys.argv)
    panel = MotorPanel()
    panel.show()
    return app.exec()
