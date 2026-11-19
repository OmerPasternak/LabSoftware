"""Fake-SDK contract tests; these tests never open physical hardware."""

from types import SimpleNamespace

import numpy as np
import pytest

import hhg_control.drivers.pco_edge as pco_edge


class FakePcoCamera:
    def __init__(self, interface: str):
        self.interface = interface
        self.description = {"max_width": 2560, "max_height": 2160}
        self.camera_name = "pco.edge 5.5 TEST DOUBLE"
        self.camera_serial = "12345"
        self.exposure_time = 0.01
        self.configuration = {"roi": (1, 1073, 64, 1088)}
        self.is_recording = False
        self.record_calls = []
        self.image_metadata = {"recorder image number": 9}
        self.sdk = FakeSdk()

    def record(self, number_of_images=1, mode="sequence"):
        self.record_calls.append((number_of_images, mode))
        self.is_recording = True

    def wait_for_new_image(self, delay=True, timeout=None):
        self.wait_args = (delay, timeout)

    def image(self, image_index=0):
        return np.ones((16, 64), dtype=np.uint16), dict(self.image_metadata)

    def stop(self):
        self.is_recording = False

    def close(self):
        self.is_recording = False


@pytest.mark.parametrize("gap", [False, True])
def test_passive_capture_profile_keeps_sdk_calls_and_restores_camera(monkeypatch, gap):
    """Profile the real FIFO driver using only a fake SDK; detect pre-rejection IDs."""
    from scripts.capture_diagnostics import profiled_camera
    fake = FakePcoCamera("USB 3.0")
    numbers = iter((10, 12, 13, 14, 15) if gap else range(10, 15))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    def image(image_index=0):
        number = next(numbers)
        return np.ones((16, 64), dtype=np.uint16), {
            "recorder image number": number,
            "timestamp": {"year": 2026, "month": 10, "day": 15, "hour": 12, "minute": 0,
                          "second": 10 + (number - 10) / 455},
        }
    fake.image = image
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    try:
        with profiled_camera(camera) as (profiled, probe):
            assert camera._cam.sdk is fake.sdk
            assert profiled.get_trigger_mode().value == "auto sequence"
            assert profiled.get_readout_mode() is not None
            if gap:
                with pytest.raises(RuntimeError, match="frame gap"):
                    list(profiled.iter_frames(5, batch_size=2))
                assert probe.summary()["sdk_frame_id_gap_events"] == 1
            else:
                list(profiled.iter_frames(5, batch_size=2))
                result = probe.summary()
                assert result["camera_cadence_fps"] == pytest.approx(455, rel=1e-4)
                assert result["stages"]["sdk_image_copy"]["calls"] == 5
                assert result["stages"]["python_frame_handling"]["calls"] == 5
                assert result["stages"]["batch_stack"]["calls"] == 3
                assert result["stages"]["sdk_stop"]["calls"] == 1
                assert result["fifo_overflow"] is False
        assert camera._cam is fake
        assert not hasattr(camera, "acquisition_diagnostics")
        assert fake.record_calls == [(5, "fifo")]
        assert not fake.is_recording
    finally:
        camera.close()


def test_profiled_fake_sdk_through_production_writer(monkeypatch, tmp_path):
    """Check the complete metadata/SDK/writer path, not just generator iteration."""
    from scripts import lab_computer_acceptance as lab
    from scripts.capture_diagnostics import profiled_camera
    fake = FakePcoCamera("USB 3.0")
    numbers = iter(range(10, 15))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (np.ones((16, 64), dtype=np.uint16),
                                      {"recorder image number": next(numbers)})
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    try:
        cfg = lab.Settings(output_dir=tmp_path, roi=camera.get_roi(),
                           exposure_s=camera.get_exposure_time(), readout=camera.get_readout_mode(), batch_size=2)
        with profiled_camera(camera, hash_pixels=True) as (profiled, probe):
            result = lab.saved_run(profiled, cfg, "profiled_fake_sdk", 5)
            assert result["complete"] and result["frames"] == 5
            assert result["pipeline_stage_timing_s"]["captured_frames"] == 5
            assert result["pipeline_stage_timing_s"]["written_frames"] == 5
            assert len(probe.blocks) == 3
        assert camera._cam is fake
    finally:
        camera.close()


def test_pco_roi_defaults_allow_off_center_y_when_sdk_omits_symmetry_flag(monkeypatch):
    """The known off-center ROI capability remains enabled if the SDK omits its flag."""
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    try:
        assert camera.get_roi_limits()["symmetric"] == (False, False)
        camera.set_roi((100, 100, 500, 500))
        assert camera.get_roi() == (100, 100, 500, 500)
    finally:
        camera.close()


class FakeSdk:
    def __init__(self):
        self.setup = 1
        self.trigger_mode = "auto sequence"
        self.armed = False
        self.timeout = None
        self.rebooted = False

    def get_camera_setup(self):
        return {"type": 0, "setup": (self.setup, 0, 0, 0), "length": 4}

    def get_trigger_mode(self):
        return {"trigger mode": self.trigger_mode}

    def set_trigger_mode(self, mode):
        self.trigger_mode = mode

    def arm_camera(self):
        self.armed = True

    def set_timeouts(self, command_timeout=200, image_timeout=3000, transfer_timeout=200):
        self.timeout = command_timeout

    def set_camera_setup(self, setup):
        self.setup = {"rolling shutter": 1, "global shutter": 2, "global reset": 4}[setup]

    def reboot_camera(self):
        self.rebooted = True


def test_pco_sdk_symbol_is_always_defined():
    """Keep the optional SDK patchable when the vendor package is absent."""
    assert hasattr(pco_edge, "pco")


def test_pco_external_trigger_uses_sdk_and_checks_readback(monkeypatch):
    """External Exposure Start is confirmed by the PCO SDK before use."""
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    mode = pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START
    camera.set_trigger_mode(mode)
    assert fake.sdk.trigger_mode == mode.value
    assert fake.sdk.armed
    assert camera.get_trigger_mode() == mode
    fake.is_recording = True
    with pytest.raises(RuntimeError, match="Stop camera recording"):
        camera.set_trigger_mode(pco_edge.TriggerMode.AUTO_SEQUENCE)
    fake.is_recording = False
    camera.close()


def test_pco_external_trigger_rejects_failed_sdk_readback(monkeypatch):
    """Never treat an unconfirmed trigger command as a successful change."""
    fake = FakePcoCamera("USB 3.0")
    fake.sdk.set_trigger_mode = lambda mode: None
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="readback did not match"):
        camera.set_trigger_mode(pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START)
    camera.close()


def test_pco_live_uses_persistent_ring_buffer(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.start_live(buffer_size=4)
    frame, metadata = camera.acquire_live_frame(timeout_s=0.25)
    assert fake.record_calls == [(4, "ring buffer")]
    assert fake.wait_args == (True, 0.25)
    assert frame.shape == (16, 64)
    assert metadata["frame_id"] == 9
    camera.stop_live()
    assert not fake.is_recording
    camera.close()


def test_pco_live_preserves_sdk_timestamp(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    fake.image_metadata["timestamp"] = "2026-09-23T12:34:56.789Z"
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.start_live()

    _, metadata = camera.acquire_live_frame()
    assert metadata["camera_time_str"] == fake.image_metadata["timestamp"]
    assert metadata["timestamp_source"] == "pco_sdk"
    camera.close()


def test_pco_readout_mode_adapts_to_sdk_dictionary(monkeypatch):
    created = []

    def make_camera(interface, serial):
        camera = FakePcoCamera(interface)
        if created:
            camera.sdk.setup = created[-1].sdk.setup
        created.append(camera)
        return camera

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=make_camera))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.set_trigger_mode(pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START)
    camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert created[0].sdk.timeout == 2000
    assert created[0].sdk.rebooted
    assert camera.get_readout_mode() is pco_edge.ReadoutMode.GLOBAL_SHUTTER
    assert camera.get_trigger_mode() is pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START
    camera.close()


def test_pco_close_resets_rolling_shutter_and_100ms(monkeypatch):
    created = []

    def make_camera(interface, serial):
        fake = FakePcoCamera(interface)
        fake.camera_serial = str(serial)
        if created:
            fake.sdk.setup = created[-1].sdk.setup
        created.append(fake)
        return fake

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=make_camera))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    camera.set_exposure_time(0.001)

    camera.close()

    assert len(created) == 3  # Initial connect, then one reboot in each direction.
    assert created[-1].sdk.setup == pco_edge.ReadoutMode.ROLLING_SHUTTER.value
    assert created[-1].exposure_time == pytest.approx(0.1)
    assert not camera.is_connected
    assert not camera.state_unknown


def test_pco_mode_switch_rejects_mismatched_hardware_readback(monkeypatch):
    """A completed reboot is not success unless the SDK reports the new mode."""
    created = []

    def make_camera(interface, serial):
        camera = FakePcoCamera(interface)
        created.append(camera)
        return camera

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=make_camera))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="not verified"):
        camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert len(created) == 2
    camera.close()


def test_pco_global_shutter_rejects_long_exposure_before_sdk_command(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.set_exposure_time(0.2)
    with pytest.raises(pco_edge.CameraSafetyError, match="Reduce exposure"):
        camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert fake.sdk.setup == 1
    assert not fake.sdk.rebooted
    camera.close()


def test_pco_connect_requires_shutter_readback_and_closes_failed_handle(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    fake.closed = False
    fake.close = lambda: setattr(fake, "closed", True)

    def fail_readback():
        raise RuntimeError("readback failed")

    fake.sdk.get_camera_setup = fail_readback
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    with pytest.raises(ConnectionError, match="Could not verify PCO shutter mode"):
        camera.connect()
    assert fake.closed
    assert not camera.is_connected


def test_pco_saved_scan_uses_one_fifo_and_preserves_recorder_numbers(monkeypatch):
    """Read a complete sequence without restarting recording at each batch."""
    fake = FakePcoCamera("USB 3.0")
    numbers = iter(range(10, 15))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (
        np.ones((16, 64), dtype=np.uint16), {"recorder image number": next(numbers)}
    )
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    assert camera.yields_owned_batches
    batches = list(camera.iter_frames(5, batch_size=2))
    assert [len(images) for images, _ in batches] == [2, 2, 1]
    assert [meta["frame_id"] for _, metas in batches for meta in metas] == list(range(10, 15))
    assert all(
        isinstance(meta["host_frame_read_monotonic_ns"], int)
        for _, metas in batches for meta in metas
    )
    assert fake.record_calls == [(5, "fifo")]
    assert camera.last_fifo_capacity_frames == 5
    assert not fake.is_recording
    camera.close()


def test_pco_saved_scan_reads_roi_only_once(monkeypatch):
    """Avoid repeated multi-call SDK configuration reads at high frame rates."""
    fake = FakePcoCamera("USB 3.0")
    numbers = iter(range(20, 25))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (
        np.ones((16, 64), dtype=np.uint16), {"recorder image number": next(numbers)}
    )
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    roi_reads = []

    def get_roi():
        roi_reads.append(1)
        return (0, 0, 64, 16)

    monkeypatch.setattr(camera, "get_roi", get_roi)
    batches = list(camera.iter_frames(5, batch_size=2))
    assert len(roi_reads) == 1
    assert all(meta["roi"] == (0, 0, 64, 16) for _, metas in batches for meta in metas)
    camera.close()


def test_pco_fifo_capacity_is_byte_bounded_for_acceptance_roi():
    frames = pco_edge._fifo_buffer_frames(10_000, height=128, width=2560)
    allocated_bytes = frames * 128 * 2560 * 2
    assert frames == 409
    assert allocated_bytes <= pco_edge.PCO_FIFO_BUFFER_BYTES
    assert allocated_bytes > pco_edge.PCO_FIFO_BUFFER_BYTES - (128 * 2560 * 2)


def test_pco_fifo_capacity_stays_bounded_for_full_sensor():
    frames = pco_edge._fifo_buffer_frames(10_000, height=2160, width=2560)
    assert frames >= 4
    assert frames * 2160 * 2560 * 2 <= pco_edge.PCO_FIFO_BUFFER_BYTES


def test_pco_fifo_fails_on_a_missing_recorder_frame(monkeypatch):
    """A numbering gap must leave the scan incomplete rather than shift data."""
    fake = FakePcoCamera("USB 3.0")
    numbers = iter((10, 12))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (
        np.ones((16, 64), dtype=np.uint16), {"recorder image number": next(numbers)}
    )
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="frame gap"):
        list(camera.iter_frames(2, batch_size=1))
    assert not fake.is_recording
    camera.close()


def test_pco_fifo_fails_on_sdk_overflow(monkeypatch):
    """The SDK overflow flag is an acquisition failure even before copying."""
    fake = FakePcoCamera("USB 3.0")
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": True, "dwLastError": 0})
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="FIFO overflow"):
        list(camera.iter_frames(1))
    assert not fake.is_recording
    camera.close()


def test_pco_fifo_rejects_frame_shape_that_disagrees_with_roi(monkeypatch):
    """Never save a frame while labeling it with the wrong hardware ROI."""
    fake = FakePcoCamera("USB 3.0")
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (
        np.ones((5, 8), dtype=np.uint16), {"recorder image number": 1}
    )
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface, serial: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="expected \\(16, 64\\) and uint16"):
        list(camera.iter_frames(1))
    assert not fake.is_recording
    camera.close()


@pytest.mark.parametrize("defect", [None, "dtype", "shape", "missing_id", "gap", "count"])
def test_whole_stack_preserves_sdk_payload_or_rejects(monkeypatch, defect):
    fake = FakePcoCamera("USB 3.0")
    frames = [np.full((16, 64), value, dtype=np.uint16) for value in (65535, 7)]
    metadata = [{"recorder image number": 42}, {"recorder image number": 43}]
    if defect == "dtype": frames[0] = frames[0].astype(np.int32)
    if defect == "shape": frames[0] = frames[0][:1]
    if defect == "missing_id": metadata[0] = {}
    if defect == "gap": metadata[1]["recorder image number"] = 45
    if defect == "count": metadata.pop()
    fake.images = lambda: (frames, metadata)
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **_: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    try:
        if defect:
            with pytest.raises(RuntimeError): camera.acquire_frames(2)
        else:
            actual, records = camera.acquire_frames(2)
            np.testing.assert_array_equal(actual, np.stack(frames))
            assert [m["frame_id"] for m in records] == [42, 43]
        assert not fake.is_recording
    finally:
        camera.close()


def test_failed_stop_locks_all_acquisition_until_reconnect(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    fake.rec = SimpleNamespace(get_status=lambda: {})
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **_: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345"); camera.connect()
    original = fake.stop
    def fail(): raise OSError("stop unavailable")
    fake.stop = fail
    with pytest.raises(RuntimeError, match="state unknown"):
        list(camera.iter_frames(1))
    count = len(fake.record_calls)
    for action in (lambda: list(camera.iter_frames(1)), lambda: camera.acquire_frames(1), camera.start_live):
        with pytest.raises(RuntimeError, match="state unknown"): action()
    assert len(fake.record_calls) == count
    fake.stop = original
    camera.close(); camera.connect()
    camera.start_live(); camera.stop_live(); camera.close()


def test_failed_setup_and_close_retains_handle_before_retry(monkeypatch):
    fake = FakePcoCamera("USB 3.0"); factories = []
    def factory(**_): factories.append(1); return fake
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=factory))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    def fail(): raise OSError("injected failure")
    original_setup, original_close = fake.sdk.get_camera_setup, fake.close
    fake.sdk.get_camera_setup = fail; fake.close = fail
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    with pytest.raises(ConnectionError, match="close failed"): camera.connect()
    with pytest.raises(RuntimeError, match="handle retained"): camera.connect()
    assert len(factories) == 1 and camera._cam is fake
    fake.close = original_close; fake.sdk.get_camera_setup = original_setup
    camera.close(); camera.connect()
    assert len(factories) == 2
    camera.close()


@pytest.mark.parametrize("exposure", [0.000523, 0.09999, 0.1, 0.999999])
def test_restored_exposure_does_not_lose_sdk_ticks(monkeypatch, exposure):
    """Repeated readback restoration must preserve SDK ticks and trigger delay."""
    class QuantizedCamera(FakePcoCamera):
        @property
        def exposure_time(self):
            return self._exposure

        @exposure_time.setter
        def exposure_time(self, value):
            scale = 1e9 if value <= .004 else 1e6 if value <= 4 else 1e3
            self._exposure = int(value * scale) * (1 / scale)

    fake = QuantizedCamera("USB 3.0")
    delay = {"delay": 37, "delay timebase": "us"}
    fake.sdk.get_delay_exposure_time = lambda: dict(delay)
    def set_timing(count, unit, exposure_ticks, exposure_unit):
        assert (count, unit) == (37, "us")
        fake._exposure = exposure_ticks * {"ns": 1e-9, "us": 1e-6, "ms": 1e-3}[exposure_unit]
    fake.sdk.set_delay_exposure_time = set_timing
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **_: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.set_exposure_time(exposure)
    first = camera.get_exposure_time()
    for _ in range(5):
        camera.set_exposure_time(camera.get_exposure_time())
        camera._cam = None
        camera._is_connected = False
        camera.connect()  # Same restoration path used after a shutter reboot.
        assert camera.get_exposure_time() == first
    assert first == pytest.approx(exposure, rel=0, abs=1e-15)
    # A factor-of-ten hardware mismatch remains an error.
    fake.sdk.set_delay_exposure_time = lambda *_: setattr(fake, "_exposure", exposure / 10)
    with pytest.raises(RuntimeError, match="differs from request"):
        camera.set_exposure_time(exposure)
    assert camera.state_unknown


@pytest.mark.parametrize("fault", [None, "delay", "signal", "metadata", "final_arm"])
@pytest.mark.parametrize("auto_enabled", [False, True])
def test_shutter_reboot_restores_settings_and_raw_image(monkeypatch, fault, auto_enabled):
    """Model a real SDK reset on every reopen, including input polarity and delay."""
    import copy
    created = []
    class ResetCamera(FakePcoCamera):
        def auto_exposure_on(self):
            self._auto_exp_state = True
        def auto_exposure_off(self):
            self._auto_exp_state = False
        @property
        def configuration(self):
            return copy.deepcopy(self._configuration)
        @configuration.setter
        def configuration(self, value):
            self._configuration = {**getattr(self, "_configuration", {}), **value}
            if fault == "metadata" and len(created) > 1:
                self._configuration["metadata"] = "off"

    def factory(interface, serial):
        fake = ResetCamera(interface)
        if created:
            fake.sdk.setup = created[-1].sdk.setup
        fake.signal = dict(enabled="off", type="TTL", polarity="rising edge",
                           filter="off", selected=0, parameter=[0, 0, 0, 0])
        fake.timing = {"delay": 0, "delay timebase": "us", "exposure": 10000, "exposure timebase": "us"}
        fake.sdk.get_delay_exposure_time = lambda: dict(fake.timing)
        def timing(delay, delay_unit, exposure, exposure_unit):
            if not (fault == "delay" and len(created) > 1):
                fake.timing.update(delay=delay, **{"delay timebase": delay_unit})
            fake.timing.update(exposure=exposure, **{"exposure timebase": exposure_unit})
            fake.exposure_time = exposure * {"ns": 1e-9, "us": 1e-6, "ms": 1e-3}[exposure_unit]
        fake.sdk.set_delay_exposure_time = timing
        fake.signals = [fake.signal, copy.deepcopy(fake.signal), copy.deepcopy(fake.signal)]
        fake._auto_exp_state = False
        fake.configuration = {"pixel rate": 86_000_000 if not created else
                              (160_000_000 if fake.sdk.setup == 2 else 43_000_000)}
        fake.sdk.get_hwio_signal_count = lambda: {"hwio signal count": len(fake.signals)}
        fake.sdk.get_hwio_signal = lambda i: copy.deepcopy(fake.signals[i])
        def signal(index, enabled, kind, polarity, filter_value, selected, parameters):
            if not (fault == "signal" and len(created) > 1):
                fake.signals[index].update(enabled=enabled, type=kind, polarity=polarity,
                                   filter=filter_value, selected=selected, parameter=list(parameters))
        fake.sdk.set_hwio_signal = signal
        def arm():
            fake.sdk.armed = True
            if fault == "final_arm" and len(created) > 1 and fake.sdk.trigger_mode != "auto sequence":
                fake.signals[1]["polarity"] = "low level"
        fake.sdk.arm_camera = arm
        created.append(fake)
        return fake

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=factory))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    camera.set_trigger_mode(pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START)
    camera._cam.signal.update(enabled="on", polarity="falling edge", parameter=[4, 3, 2, 1])
    camera._cam.timing.update(delay=1234)
    camera._cam.signals[1].update(enabled="on", polarity="high level", selected=1)
    camera._cam.signals[2].update(polarity="low level", filter="medium")
    camera._cam._auto_exp_state = auto_enabled
    camera._cam.configuration = {"metadata": "on", "timestamp": "off", "acquire": "auto", "binning": (1, 1, "sum"), "noise filter": "off", "auto exposure": ("center", .001, .08)}
    expected_config = camera._cam.configuration
    expected_signals = copy.deepcopy(camera._cam.signals)
    expected_signal = copy.deepcopy(camera._cam.signal)
    expected_roi = camera.get_roi()
    if fault:
        with pytest.raises(RuntimeError, match="not verified"):
            camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
        assert camera.state_unknown
        return
    camera._exposure_time_s = .02  # SDK .01 s readback is authoritative, not this stale cache.
    image = np.arange(1024, dtype=np.uint16).reshape(16, 64)
    image[0, 0] = 65535
    for mode in (pco_edge.ReadoutMode.GLOBAL_SHUTTER, pco_edge.ReadoutMode.ROLLING_SHUTTER):
        camera.set_readout_mode(mode)
        assert camera._cam.signal == expected_signal
        assert camera._cam.signals == expected_signals
        assert camera._cam._auto_exp_state == auto_enabled
        actual_config = camera._cam.configuration
        assert all(actual_config[k] == v for k, v in expected_config.items() if k != "pixel rate")
        assert actual_config["pixel rate"] == (160_000_000 if mode == pco_edge.ReadoutMode.GLOBAL_SHUTTER else 86_000_000)
        assert camera._cam.timing["delay"] == 1234
        assert camera.get_roi() == expected_roi
        assert camera.get_trigger_mode() == pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START
        assert camera.get_exposure_time() == pytest.approx(.01)
        assert camera._cam.configuration["metadata"] == "on"
        camera._cam.image = lambda **_: (image.copy(), {"recorder image number": 8})
        camera.start_live()
        frame, _ = camera.acquire_live_frame()
        camera.stop_live()
        np.testing.assert_array_equal(frame, image)
        assert set(camera.last_mode_switch_timing) >= {"reboot_wait_s", "reconnect_s", "restore_and_verify_s", "total_s"}
    assert len(created) == 3
    camera.close()


def test_mock_round_trip_shutter_preserves_user_settings(tmp_path, qtbot):
    from hhg_control.camera.drivers.mock_camera import MockPcoCamera
    from hhg_control.camera.sequencer.scan_manager import CameraScanManager
    from hhg_control.gui.camera.workers import CameraModeTask
    class TinyMock(MockPcoCamera):
        WIDTH, HEIGHT = 64, 32
    camera = TinyMock()
    camera.connect()
    camera.set_exposure_time(.01)
    camera.set_delay_time(2000e-9)
    camera.set_trigger_mode(pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START)
    camera.set_external_trigger_input_enabled(True)
    manager = CameraScanManager(camera, tmp_path)
    reports = []
    try:
        for mode in (pco_edge.ReadoutMode.GLOBAL_SHUTTER, pco_edge.ReadoutMode.ROLLING_SHUTTER):
            task = CameraModeTask(manager, mode)
            task.timing_ready.connect(reports.append)
            task.run()
            assert camera.get_readout_mode() == mode
            assert camera.get_exposure_time() == .01
            assert camera.get_delay_time() == 2000e-9
            assert camera.get_external_trigger_input_enabled()
            assert camera.get_trigger_mode() == pco_edge.TriggerMode.EXTERNAL_EXPOSURE_START
        assert len(reports) == 2 and all(r["worker_total_s"] >= 0 for r in reports)
    finally:
        camera.close()


def test_shutter_switch_refuses_unknown_sdk_setting_before_reboot(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    fake.configuration["future setting"] = 7
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda **_: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera(serial="12345")
    camera.connect()
    with pytest.raises(RuntimeError, match="unreviewed SDK configuration"):
        camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert not fake.sdk.rebooted
