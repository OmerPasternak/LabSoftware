"""Safety regressions exercised with small mocks and fake SDKs only."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtCore import QTimer

from hhg_control import safe_io
from hhg_control.drivers.base_camera import CameraSafetyError, ReadoutMode, TriggerMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.drivers import pco_edge
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.ui.camera.camera_panel import CameraMainWindow
from tests.test_pco_driver_contract import FakePcoCamera


class SmallMock(MockPcoCamera):
    WIDTH, HEIGHT = 64, 32


def test_exposure_model_and_sdk_limits_are_intersected(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    fake.description["max exposure time"] = 0.05
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kwargs: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(CameraSafetyError):
        camera.set_exposure_time(0.06)
    assert fake.exposure_time == 0.01
    camera.close()
    mock = SmallMock()
    with pytest.raises(CameraSafetyError):
        mock.set_exposure_time(2.001)


def test_serial_is_required_before_vendor_constructor(monkeypatch):
    calls = []
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kwargs: calls.append(kwargs)))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    with pytest.raises(ValueError, match="serial"):
        pco_edge.PcoEdgeCamera().connect()
    assert calls == []


def test_failed_stop_still_closes_and_reports_unknown(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kwargs: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    closed = []
    def failed_stop():
        raise OSError("injected stop failure")
    fake.stop = failed_stop
    fake.close = lambda: closed.append(True)
    with pytest.raises(RuntimeError, match="state unknown.*stop"):
        camera.close()
    assert closed == [True]
    assert camera.state_unknown


def test_failed_close_retains_handle_for_retry(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kwargs: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    def failed_close():
        raise OSError("injected close failure")
    fake.close = failed_close
    with pytest.raises(RuntimeError, match="state unknown.*close"):
        camera.close()
    assert camera._cam is fake and camera.is_connected
    fake.close = lambda: None
    camera.close()
    assert not camera.is_connected and not camera.state_unknown


def test_slow_commands_keep_gui_responsive_and_block_overlap(qtbot, tmp_path):
    gui_thread = threading.get_ident()
    calls = []
    class SlowCamera(SmallMock):
        def set_readout_mode(self, mode):
            calls.append(("mode", threading.get_ident()))
            time.sleep(0.25)
            super().set_readout_mode(mode)
        def set_exposure_time(self, value):
            calls.append(("exposure", threading.get_ident()))
            time.sleep(0.25)
            super().set_exposure_time(value)
        def close(self):
            calls.append(("close", threading.get_ident()))
            time.sleep(0.25)
            super().close()
    camera = SlowCamera()
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window._on_camera_connected(camera, True, 0)
    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start(10)
    window._start_mode_change(ReadoutMode.GLOBAL_SHUTTER, resume_live=False)
    qtbot.waitUntil(lambda: window.scan_manager.state == "CONFIGURING")
    window._on_go_clicked()
    window._on_exposure_changed(20)
    assert window.active_live_task is None and window.active_exposure_task is None
    assert not window.btn_go.isEnabled()
    qtbot.waitUntil(lambda: window.active_mode_task is None and window.active_preview_task is None)
    assert len(ticks) >= 8
    before = len(ticks)
    window._on_exposure_changed(20)
    qtbot.waitUntil(lambda: window.active_exposure_task is None)
    assert camera._exposure_time_s == 0.02 and len(ticks) - before >= 8
    before = len(ticks)
    window.show()
    window.close()
    qtbot.waitUntil(lambda: window._close_disconnect_done)
    assert len(ticks) - before >= 8
    assert all(thread_id != gui_thread for _, thread_id in calls)


@pytest.mark.parametrize("change", ["exposure", "mode", "trigger", "roi"])
def test_configuration_pauses_live_without_label_flicker(qtbot, tmp_path, change):
    class GuardedMock(SmallMock):
        def _idle_delay(self):
            assert not getattr(self, "_live_active", False)
            time.sleep(0.15)
        def set_exposure_time(self, value):
            self._idle_delay()
            super().set_exposure_time(value)
        def set_readout_mode(self, mode):
            self._idle_delay()
            super().set_readout_mode(mode)
        def set_trigger_mode(self, mode):
            self._idle_delay()
            super().set_trigger_mode(mode)
        def set_roi(self, roi):
            self._idle_delay()
            super().set_roi(roi)
    camera = GuardedMock()
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window._on_camera_connected(camera, True, 0)
    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count > 0)
    original = (window.btn_go.text(), window.btn_go.styleSheet(), window.lbl_system_status.text())
    observed = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: observed.append(
        (window.btn_go.text(), window.btn_go.styleSheet(), window.lbl_system_status.text())))
    timer.start(5)
    if change == "exposure":
        window.spn_exposure.setValue(20)
    elif change == "mode":
        window.cmb_readout_mode.setCurrentIndex(window.cmb_readout_mode.findData(ReadoutMode.GLOBAL_SHUTTER))
    elif change == "trigger":
        window.chk_external_trigger.setChecked(True)
    else:
        window._request_roi_change((0, 0, 64, 32), full_sensor=True)
    assert not window.btn_go.isEnabled() and window.btn_stop.isEnabled()
    qtbot.waitUntil(lambda: not window._configuration_busy() and window.scan_manager.state == "LIVE")
    timer.stop()
    assert len(observed) >= 10 and all(item == original for item in observed)
    if change == "exposure":
        assert camera._exposure_time_s == 0.02
    elif change == "mode":
        assert camera._readout_mode == ReadoutMode.GLOBAL_SHUTTER
    elif change == "trigger":
        assert camera._trigger_mode == TriggerMode.EXTERNAL_EXPOSURE_START
    window._on_stop_clicked()
    qtbot.waitUntil(lambda: window.active_live_task is None)
    window.close()
    qtbot.waitUntil(lambda: window._close_disconnect_done)


def test_camera_selection_is_remembered_between_windows(qtbot, tmp_path, monkeypatch):
    from hhg_control.ui.camera.selection import load_camera_serial, save_camera_serial
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data" / "camera_selection.json"
    assert load_camera_serial(path) is None
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window._physical_serial = "12345"
    camera = SmallMock()
    camera.connect()
    window._on_camera_connected(camera, False, 0)
    assert load_camera_serial(path) == "12345"
    second = CameraMainWindow()
    qtbot.addWidget(second)
    assert second._physical_serial == "12345"
    window.close()
    qtbot.waitUntil(lambda: window._close_disconnect_done)
    second.close()
    path.write_text("invalid JSON", encoding="utf-8")
    assert load_camera_serial(path) is None
    with pytest.raises(ValueError):
        save_camera_serial(path, "not a serial")


def test_stop_during_configuration_cancels_live_resume(qtbot, tmp_path):
    class SlowMock(SmallMock):
        def set_exposure_time(self, value):
            time.sleep(0.2)
            super().set_exposure_time(value)
    camera = SlowMock()
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window._on_camera_connected(camera, True, 0)
    window._start_live()
    qtbot.waitUntil(lambda: window._frame_count > 0)
    window.spn_exposure.setValue(20)
    qtbot.waitUntil(lambda: window.active_exposure_task is not None)
    window.btn_stop.click()
    qtbot.waitUntil(lambda: window.active_exposure_task is None)
    assert window.active_live_task is None and not window._is_live_active
    assert window.btn_go.text() == "GO" and window.btn_go.isEnabled()
    window.close()
    qtbot.waitUntil(lambda: window._close_disconnect_done)


def test_mid_scan_low_disk_stops_and_retains_partial(tmp_path, monkeypatch):
    count = []
    def disk_usage(directory):
        count.append(True)
        return SimpleNamespace(free=1_000_000_000 if len(count) == 1 else 0)
    monkeypatch.setattr(safe_io.shutil, "disk_usage", disk_usage)
    camera = SmallMock()
    camera.connect()
    manager = CameraScanManager(camera, tmp_path)
    with pytest.raises(RuntimeError, match="writer failed"):
        manager.acquire_and_save_step("scan", 0, "metadata", 0, 20)
    assert manager.state == "IDLE"
    assert not list(tmp_path.glob("*.h5"))
    assert len(list(tmp_path.glob("*.h5.partial"))) == 1
    camera.close()


def test_final_name_collision_never_replaces_existing_file(tmp_path, monkeypatch):
    camera = SmallMock()
    camera.connect()
    manager = CameraScanManager(camera, tmp_path)
    path = tmp_path / "existing.h5"
    path.write_bytes(b"valuable existing data")
    monkeypatch.setattr(manager, "_unique_filepath", lambda *args: path)
    with pytest.raises(FileExistsError):
        manager.acquire_and_save_step("scan", 0, "metadata", 0, 2)
    assert path.read_bytes() == b"valuable existing data"
    assert path.with_suffix(".h5.partial").exists()
    camera.close()


def test_stack_memory_is_checked_before_camera_record(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **kwargs: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    def deny(*args):
        raise MemoryError("injected RAM budget")
    monkeypatch.setattr(pco_edge, "check_stack_memory", deny)
    with pytest.raises(MemoryError):
        camera.acquire_frames(10000)
    assert fake.record_calls == []
    camera.close()


def test_analysis_export_cannot_replace_scan(tmp_path, monkeypatch):
    from scripts import analyze_saved_scan_g2 as script
    path = tmp_path / "original.h5"
    path.write_bytes(b"original scan")
    args = SimpleNamespace(data_dir=str(tmp_path), prefix="HHG", epsilon=0,
                           background=0, max_steps=None, output=str(path))
    monkeypatch.setattr(script, "parse_args", lambda: args)
    stats = dict(num_files=1, total_frames=2, peak_intensity=1, g2_center=1, g2_mean_active=1)
    monkeypatch.setattr(script, "compute_g2_from_scan", lambda *args, **kwargs: (np.ones((2, 2)), stats))
    assert script.main() == 1
    assert path.read_bytes() == b"original scan"
