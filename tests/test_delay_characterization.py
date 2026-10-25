"""Offline delay characterization: raw integrity, statistical meaning and lifecycle."""

import json
import os
from threading import Event, Thread
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
import pytest

from hhg_control.camera.analysis.harmonic_intensity import make_intensity_window
from hhg_control.camera.drivers.base_camera import CameraSafetyError, TriggerMode
from hhg_control.camera.drivers.mock_camera import MockPcoCamera
from hhg_control.camera.sequencer.delay_characterization import (
    acquire_delay_reference, characterize_delay, delay_points_ns,
)
from hhg_control.camera.sequencer.scan_manager import CameraScanManager, AcquisitionAborted
from hhg_control.safe_io import filesystem_path


@pytest.fixture
def manager(tmp_path):
    """Use the actual synthetic driver and production writer with a tiny sensor."""
    class SmallCamera(MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    camera = SmallCamera()
    camera.connect()
    camera.set_exposure_time(0.0005)
    camera.set_delay_time(3000e-9)
    manager = CameraScanManager(camera, tmp_path)
    yield manager
    camera.close()


def select(manager, run_id="test"):
    """Produce a real mock reference file and define a fixed test rectangle."""
    logs = []
    reference = acquire_delay_reference(manager, run_id, 3, lambda: False, logs.append)
    assert any("Estimated characterization time" in line for line in logs)
    window = make_intensity_window((1, 2, 8, 6), reference)
    (manager.storage_dir / f"camera_delay_{run_id}_window.json").write_text(
        json.dumps(window), encoding="utf-8")
    return window


def test_scan_raw_frames_statistics_settings_and_plot(manager, monkeypatch):
    camera = manager.camera
    window = select(manager)
    captured = []
    original_acquire = camera.acquire_frames
    def acquire(count):
        images, metadata = original_acquire(count)
        captured.extend((image.copy(), dict(meta)) for image, meta in zip(images, metadata))
        return images, metadata
    camera.acquire_frames = acquire
    path = characterize_delay(manager, "test", 3, window, lambda: False, lambda _: None)
    report = json.loads(filesystem_path(path).read_text())
    assert report["complete"]
    assert report["original_delay_s"] == pytest.approx(3000e-9)
    assert report["original_trigger_mode"] == TriggerMode.AUTO_SEQUENCE.value
    assert camera.get_delay_time() == pytest.approx(3000e-9)
    assert camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert not camera.get_external_trigger_input_enabled()
    assert manager.state == "IDLE"
    assert len(report["points"]) == 15
    assert len(captured) == 45
    for index, point in enumerate(report["points"]):
        with h5py.File(filesystem_path(point["source_files"][0]), "r") as source:
            images = source["images"][:]
            assert source.attrs["complete"]
            assert source.attrs["trigger_mode"] == TriggerMode.EXTERNAL_EXPOSURE_START.value
            assert source.attrs["external_trigger_input_enabled"]
            assert source.attrs["external_trigger_input_polarity"] == "rising edge"
            assert source.attrs["scan_parameter_name"] == "Camera delay (ns)"
            assert source.attrs["scan_parameter_value"] == pytest.approx(point["actual_delay_ns"])
            expected_frames = [item[0] for item in captured[index*3:index*3+3]]
            np.testing.assert_array_equal(images, expected_frames)
            metas = [json.loads(value) for value in source["frame_metadata/json"][:]]
            assert [m["frame_id"] for m in metas] == [m[1]["frame_id"] for m in captured[index*3:index*3+3]]
            context = json.loads(source.attrs["frame_acquisition_context_json"])
            assert context["actual_delay_ns"] == pytest.approx(point["actual_delay_ns"])
            assert context["delay_point_index"] == index + 1
            assert all(meta["acquisition_context"] == context for meta in metas)
        sums = images[:, 2:6, 1:8].sum(axis=(1, 2), dtype=np.float64)
        assert point["average_adu"] == pytest.approx(sums.mean())
        assert point["stdev_adu"] == pytest.approx(sums.std(ddof=1))
    from scripts.plot_camera_delay import make_figure
    figure = make_figure(report)
    assert len(figure.axes) == 2
    assert len(figure.axes[0].lines[0].get_xdata()) == 5
    assert len(figure.axes[1].lines[0].get_xdata()) == 10
    assert "SIMULATED" in figure._suptitle.get_text()
    figure.savefig(filesystem_path(manager.storage_dir / "test.png"))
    from scripts import plot_camera_delay
    report_for_safety_check = json.loads(json.dumps(report))
    unrelated = manager.storage_dir / "unrelated.h5"
    filesystem_path(unrelated).write_bytes(b"")
    report_for_safety_check["points"][0]["source_files"] = [
        str(unrelated)]
    with pytest.raises(ValueError, match="Unexpected gathered-data file"):
        plot_camera_delay.delete_gathered_data(path, report_for_safety_check)
    assert all(filesystem_path(source).is_file()
               for point in report["points"] for source in point["source_files"])
    assert filesystem_path(report["window"]["reference_file"]).is_file()
    window_path = manager.storage_dir / "camera_delay_test_window.json"
    assert filesystem_path(window_path).is_file()
    shown = []
    monkeypatch.setattr("hhg_control.camera.analysis.plot_windows.show_analysis_figures",
                        lambda: shown.append(True))
    assert plot_camera_delay.main([str(path)]) == 0
    assert shown == [True]
    assert filesystem_path(path.with_suffix(".png")).is_file()
    assert not filesystem_path(path).exists()
    assert not filesystem_path(window_path).exists()
    assert not filesystem_path(report["window"]["reference_file"]).exists()
    assert all(not filesystem_path(source).exists()
               for point in report["points"] for source in point["source_files"])
    assert filesystem_path(unrelated).is_file()


@pytest.mark.parametrize("initial_external_input", [False, True])
def test_stop_preserves_completed_points_and_restores_settings(manager, initial_external_input):
    manager.camera.set_external_trigger_input_enabled(initial_external_input)
    window = select(manager)
    stopped = Event()
    def log(message):
        if "Delay 1/15: Average" in message:
            stopped.set()
    with pytest.raises(AcquisitionAborted):
        characterize_delay(manager, "test", 3, window, stopped.is_set, log)
    report = json.loads(filesystem_path(manager.storage_dir / "camera_delay_test.json").read_text())
    assert not report["complete"] and len(report["points"]) == 1
    assert manager.camera.get_delay_time() == pytest.approx(3000e-9)
    assert manager.camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert manager.camera.get_external_trigger_input_enabled() is initial_external_input
    assert manager.state == "IDLE"


def test_analysis_failure_retains_raw_and_restores(manager, monkeypatch):
    from hhg_control.camera.sequencer import delay_characterization as module
    window = select(manager)
    def fail(*args, **kwargs):
        raise ValueError("analysis failed")
    monkeypatch.setattr(module, "compute_harmonic_intensity", fail)
    with pytest.raises(ValueError, match="analysis failed"):
        characterize_delay(manager, "test", 3, window, lambda: False, lambda _: None)
    assert len(list(manager.storage_dir.glob("*.h5"))) == 2
    assert manager.camera.get_delay_time() == pytest.approx(3000e-9)
    assert manager.camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert not manager.camera.get_external_trigger_input_enabled()
    assert manager.state == "IDLE"


def test_cleanup_failure_is_explicit_and_report_is_incomplete(manager, monkeypatch):
    window = select(manager)
    original = manager.camera.set_delay_time
    def set_delay(delay):
        if delay == pytest.approx(3000e-9):
            raise RuntimeError("restore failed")
        original(delay)
    monkeypatch.setattr(manager.camera, "set_delay_time", set_delay)
    with pytest.raises(RuntimeError, match="camera state unknown"):
        characterize_delay(manager, "test", 2, window, lambda: False, lambda _: None)
    report = json.loads(filesystem_path(manager.storage_dir / "camera_delay_test.json").read_text())
    assert not report["complete"] and "cleanup failed" in report["error"]
    assert manager.camera.state_unknown
    assert manager.camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert not manager.camera.get_external_trigger_input_enabled()


def test_delay_grid_and_range_validation():
    values = delay_points_ns((0, 1, 10e-9))
    assert len(values) == 15 and len(set(values)) == 15
    assert values[0] == 0 and values[4] == 100 and values[5] == 1900 and values[-1] == 2100
    assert all(value % 10 == 0 for value in values)
    for limits in ((0, 1, 100e-9), (1e-9, 1, 1e-9), (0, 2000e-9, 1e-9)):
        with pytest.raises(ValueError):
            delay_points_ns(limits)


@pytest.mark.parametrize("fault", ["mode", "input"])
def test_reference_rejects_unverified_external_trigger(manager, monkeypatch, fault):
    """Never start a recorder if trigger configuration silently fails to take effect."""
    camera = manager.camera
    if fault == "mode":
        monkeypatch.setattr(camera, "set_trigger_mode", lambda mode: None)
    else:
        monkeypatch.setattr(camera, "set_external_trigger_input_enabled", lambda enabled: None)
    with pytest.raises(RuntimeError, match="requires External Exposure Start"):
        acquire_delay_reference(manager, "invalid_trigger", 3, lambda: False, lambda _: None)
    assert not list(manager.storage_dir.glob("*.h5*"))
    assert manager.state == "IDLE"
    assert camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert not camera.get_external_trigger_input_enabled()


def test_late_last_frame_finishes_before_next_delay(manager, monkeypatch):
    """Hold a final frame in flight; no new delay is applied until it is saved and stopped."""
    window = select(manager)
    camera = manager.camera
    original_iter, original_set = camera.iter_frames, camera.set_delay_time
    waiting, release = Event(), Event()
    active = Event()
    settings, errors = [], []

    def set_delay(value):
        assert not active.is_set(), "Delay changed while a frame was still in flight"
        settings.append(value)
        original_set(value)

    def delayed_frames(count, **kwargs):
        active.set()
        frozen_delay = camera.get_delay_time()
        try:
            for index, (images, metadata) in enumerate(original_iter(count, **kwargs)):
                if len(settings) == 1 and index == count - 1:
                    waiting.set()
                    assert release.wait(5), "Test did not release the late frame"
                assert camera.get_delay_time() == frozen_delay
                yield images, metadata
        finally:
            active.clear()

    monkeypatch.setattr(camera, "set_delay_time", set_delay)
    monkeypatch.setattr(camera, "iter_frames", delayed_frames)
    def run():
        try:
            characterize_delay(manager, "test", 3, window, lambda: False, lambda _: None)
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run)
    thread.start()
    try:
        assert waiting.wait(5)
        assert settings == [0.0]
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and not errors
    report = json.loads(filesystem_path(manager.storage_dir / "camera_delay_test.json").read_text())
    for index, point in enumerate(report["points"], 1):
        with h5py.File(filesystem_path(point["source_files"][0]), "r") as source:
            for value in source["frame_metadata/json"][:]:
                context = json.loads(value)["acquisition_context"]
                assert context["delay_point_index"] == index
                assert context["actual_delay_ns"] == pytest.approx(point["actual_delay_ns"])


@pytest.mark.parametrize("recorder_running,sensor_state", [(True, "off"), (False, "on"), (False, None)])
def test_physical_boundary_rejects_active_or_unknown_sensor(monkeypatch, recorder_running, sensor_state):
    """A recorder stop alone does not prove the physical camera is idle."""
    from hhg_control.camera.drivers import pco_edge
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
    camera._is_connected = True
    camera._cam = SimpleNamespace(
        is_recording=recorder_running,
        sdk=SimpleNamespace(get_recording_state=lambda: {"recording state": sensor_state}),
    )
    with pytest.raises(RuntimeError, match="Cannot verify acquisition stopped"):
        camera.verify_acquisition_stopped()
    assert camera.state_unknown


def test_unfinished_recorder_blocks_next_delay_and_cleanup_changes(manager, monkeypatch):
    """Retain acquired data and refuse all setting changes if the stop cannot be verified."""
    window = select(manager)
    calls = []
    original_set = manager.camera.set_delay_time
    original_iter = manager.camera.iter_frames
    stopped = [True]
    def set_delay(value):
        calls.append(value)
        original_set(value)
    def frames(count, **kwargs):
        yield from original_iter(count, **kwargs)
        stopped[0] = False
    def verify():
        if not stopped[0]:
            manager.camera.state_unknown = True
            raise RuntimeError("Cannot verify acquisition stopped; camera state unknown.")
    monkeypatch.setattr(manager.camera, "verify_acquisition_stopped", verify)
    monkeypatch.setattr(manager.camera, "iter_frames", frames)
    monkeypatch.setattr(manager.camera, "set_delay_time", set_delay)
    with pytest.raises(RuntimeError, match="Cannot verify acquisition stopped"):
        characterize_delay(manager, "test", 3, window, lambda: False, lambda _: None)
    assert calls == [0.0]
    assert len(list(manager.storage_dir.glob("*.h5"))) == 2
    report = json.loads(filesystem_path(manager.storage_dir / "camera_delay_test.json").read_text())
    assert not report["complete"] and not report["points"]


def test_physical_fifo_path_assigns_recording_delay_not_arrival_time(tmp_path, monkeypatch):
    """Use the real physical driver with a fake SDK and repeated IDs across fresh recorders."""
    from hhg_control.camera.drivers import pco_edge
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)

    class RecorderCamera:
        description = {"min delay time": 0, "max delay time": 1, "min delay step": 10e-9}
        configuration = {"roi": (1, 1, 8, 4)}
        exposure_time, delay_time = 0.001, 3000e-9
        is_recording = False
        mode = TriggerMode.AUTO_SEQUENCE.value
        enabled = "off"

        def __init__(self):
            self.recordings = []
            self.sdk = SimpleNamespace(
                get_recording_state=lambda: {"recording state": "on" if self.is_recording else "off"},
                get_trigger_mode=lambda: {"trigger mode": self.mode},
                set_trigger_mode=lambda mode: setattr(self, "mode", mode),
                arm_camera=lambda: None,
                get_hwio_signal=lambda index: {"enabled": self.enabled, "polarity": "rising edge"},
                get_camera_setup=lambda: {"setup": (1, 0, 0, 0)},
            )
            self.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})

        def configureHWIO_1_exposureTrigger(self, enabled, polarity):
            self.enabled = "on" if enabled else "off"

        def record(self, number_of_images, mode):
            assert not self.is_recording and mode == "fifo"
            self.recorded_delay = self.delay_time
            self.recordings.append(self.recorded_delay)
            self.frame_index = 0
            self.is_recording = True

        def wait_for_new_image(self, **kwargs):
            assert self.is_recording

        def image(self, image_index):
            assert self.delay_time == self.recorded_delay
            self.frame_index += 1
            # A frame's pixel value encodes the setting when its recorder started.
            return (np.full((4, 8), round(self.recorded_delay * 1e9), dtype=np.uint16),
                    {"recorder image number": self.frame_index})

        def stop(self):
            self.is_recording = False

    camera = pco_edge.PcoEdgeCamera()
    sdk_camera = RecorderCamera()
    camera._cam, camera._is_connected = sdk_camera, True
    camera._info = {"model": "FAKE SDK", "width": 8, "height": 4}
    manager = CameraScanManager(camera, tmp_path)
    reference = acquire_delay_reference(manager, "physical_path", 3, lambda: False, lambda _: None)
    window = make_intensity_window((1, 1, 7, 3), reference)
    report_path = characterize_delay(manager, "physical_path", 3, window, lambda: False, lambda _: None)
    report = json.loads(filesystem_path(report_path).read_text())
    assert report["complete"] and len(sdk_camera.recordings) == 16
    for point in report["points"]:
        with h5py.File(filesystem_path(point["source_files"][0]), "r") as source:
            assert np.all(source["images"][:] == round(point["actual_delay_ns"]))
            metadata = [json.loads(value) for value in source["frame_metadata/json"][:]]
            assert [value["frame_id"] for value in metadata] == [1, 2, 3]
            assert all(value["acquisition_context"]["actual_delay_ns"] == pytest.approx(point["actual_delay_ns"])
                       for value in metadata)
    assert camera.get_delay_time() == pytest.approx(3000e-9)
    assert not sdk_camera.is_recording


def test_physical_delay_adapter_uses_seconds_and_verifies_readback(monkeypatch):
    """Exercise the physical driver with an SDK stand-in; never discover/connect hardware."""
    from hhg_control.camera.drivers import pco_edge
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    from hhg_control.camera.drivers.pco_edge import PcoEdgeCamera
    camera = PcoEdgeCamera()
    fake = SimpleNamespace(description={"min delay time": 0, "max delay time": 1,
                                        "min delay step": 10e-9},
                           delay_time=0, exposure_time=0.01, is_recording=False)
    camera._cam, camera._is_connected = fake, True
    camera.set_delay_time(2000e-9)
    assert fake.delay_time == pytest.approx(2000e-9)
    assert fake.exposure_time == 0.01
    for value in (-1, float("nan"), 25e-9):
        with pytest.raises(CameraSafetyError):
            camera.set_delay_time(value)
    fake.is_recording = True
    with pytest.raises(RuntimeError, match="Stop recording"):
        camera.set_delay_time(0)
    class WrongReadback:
        description = fake.description
        exposure_time, is_recording = 0.01, False
        @property
        def delay_time(self):
            return 0
        @delay_time.setter
        def delay_time(self, value):
            pass
    camera._cam = WrongReadback()
    with pytest.raises(RuntimeError, match="readback"):
        camera.set_delay_time(20e-9)
    assert camera.state_unknown


def test_physical_external_trigger_input_preserves_edge_and_verifies_readback(monkeypatch):
    """Exercise HWIO-1 enable logic using an SDK stand-in, without camera discovery."""
    from hhg_control.camera.drivers import pco_edge
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    from hhg_control.camera.drivers.pco_edge import PcoEdgeCamera
    camera = PcoEdgeCamera()
    state = {"enabled": "off", "polarity": "falling edge", "type": "TTL",
             "filter": "off", "selected": 0, "parameter": [0, 0, 0, 0]}
    fake = SimpleNamespace(
        is_recording=False,
        sdk=SimpleNamespace(get_hwio_signal=lambda index: dict(state)),
    )
    def configure(enabled, polarity):
        state.update(enabled="on" if enabled else "off", polarity=polarity)
    fake.configureHWIO_1_exposureTrigger = configure
    camera._cam, camera._is_connected = fake, True
    assert not camera.get_external_trigger_input_enabled()
    assert camera.get_external_trigger_input_settings()["polarity"] == "falling edge"
    camera.set_external_trigger_input_enabled(True)
    assert camera.get_external_trigger_input_enabled()
    assert state["polarity"] == "falling edge"
    camera.set_external_trigger_input_enabled(False)
    assert not camera.get_external_trigger_input_enabled()
    assert state["polarity"] == "falling edge"
    for invalid in (1, "on", None):
        with pytest.raises(ValueError, match="bool"):
            camera.set_external_trigger_input_enabled(invalid)


def test_diagnostics_window_moves_buttons_and_keeps_stop_available(qtbot):
    from hhg_control.gui.camera.camera_panel import CameraMainWindow
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.show()
    window.btn_diagnostics.click()
    assert window.diagnostics_window.isVisible()
    for button in (window.btn_spectral_covariance, window.btn_mark_harmonics, window.btn_harmonic_g2):
        assert button.parentWidget() is window.diagnostics_window
    assert window.spn_delay_frames.value() == 2000
    assert window.spn_delay_frames.minimum() == 2
    assert window.delay_diagnostic.active is False
    window.diagnostics_window.close()
    window.close()


@pytest.mark.parametrize("live", [False, True])
def test_gui_full_workflow_with_only_rectangle_selection(qtbot, manager, monkeypatch, live):
    """Check the editable frame count; use small acquisitions for GUI lifecycle checks."""
    from hhg_control.gui.camera import diagnostics
    from hhg_control.gui.camera.camera_panel import CameraMainWindow
    original_characterize = diagnostics.characterize_delay
    original_reference = diagnostics.acquire_delay_reference
    def bounded_reference(manager, run_id, count, abort, log, **kwargs):
        assert count == 2468
        assert "External Exposure Start" in kwargs["start_message"]
        return original_reference(manager, run_id, 3, abort, log, **kwargs)
    def bounded_characterize(manager, run_id, count, selected_window, abort, log):
        assert count == 2468
        return original_characterize(manager, run_id, 3, selected_window, abort, log)
    monkeypatch.setattr(diagnostics, "acquire_delay_reference", bounded_reference)
    monkeypatch.setattr(diagnostics, "characterize_delay", bounded_characterize)
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera, window.scan_manager = manager.camera, manager
    window._connected_source = "simulated"
    window.spn_frames.setValue(3)
    window.spn_delay_frames.setValue(2468)
    controller = window.delay_diagnostic
    launched = []
    monkeypatch.setattr(window, "_launch_analysis_script", lambda *args: launched.append(args))
    def choose(reference):
        controller.window_path.write_text(json.dumps(make_intensity_window((1, 2, 8, 6), reference)))
        # Use an inert process object to exercise _selected's normal continuation.
        from PyQt6.QtCore import QProcess
        controller.selector = QProcess(controller)
        controller._selected(0, QProcess.ExitStatus.NormalExit)
    monkeypatch.setattr(controller, "_select", choose)
    if live:
        window._start_live()
        qtbot.waitUntil(lambda: bool(getattr(manager.camera, "_live_active", False)), timeout=5000)
    controller.start()
    assert controller.count == 2468 and window.spn_frames.value() == 3
    assert controller.active and not window.control_panel.isEnabled()
    assert not window.spn_delay_frames.isEnabled()
    assert not window.btn_go.isEnabled() and not window.cmb_camera_source.isEnabled()
    assert window.btn_stop.isEnabled()
    qtbot.waitUntil(lambda: not controller.active, timeout=15000)
    assert window.control_panel.isEnabled()
    assert window.spn_delay_frames.isEnabled()
    assert window.btn_go.isEnabled() and window.cmb_camera_source.isEnabled()
    assert len(launched) == 1 and launched[0][0] == "plot_camera_delay.py"
    assert manager.camera.get_delay_time() == pytest.approx(3000e-9)
    window.camera = MockPcoCamera()  # fixture owns the connected camera
    window.close()


def test_gui_stop_cancels_selection_without_scanning(qtbot, manager, monkeypatch):
    """Run the actual reference worker/selector process and cancel it through the main STOP."""
    from hhg_control.gui.camera.camera_panel import CameraMainWindow
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera, window.scan_manager = manager.camera, manager
    controller = window.delay_diagnostic
    launched = []
    monkeypatch.setattr(window, "_launch_analysis_script", lambda *args: launched.append(args))
    controller.start()
    qtbot.waitUntil(lambda: controller.selector is not None, timeout=10000)
    window._on_stop_clicked()
    qtbot.waitUntil(lambda: not controller.active, timeout=10000)
    assert not launched and controller.task is None and controller.selector is None
    assert len(list(manager.storage_dir.glob("*.h5"))) == 1
    assert manager.camera.get_delay_time() == pytest.approx(3000e-9)
    assert manager.camera.get_trigger_mode() == TriggerMode.AUTO_SEQUENCE
    assert not manager.camera.get_external_trigger_input_enabled()
    window.camera = MockPcoCamera()
    window.close()
