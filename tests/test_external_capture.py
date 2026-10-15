"""Offline checks for the external-trigger bench script; never open hardware."""

import json

import h5py
import pytest

from scripts import test_external_capture as capture


@pytest.fixture
def config(tmp_path, monkeypatch):
    class SmallMock(capture.lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    monkeypatch.setattr(capture.lab, "MockPcoCamera", SmallMock)
    return capture.lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32))


def test_mock_saves_external_mode_with_no_timing_claim(config):
    report = capture.run_capture(config, frames=5)
    assert report["status"] == "MOCK_ONLY"
    assert report["pulse_accounting_verified"] is False
    assert report["software_fps_cap"] is None
    assert report["result"]["frames"] == 5
    assert report["result"]["consecutive_ids"]
    with h5py.File(report["result"]["file"], "r") as handle:
        assert handle.attrs["trigger_mode"] == capture.lab.TriggerMode.EXTERNAL_EXPOSURE_START.value
        assert handle["images"].shape == (5, 32, 64)


def test_failure_keeps_report_and_closes_camera(config, monkeypatch):
    closed = []
    original_close = capture.lab.MockPcoCamera.close

    def close(camera):
        closed.append(True)
        original_close(camera)

    def fail(*args, **kwargs):
        assert callable(kwargs["abort_check"])
        raise TimeoutError("no external pulses")

    monkeypatch.setattr(capture.lab.MockPcoCamera, "close", close)
    monkeypatch.setattr(capture.lab, "saved_run", fail)
    report = capture.run_capture(config, frames=5)
    assert report["status"] == "FAILED"
    assert "no external pulses" in report["error"]
    assert closed == [True]
    assert json.loads(capture.Path(report["report_file"]).read_text())["status"] == "FAILED"


def test_cli_defaults_to_mock(monkeypatch, tmp_path):
    seen = []

    def run(cfg, frames, max_seconds):
        seen.append(cfg)
        return {"status": "MOCK_ONLY"}

    monkeypatch.setattr(capture, "run_capture", run)
    assert capture.main(["--output-dir", str(tmp_path)]) == 0
    assert seen[0].source == "mock"
