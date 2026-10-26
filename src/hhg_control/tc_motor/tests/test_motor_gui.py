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
from hhg_control.tc_motor.sequencer import MotorSequencer


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
