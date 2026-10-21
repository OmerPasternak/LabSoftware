"""4H: current production sequencer, raw fidelity and timing evidence boundaries."""

from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from scripts import external_trigger_trial as trial


@pytest.fixture
def config(tmp_path, monkeypatch):
    class SmallMock(trial.lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32
    monkeypatch.setattr(trial.lab, "MockPcoCamera", SmallMock)
    return trial.lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32),
                              writer_memory_reserve_gib=0)


def test_uses_one_current_gui_recording_and_verifies_every_raw_pixel(config, monkeypatch, capsys):
    calls, source_pixels = [], []
    original = trial.lab.MockPcoCamera.iter_frames
    def recorded(camera, count, **kwargs):
        calls.append(count)
        assert camera.get_trigger_mode() == trial.TriggerMode.EXTERNAL_EXPOSURE_START
        for images, metadata in original(camera, count, **kwargs):
            source_pixels.extend(image.copy() for image in images)
            yield images, metadata
    monkeypatch.setattr(trial.lab.MockPcoCamera, "iter_frames", recorded)
    report = trial.run_external_trigger_trial(config, files=2, frames_per_file=5)
    assert capsys.readouterr().out == ""
    assert report["status"] == "MOCK_ONLY", report.get("error")
    assert calls == [10]  # Neither reconnect nor recorder restart at file boundaries.
    assert report["pipeline"]["continuous_recording"]
    assert report["pipeline"]["files_verified"] == 2
    assert report["rates"]["frames_in_closed_files"] == 10
    assert report["rates"]["durable_fps"] > 0
    assert report["rates"]["complete_verified_fps"] > 0
    assert report["reader"]["reader_stopped"]
    assert report["within_output_cap"]
    assert not report["physical_acceptance"]
    assert report["timing"]["camera_timestamp"]["status"] == "UNAVAILABLE"
    assert "MOCK_ONLY" in report["interpretation"]["timing_evidence"]
    assert "NOT_ESTABLISHED" in report["interpretation"]["input_pulse_accounting"]
    saved_pixels, ids = [], []
    for row in report["files"]:
        assert row["integrity"]["status"] == "VERIFIED"
        assert row["integrity"]["pixels_equal_source"]
        with h5py.File(Path(report["output_directory"]) / row["file"]) as handle:
            assert handle.attrs["trigger_mode"] == "external exposure start & software trigger"
            assert handle.attrs["complete"]
            saved_pixels.extend(handle["images"][:])
            ids.extend(json.loads(item)["frame_id"] for item in handle["frame_metadata/json"][:])
    np.testing.assert_array_equal(saved_pixels, source_pixels)
    assert ids == list(range(ids[0], ids[0]+10))
    path = Path(report["report_file"])
    assert path.parent == config.output_dir
    assert json.loads(path.read_text())["rates"] == report["rates"]


def test_no_pulses_preserves_partial_and_failure_report(config, monkeypatch):
    closed = []
    original_close = trial.lab.MockPcoCamera.close
    def close(camera):
        closed.append(True)
        original_close(camera)
    def no_frames(*args, **kwargs):
        raise TimeoutError("no external pulses during SDK wait")
        yield  # A generator, like the real driver.
    monkeypatch.setattr(trial.lab.MockPcoCamera, "close", close)
    monkeypatch.setattr(trial.lab.MockPcoCamera, "iter_frames", no_frames)
    report = trial.run_external_trigger_trial(config, files=1, frames_per_file=4)
    assert report["status"] == "FAILED"
    assert "no external pulses" in report["error"]
    assert closed == [True]
    assert report["partial_files"]
    assert report["rates"]["durable_fps"] is None
    assert json.loads(Path(report["report_file"]).read_text())["status"] == "FAILED"


def test_acquisition_deadline_cannot_report_a_successful_short_scan(config):
    report = trial.run_external_trigger_trial(config, files=2, frames_per_file=4, max_capture_s=1e-12)
    assert report["status"] == "FAILED"
    assert report["rates"]["capture_fps"] is None
    assert "every requested" in report["error"]


def test_camera_period_multiples_are_not_trigger_loss_or_latency():
    observer = trial._TimingCamera(None, SimpleNamespace(stage=lambda *args: None))
    start = datetime(2026, 1, 1)
    observer.camera_s = [trial.capture._camera_seconds(start+timedelta(milliseconds=3*i)) for i in range(6)]
    observer.host_ns = [100_000_000+i*2_500_000 for i in range(6)]
    timing = observer.timing(1000.0, True)
    assert timing["camera_timestamp"]["fps"] == pytest.approx(1000/3, rel=1e-5)
    assert timing["host_receipt"]["fps"] == pytest.approx(400)
    assert timing["camera_timestamp"]["nominal_trigger_period_multiples"] == {"3": 5}
    assert timing["trigger_to_exposure_latency"].startswith("NOT_MEASURED")
    assert "rejection" in timing["camera_timestamp"]["note"]
    assert "nominal_trigger_period_multiples" not in observer.timing(None, True)["camera_timestamp"]
    observer.camera_s[2] = None
    assert observer.timing(1000, True)["camera_timestamp"]["fps"] is None


def test_scope_pairs_on_one_clock_and_censors_incomplete_tail(tmp_path):
    path = tmp_path / "scope.csv"
    path.write_text("trigger_s,exposure_s\n0,0.00005\n0.001,0.00207\n0.002,\n0.003,\n")
    report = trial.analyze_scope_edges(path, max_latency_s=0.0005, window_start_s=0, window_end_s=0.003)
    assert report["matched_responses"] == 2
    assert report["unmatched_eligible_triggers"] == 1
    assert report["end_censored_triggers"] == 1
    assert report["trigger_to_exposure_latency_us"]["mean"] == pytest.approx(60)
    assert report["trigger_to_exposure_latency_us"]["std"] == pytest.approx(10)
    assert report["saved_frame_accounting"].startswith("NOT_ESTABLISHED")
    with pytest.raises(ValueError, match="ambiguous"):
        trial.analyze_scope_edges(path, max_latency_s=0.0015, window_start_s=0, window_end_s=0.003)
    assert path.read_text().startswith("trigger_s,exposure_s")


@pytest.mark.parametrize("kwargs", [dict(nominal_source_hz=0), dict(files=0),
    dict(frames_per_file=100001), dict(max_capture_s=float("nan")), dict(max_output_gb=41)])
def test_invalid_parameters_never_open_camera(config, monkeypatch, kwargs):
    def forbidden(*args, **kw):
        pytest.fail("invalid test must not configure hardware")
    monkeypatch.setattr(trial.lab, "configured_camera", forbidden)
    with pytest.raises(ValueError):
        trial.run_external_trigger_trial(config, **kwargs)


def test_output_cap_failure_and_cli_default_do_not_open_physical_camera(config, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preflight failure must precede camera connection")
    monkeypatch.setattr(trial.lab, "configured_camera", forbidden)
    report = trial.run_external_trigger_trial(config, max_output_gb=0.0001)
    assert report["status"] == "FAILED" and "cap" in report["error"]
    seen = []
    def run(cfg, **kwargs):
        seen.append(cfg.source)
        return {"status": "MOCK_ONLY"}
    monkeypatch.setattr(trial, "run_external_trigger_trial", run)
    trial.main(["--output-dir", str(config.output_dir)])
    assert seen == ["mock"]
