"""Offline acceptance of the separate motor GUI and its cancellation lifecycle."""

import threading
import json
from dataclasses import replace

import pytest
from PyQt6.QtCore import QTimer, Qt

from hhg_control.tc_motor.gui.motor_panel import MotorPanel
from hhg_control.tc_motor.gui.workers import ConnectionSettings, MotorWorker
from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits
from hhg_control.tc_motor.drivers.mock import MockMotor
from hhg_control.tc_motor.drivers.smc100 import SafetyProfile, SMC100Motor
from hhg_control.tc_motor.sequencer import MotorSequencer


class SyntheticSerial:
    """One controlled fake SMC100 session; no VISA or physical port is touched."""

    def __init__(self):
        self.commands = []
        self.corrupt_position = False
        self.closed = False
        self.responses = {
            "VE": "SMC_CC - Controller-driver version SYNTHETIC",
            "ZX?": "LTA-HS", "TS": "000032", "TP": "2", "TE": "@",
            "SU?": "0.0001", "VA?": "1", "AC?": "2", "SL?": "0",
            "SR?": "10", "BA?": "0", "BH?": "0",
        }

    def open(self):
        """Record a synthetic connection without accessing hardware."""
        self.closed = False

    def query(self, text):
        """Return one addressed response or inject the reported decoding fault."""
        self.commands.append(text)
        command = text[1:]
        if command == "TP" and self.corrupt_position:
            raise UnicodeDecodeError("ascii", b"\xca", 0, 1, "invalid byte")
        return text[:3] + self.responses[command]

    def write(self, text):
        """Record a motor command without executing it."""
        self.commands.append(text)

    def close(self):
        """Record release of the synthetic serial handle."""
        self.closed = True


@pytest.fixture
def motor_window(qtbot):
    """Always finish asynchronous cleanup before pytest releases Qt widgets."""
    windows = []
    gates = []

    def build(factory=None):
        panel = MotorPanel(**({"sequencer_factory": factory} if factory else {}))
        windows.append(panel)
        qtbot.addWidget(panel)
        panel.show()
        return panel

    build.gates = gates
    yield build
    for gate in gates:
        gate.set()
    for panel in windows:
        if panel.worker.isRunning():
            panel.close()
            qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=5000)


def connect_mock(qtbot, panel):
    """Connect through the same user action used by the standalone window."""
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.move_button.isEnabled(), timeout=3000)


def test_construction_is_disconnected_and_does_not_create_driver(qtbot, motor_window):
    calls = []
    panel = motor_window(lambda settings: calls.append(settings))
    assert calls == []
    assert panel.mode_combo.currentText() == "Mock"
    assert not panel.move_button.isEnabled()
    assert not panel.stop_button.isEnabled()
    assert panel.connect_button.isEnabled()
    assert not panel.report_button.isEnabled()


def test_mock_connection_locks_mode_and_displays_mm(qtbot, motor_window):
    panel = motor_window()
    connect_mock(qtbot, panel)
    assert not panel.mode_combo.isEnabled()
    assert "SIMULATION" in panel.identity_label.text()
    assert "mm" in panel.position_label.text()
    assert "maximum move 2 mm" in panel.limits_label.text()
    assert panel.stop_button.isEnabled()


def test_save_setup_report_through_worker(qtbot, motor_window, tmp_path, monkeypatch):
    path = tmp_path / "TC motor setup.json"
    monkeypatch.setattr("hhg_control.tc_motor.gui.motor_panel.QFileDialog.getSaveFileName",
                        lambda *_args: (str(path), "JSON (*.json)"))
    panel = motor_window()
    connect_mock(qtbot, panel)
    qtbot.mouseClick(panel.report_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Setup report saved:" in panel.log.toPlainText())
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["identity"]["simulation"] is True
    assert report["connection_request"]["mode"] == "Mock"
    assert panel.position_label.text() == "0.000000 mm"
    assert panel._connection.connected


def test_report_write_failure_keeps_connection_and_window(qtbot, motor_window, tmp_path, monkeypatch):
    path = tmp_path / "missing folder" / "report.json"
    monkeypatch.setattr("hhg_control.tc_motor.gui.motor_panel.QFileDialog.getSaveFileName",
                        lambda *_args: (str(path), "JSON (*.json)"))
    panel = motor_window()
    connect_mock(qtbot, panel)
    qtbot.mouseClick(panel.report_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "FileNotFoundError" in panel.log.toPlainText())
    assert panel.isVisible()
    assert panel._connection.connected
    qtbot.waitUntil(lambda: not panel.worker.is_busy)
    assert panel.move_button.isEnabled()


def test_report_cannot_overwrite_selected_safety_profile(qtbot, motor_window, tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    path.write_text("existing reviewed profile", encoding="utf-8")
    monkeypatch.setattr("hhg_control.tc_motor.gui.motor_panel.QFileDialog.getSaveFileName",
                        lambda *_args: (str(path), "JSON (*.json)"))
    # Use a mock factory for all physical-mode UI actions; no serial resource opens.
    panel = motor_window(lambda _: MotorSequencer(MockMotor(MotorLimits(0, 10, 1, 2, 2))))
    panel.mode_combo.setCurrentText("Physical")
    panel.profile_edit.setText(str(path))
    connect_mock(qtbot, panel)
    qtbot.mouseClick(panel.report_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "separate from the active safety profile" in panel.log.toPlainText())
    assert path.read_text(encoding="utf-8") == "existing reviewed profile"


def test_range_rejection_is_visible_without_motion(qtbot, motor_window):
    panel = motor_window()
    connect_mock(qtbot, panel)
    panel.target_spin.setValue(3)  # Within travel, outside the 2 mm per-move limit.
    qtbot.mouseClick(panel.move_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "per-move limit" in panel.log.toPlainText())
    assert panel.position_label.text() == "0.000000 mm"
    qtbot.waitUntil(lambda: panel.resume_button.isEnabled())
    assert not panel.move_button.isEnabled()


def test_stop_is_responsive_during_motion_and_requires_resume(qtbot, motor_window):
    started = threading.Event()
    owner_threads = []

    class RecordingMotor(MockMotor):
        def move_to(self, target_mm):
            owner_threads.append(threading.get_ident())
            super().move_to(target_mm)
            started.set()

    driver = RecordingMotor(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver, poll_interval_s=.01))
    connect_mock(qtbot, panel)
    ticks = []
    timer = QTimer(panel)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    panel.target_spin.setValue(2)
    qtbot.mouseClick(panel.move_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(started.is_set)
    qtbot.waitUntil(lambda: len(ticks) >= 3)
    assert panel.stop_button.isEnabled()
    assert not panel.move_button.isEnabled()
    qtbot.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.resume_button.isEnabled(), timeout=3000)
    assert not panel.move_button.isEnabled()
    assert panel.worker.is_stop_latched
    assert owner_threads == [owner_threads[0]]
    assert owner_threads[0] != threading.get_ident()
    assert 0 <= driver.get_position() < 2
    qtbot.mouseClick(panel.resume_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.move_button.isEnabled())


def test_stop_cancels_an_accepted_but_unstarted_move(qtbot):
    """Use the condition to control dispatch rather than assume thread ordering."""
    calls = []

    class RecordingMotor(MockMotor):
        def move_to(self, target_mm):
            calls.append(target_mm)
            super().move_to(target_mm)

    driver = RecordingMotor(MotorLimits(0, 10, 1, 2, 2))
    seq = MotorSequencer(driver)
    worker = MotorWorker(lambda _: seq)
    worker.start()
    try:
        assert worker.submit("connect", ConnectionSettings())
        qtbot.waitUntil(lambda: not worker.is_busy)
        with worker._condition:
            assert worker.submit("move_to", 1, 5, 0)
            worker.request_stop()
        qtbot.waitUntil(lambda: not worker.is_busy)
        assert calls == []
        assert seq.stop_requested
        assert not worker.submit("move_to", 1, 5, 0)
    finally:
        worker.request_disconnect(close_window=True)
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=3000)


def test_stop_during_slow_connect_is_latched(qtbot, motor_window):
    entered = threading.Event()
    release = threading.Event()
    motor_window.gates.append(release)

    class SlowConnect(MockMotor):
        def connect(self):
            entered.set()
            if not release.wait(3):
                raise MotorError("Test connection gate timed out.")
            return super().connect()

    driver = SlowConnect(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver))
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(entered.is_set)
    assert panel.stop_button.isEnabled()
    qtbot.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
    assert panel.worker.is_stop_latched
    release.set()
    qtbot.waitUntil(lambda: panel.resume_button.isEnabled(), timeout=3000)
    assert not panel.move_button.isEnabled()


def test_close_stops_disconnects_and_releases_worker(qtbot, motor_window):
    driver = MockMotor(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver, poll_interval_s=.01))
    connect_mock(qtbot, panel)
    panel.target_spin.setValue(2)
    qtbot.mouseClick(panel.move_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel._status.is_moving)
    panel.close()
    qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=3000)
    qtbot.waitUntil(lambda: not panel.isVisible())
    assert not driver.is_connected


def test_close_during_pending_connection_waits_for_safe_disconnect(qtbot, motor_window):
    entered = threading.Event()
    release = threading.Event()
    motor_window.gates.append(release)

    class SlowConnect(MockMotor):
        def connect(self):
            entered.set()
            if not release.wait(3):
                raise MotorError("Test connection gate timed out.")
            return super().connect()

    driver = SlowConnect(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver))
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(entered.is_set)
    panel.close()
    assert panel.isVisible()
    assert panel.worker.isRunning()
    assert panel.worker.is_stop_latched
    release.set()
    qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=3000)
    qtbot.waitUntil(lambda: not panel.isVisible())
    assert not driver.is_connected


def test_stop_wins_over_in_progress_resume(qtbot, motor_window):
    entered = threading.Event()
    release = threading.Event()
    motor_window.gates.append(release)

    class SlowResume(MotorSequencer):
        def acknowledge_stop(self):
            entered.set()
            if not release.wait(3):
                raise MotorError("Test resume gate timed out.")
            super().acknowledge_stop()

    panel = motor_window(lambda _: SlowResume(MockMotor(MotorLimits(0, 10, 1, 2, 2))))
    connect_mock(qtbot, panel)
    qtbot.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.resume_button.isEnabled())
    qtbot.mouseClick(panel.resume_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(entered.is_set)
    qtbot.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
    assert panel.worker.is_stop_latched
    release.set()
    qtbot.waitUntil(lambda: panel.resume_button.isEnabled())
    assert panel.worker.is_stop_latched
    assert not panel.move_button.isEnabled()


def test_failed_stop_keeps_close_pending_window_open(qtbot, motor_window):
    class FailingStop(MockMotor):
        fail_stop = True

        def stop(self):
            if self.fail_stop:
                raise MotorError("Injected stop failure.")
            return super().stop()

    driver = FailingStop(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver))
    try:
        connect_mock(qtbot, panel)
        panel.close()
        qtbot.waitUntil(lambda: "unconfirmed" in panel.log.toPlainText())
        assert panel.isVisible()
        assert panel.worker.isRunning()
        assert driver.is_connected
        assert not panel.move_button.isEnabled()
        assert not panel._closing
    finally:
        driver.fail_stop = False
    panel.close()
    qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=3000)
    assert not driver.is_connected


def test_physical_selection_has_no_mock_limits_or_auto_connection(qtbot, motor_window):
    calls = []
    panel = motor_window(lambda config: calls.append(config))
    panel.mode_combo.setCurrentText("Physical")
    assert calls == []
    assert "INSPECTION ONLY" in panel.banner.text()
    assert "mm unverified" in panel.position_label.text()
    assert "not loaded" in panel.limits_label.text()
    assert panel.com_port_spin.isEnabled()
    assert panel.com_port_spin.value() == 21
    assert not panel.move_button.isEnabled()


def test_inspection_connection_displays_native_units_and_locks_motion(qtbot, motor_window):
    class InspectionMotor(MockMotor):
        def connect(self):
            info = super().connect()
            self._motion_enabled = False
            return replace(info, simulation=False, units_verified=False)

        def get_status(self):
            return replace(super().get_status(), position_mm=None, native_position=7.25)

    driver = InspectionMotor(MotorLimits(0, 10, 1, 2, 2))
    panel = motor_window(lambda _: MotorSequencer(driver))
    panel.mode_combo.setCurrentText("Physical")
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel._connection.connected and not panel.worker.is_busy)
    assert panel.position_label.text() == "7.250000 controller units (mm unverified)"
    assert not panel.move_button.isEnabled()
    assert "inspection only" in panel.identity_label.text()


def test_inspection_serial_fault_pauses_polling_and_closes_without_stop(qtbot, motor_window):
    serial = SyntheticSerial()
    panel = motor_window(lambda _: MotorSequencer(
        SMC100Motor("COM21", transport_factory=lambda: serial)))
    panel.mode_combo.setCurrentText("Physical")
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel._connection.connected and not panel.worker.is_busy)
    serial.corrupt_position = True
    assert panel.worker.submit("snapshot")
    qtbot.waitUntil(lambda: panel._connection.communication_uncertain and not panel.worker.is_busy)
    assert not panel.poll_timer.isActive()
    assert "Position unknown" in panel.position_label.text()
    assert "state unverified" in panel.state_label.text()
    assert panel.worker.submit("snapshot") is False
    first_error_count = panel.log.toPlainText().count("SMC100 query TP failed")
    panel._poll()
    panel._poll()
    assert panel.log.toPlainText().count("SMC100 query TP failed") == first_error_count == 1
    assert not panel.move_button.isEnabled()
    qtbot.mouseClick(panel.disconnect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not panel._connection.has_session)
    assert serial.closed
    assert "Inspection port released after serial fault" in panel.log.toPlainText()
    assert not any(command.startswith(("1ST", "1PA")) for command in serial.commands)
    panel.close()
    qtbot.waitUntil(lambda: not panel.worker.isRunning())


def test_rejected_connect_cleans_session_and_allows_new_attempt(qtbot, motor_window):
    serials = []

    def factory(_):
        serial = SyntheticSerial()
        if not serials:
            serial.responses["TE"] = "M"
        serials.append(serial)
        return MotorSequencer(SMC100Motor("COM21", transport_factory=lambda: serial))

    panel = motor_window(factory)
    panel.mode_combo.setCurrentText("Physical")
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Controller command error M" in panel.log.toPlainText()
                    and not panel.worker.is_busy)
    assert serials[0].closed and not panel._connection.has_session
    assert panel.connect_button.isEnabled()
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel._connection.connected and not panel.worker.is_busy)
    assert len(serials) == 2 and not serials[1].closed


def test_motion_profile_serial_fault_requires_explicit_physical_recovery(
        qtbot, motor_window, monkeypatch):
    serial = SyntheticSerial()
    version = serial.responses["VE"]
    reviewed = SafetyProfile(MotorLimits(1, 9, 1, 2, 2), version, "LTA-HS", .0001,
                             .1, True, True, True)
    panel = motor_window(lambda _: MotorSequencer(
        SMC100Motor("COM21", profile=reviewed, transport_factory=lambda: serial)))
    panel.mode_combo.setCurrentText("Physical")
    panel.profile_edit.setText("synthetic test profile")
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel._connection.connected and not panel.worker.is_busy)
    serial.corrupt_position = True
    assert panel.worker.submit("snapshot")
    qtbot.waitUntil(lambda: panel._connection.communication_uncertain and not panel.worker.is_busy)
    panel.close()
    qtbot.waitUntil(lambda: panel._cleanup_uncertain and not panel.worker.is_busy)
    assert panel.isVisible() and panel.recovery_button.isVisible()
    assert "1ST" in serial.commands and not serial.closed
    command_count = len(serial.commands)
    monkeypatch.setattr("hhg_control.tc_motor.gui.motor_panel.QInputDialog.getText",
                        lambda *_args: ("STATIONARY", True))
    qtbot.mouseClick(panel.recovery_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not panel._connection.has_session)
    assert serial.closed and len(serial.commands) == command_count
    assert "software STOP remains unverified" in panel.log.toPlainText()
    panel.close()
    qtbot.waitUntil(lambda: not panel.worker.isRunning())


def test_com_number_is_passed_as_explicit_port_without_discovery(qtbot, motor_window):
    requests = []

    def factory(settings):
        requests.append(settings)
        return MotorSequencer(MockMotor(MotorLimits(0, 10, 1, 2, 2)))

    panel = motor_window(factory)
    panel.mode_combo.setCurrentText("Physical")
    panel.com_port_spin.setValue(21)
    assert requests == []
    connect_mock(qtbot, panel)
    assert requests[0].resource == "COM21"
    assert requests[0].axis == 1
    assert not panel.com_port_spin.isEnabled()
