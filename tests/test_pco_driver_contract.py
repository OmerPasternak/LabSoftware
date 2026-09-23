"""Fake-SDK contract tests; these tests never open physical hardware."""

from types import SimpleNamespace

import numpy as np
import pytest

import hhg_control.drivers.pco_edge as pco_edge


class FakePcoCamera:
    def __init__(self, interface: str):
        self.interface = interface
        self.description = {"max_width": 2560, "max_height": 2160}
        self.camera_name = "fake pco.edge"
        self.camera_serial = "FAKE-1"
        self.exposure_time = 0.01
        self.configuration = {"roi": (1, 1073, 64, 1088)}
        self.is_recording = False
        self.record_calls = []
        self.image_metadata = {"recorder image number": 9}
        self.sdk = FakeSdk()

    def set_exposure_time(self, value):
        self.exposure_time = value

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


class FakeSdk:
    def __init__(self):
        self.setup = 1
        self.timeout = None
        self.rebooted = False

    def get_camera_setup(self):
        return {"type": 0, "setup": (self.setup, 0, 0, 0), "length": 4}

    def set_timeouts(self, command_timeout=200, image_timeout=3000, transfer_timeout=200):
        self.timeout = command_timeout

    def set_camera_setup(self, setup):
        self.setup = {"rolling shutter": 1, "global shutter": 2, "global reset": 4}[setup]

    def reboot_camera(self):
        self.rebooted = True


def test_pco_sdk_symbol_is_always_defined():
    """Keep the optional SDK patchable when the vendor package is absent."""
    assert hasattr(pco_edge, "pco")


def test_pco_live_uses_persistent_ring_buffer(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
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
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    camera.start_live()

    _, metadata = camera.acquire_live_frame()
    assert metadata["camera_time_str"] == fake.image_metadata["timestamp"]
    assert metadata["timestamp_source"] == "pco_sdk"
    camera.close()


def test_pco_readout_mode_adapts_to_sdk_dictionary(monkeypatch):
    created = []

    def make_camera(interface):
        camera = FakePcoCamera(interface)
        if created:
            camera.sdk.setup = created[-1].sdk.setup
        created.append(camera)
        return camera

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=make_camera))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert created[0].sdk.timeout == 2000
    assert created[0].sdk.rebooted
    assert camera.get_readout_mode() is pco_edge.ReadoutMode.GLOBAL_SHUTTER
    camera.close()


def test_pco_mode_switch_rejects_mismatched_hardware_readback(monkeypatch):
    """A completed reboot is not success unless the SDK reports the new mode."""
    created = []

    def make_camera(interface):
        camera = FakePcoCamera(interface)
        created.append(camera)
        return camera

    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=make_camera))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    monkeypatch.setattr(pco_edge.time, "sleep", lambda _: None)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    with pytest.raises(RuntimeError, match="not verified"):
        camera.set_readout_mode(pco_edge.ReadoutMode.GLOBAL_SHUTTER)
    assert len(created) == 2
    camera.close()


def test_pco_global_shutter_rejects_long_exposure_before_sdk_command(monkeypatch):
    fake = FakePcoCamera("USB 3.0")
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
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
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
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
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    batches = list(camera.iter_frames(5, batch_size=2))
    assert [len(images) for images, _ in batches] == [2, 2, 1]
    assert [meta["frame_id"] for _, metas in batches for meta in metas] == list(range(10, 15))
    assert all(
        isinstance(meta["host_frame_read_monotonic_ns"], int)
        for _, metas in batches for meta in metas
    )
    assert fake.record_calls == [(8, "fifo")]
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
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
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


def test_pco_fifo_fails_on_a_missing_recorder_frame(monkeypatch):
    """A numbering gap must leave the scan incomplete rather than shift data."""
    fake = FakePcoCamera("USB 3.0")
    numbers = iter((10, 12))
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": False, "dwLastError": 0})
    fake.image = lambda image_index=0: (
        np.ones((16, 64), dtype=np.uint16), {"recorder image number": next(numbers)}
    )
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    with pytest.raises(RuntimeError, match="frame gap"):
        list(camera.iter_frames(2, batch_size=1))
    assert not fake.is_recording
    camera.close()


def test_pco_fifo_fails_on_sdk_overflow(monkeypatch):
    """The SDK overflow flag is an acquisition failure even before copying."""
    fake = FakePcoCamera("USB 3.0")
    fake.rec = SimpleNamespace(get_status=lambda: {"bFIFOOverflow": True, "dwLastError": 0})
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
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
    monkeypatch.setattr(pco_edge, "pco", SimpleNamespace(Camera=lambda interface: fake))
    monkeypatch.setattr(pco_edge, "PCO_AVAILABLE", True)
    camera = pco_edge.PcoEdgeCamera()
    camera.connect()
    with pytest.raises(RuntimeError, match="expected \\(16, 64\\) and uint16"):
        list(camera.iter_frames(1))
    assert not fake.is_recording
    camera.close()
