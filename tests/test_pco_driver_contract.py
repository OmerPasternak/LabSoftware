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
        self.configuration = {"roi": (1, 1, 2560, 2160)}
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
        return np.ones((6, 8), dtype=np.uint16), dict(self.image_metadata)

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
    assert frame.shape == (6, 8)
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
