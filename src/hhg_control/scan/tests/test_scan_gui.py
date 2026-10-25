"""Offline GUI ownership, STOP acknowledgement and uncertain-cleanup close guards."""

from threading import Event
from types import SimpleNamespace

import pytest

from hhg_control.scan.gui import ScanPanel
from hhg_control.scan.session import ScanSession, ScanSettings


class Session:
    """Event-controlled session with no instrument objects or hardware access."""

    def __init__(self, *, cleanup_fails=False):
        self.entered, self.release, self.stopped = Event(), Event(), Event()
        self.cleanup_fails = cleanup_fails
        self.scan = SimpleNamespace(run=self.run, request_stop=self.stopped.set, last_run_dir=None)

    def connect(self, cancel):
        if cancel():
            raise RuntimeError("Cancelled before connect")

    def run(self, *args, **kwargs):
        self.entered.set()
        if not self.release.wait(10):
            raise TimeoutError("Test did not release worker")
        if self.stopped.is_set():
            raise RuntimeError("Test STOP latched")
        return "simulated-run"

    def close(self):
        if self.cleanup_fails:
            raise RuntimeError("Test cleanup uncertainty")


def build(qtbot, tmp_path, factory):
    panel = ScanPanel(session_factory=factory)
    qtbot.addWidget(panel)
    panel.output.setText(str(tmp_path))
    panel.show()
    return panel


def test_window_starts_disconnected_and_mode_change_clears_mock_coordinates(qtbot, tmp_path):
    calls = []
    panel = build(qtbot, tmp_path, lambda settings: calls.append(settings))
    assert not calls and panel.mode.currentText() == "Mock"
    settings, plan = panel.inputs()
    assert settings.mode == "Mock" and len(plan.visits) == 4
    panel.mode.setCurrentText("Physical")
    assert not panel.positions.toPlainText() and not panel.roi.text()
    assert not panel.reviewed.isChecked()
    panel.close()


def test_stop_is_responsive_and_requires_acknowledgement_after_owner_finishes(qtbot, tmp_path):
    session = Session()
    panel = build(qtbot, tmp_path, lambda settings: session)
    panel._start()
    try:
        qtbot.waitUntil(session.entered.is_set)
        assert not panel.body.isEnabled()
        panel._stop()
        assert session.stopped.is_set()
        assert panel.worker.isRunning()
    finally:
        session.release.set()
        qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=5000)
        qtbot.waitUntil(lambda: panel.acknowledge.isEnabled())
    assert panel.safe and not panel.start.isEnabled()
    panel._acknowledge()
    assert panel.start.isEnabled() and not panel.stop_latched
    panel.close()


def test_close_is_blocked_until_uncertain_session_cleanup_is_retried(qtbot, tmp_path):
    session = Session(cleanup_fails=True)
    panel = build(qtbot, tmp_path, lambda settings: session)
    panel._start()
    try:
        qtbot.waitUntil(session.entered.is_set)
        panel.close()
        assert panel.isVisible() and session.stopped.is_set()
    finally:
        session.release.set()
        qtbot.waitUntil(lambda: not panel.worker.isRunning(), timeout=5000)
        qtbot.waitUntil(lambda: panel.retry.isEnabled())
        session.cleanup_fails = False
        panel._retry()
        qtbot.waitUntil(lambda: not panel.retry_worker.isRunning(), timeout=5000)
        qtbot.waitUntil(lambda: not panel.isVisible())
    assert panel.safe


def test_physical_factory_rejects_missing_review_before_importing_hardware(tmp_path):
    with pytest.raises(ValueError, match="reviewed"):
        ScanSession(ScanSettings(str(tmp_path), mode="Physical"))


def test_actual_mock_session_runs_with_the_visible_external_input_choice(tmp_path):
    from hhg_control.scan import ScanPlan, Visit
    session = ScanSession(ScanSettings(str(tmp_path), roi=(0, 1072, 64, 1088), enable_external_input=True))
    try:
        session.connect()
        assert session.manager.camera.get_external_trigger_input_enabled()
        path = session.scan.run(ScanPlan((Visit(.001),), 3, .01, .001))
        assert (path / "run.json").exists()
    finally:
        session.close()
