"""Offline regressions for STOP after a camera reports an unknown state."""

from threading import Event, get_ident

import pytest

from hhg_control.camera.drivers.mock_camera import MockPcoCamera
from hhg_control.camera.sequencer.scan_manager import CameraScanManager
from hhg_control.gui.camera.camera_panel import CameraMainWindow
from hhg_control.gui.camera.workers import CameraExposureTask


@pytest.mark.parametrize("gui_unknown", [False, True])
def test_stop_disconnects_idle_unknown_camera(qtbot, tmp_path, gui_unknown):
    """Both the GUI latch and driver fault allow STOP to release the source."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    camera = MockPcoCamera()
    camera.connect()
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window._hardware_state_unknown = gui_unknown
    camera.state_unknown = not gui_unknown
    window._roi_change_pending = True
    window._exposure_change_pending = True
    window._mode_change_pending = True
    window._configuration_controls(False)
    assert window.btn_stop.isEnabled()
    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_disconnect_task is None and not camera.is_connected)
    assert window.camera is not camera
    assert not window._hardware_state_unknown
    assert window.btn_go.isEnabled()
    assert not window._configuration_busy()
    assert not window._is_live_active
    window.close()


@pytest.mark.parametrize("error_before_stop", [False, True])
def test_stop_disconnects_after_live_cleanup_fails(qtbot, tmp_path, monkeypatch, error_before_stop):
    """A failed live stop must not leave LIVE ownership blocking disconnect."""
    window = CameraMainWindow()
    qtbot.addWidget(window)
    camera = MockPcoCamera()
    camera.connect()
    camera.set_exposure_time(0.001)
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    stop_live = camera.stop_live
    calls = []

    def fail_first_stop():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("unknown camera status")
        stop_live()

    monkeypatch.setattr(camera, "stop_live", fail_first_stop)
    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count > 0, timeout=5000)
    if error_before_stop:
        def fail_read(*args, **kwargs):
            raise RuntimeError("unknown camera status")

        monkeypatch.setattr(camera, "acquire_live_frame", fail_read)
        qtbot.waitUntil(lambda: window.active_live_task is None)
        assert camera.state_unknown
    window.btn_stop.click()
    qtbot.waitUntil(lambda: not camera.is_connected and window.active_disconnect_task is None)
    assert window.scan_manager.state == "IDLE"
    assert window.active_live_task is None
    assert "Disconnecting camera after unknown-state error" in window.txt_activity_log.toPlainText()
    window.close()


def test_fault_stop_waits_for_configuration_and_keeps_qt_responsive(qtbot, tmp_path, monkeypatch):
    """An in-flight SDK operation exits before close; repeated STOP is safe."""
    entered, release = Event(), Event()
    closed_on = []
    window = CameraMainWindow()
    qtbot.addWidget(window)
    camera = MockPcoCamera()
    camera.connect()
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    original_close = camera.close

    def close():
        assert release.is_set()
        closed_on.append(get_ident())
        original_close()

    def configure(value):
        entered.set()
        assert release.wait(5)
        camera.state_unknown = True
        raise RuntimeError("unknown camera status")

    monkeypatch.setattr(camera, "close", close)
    monkeypatch.setattr(window.scan_manager, "set_exposure_time", configure)
    task = CameraExposureTask(window.scan_manager, 0.001)
    window.active_exposure_task = task
    task.finished.connect(window._on_exposure_finished)
    task.start()
    try:
        qtbot.waitUntil(entered.is_set)
        window.btn_stop.click()
        window.btn_stop.click()
        assert not closed_on
        assert window._stop_disconnect_pending
    finally:
        release.set()
        assert task.wait(5000)
    qtbot.waitUntil(lambda: not camera.is_connected and window.active_disconnect_task is None)
    assert len(closed_on) == 1
    assert closed_on[0] != get_ident()
    window.close()


def test_fault_stop_failure_remains_visible_and_can_be_retried(qtbot, tmp_path, monkeypatch):
    """A failed close retains the camera and lockout until a successful retry."""
    from hhg_control.gui.camera import camera_panel

    warnings = []
    monkeypatch.setattr(camera_panel.QMessageBox, "warning", lambda *args: warnings.append(args))
    window = CameraMainWindow()
    qtbot.addWidget(window)
    camera = MockPcoCamera()
    camera.connect()
    camera.state_unknown = True
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    original_close = camera.close
    monkeypatch.setattr(camera, "close", lambda: (_ for _ in ()).throw(RuntimeError("close failed")))
    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_disconnect_task is None and bool(warnings))
    assert window.camera is camera and camera.is_connected
    assert window._hardware_state_unknown
    assert not window.btn_go.isEnabled()
    assert window.btn_stop.isEnabled()
    monkeypatch.setattr(camera, "close", original_close)
    window.btn_stop.click()
    qtbot.waitUntil(lambda: not camera.is_connected and window.active_disconnect_task is None)
    assert not window._hardware_state_unknown
    window.close()
