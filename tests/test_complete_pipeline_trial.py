"""Small mock-only 4F checks, including a real spawned verifier process."""

from pathlib import Path
import json

import h5py
import pytest

from scripts import complete_pipeline_trial as trial
from scripts import lab_computer_acceptance as lab


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    class SmallMock(lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    monkeypatch.setattr(lab, "MockPcoCamera", SmallMock)
    return lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32), batch_size=2)


def test_continuous_files_use_one_iterator_and_verify_all_pixels(cfg, monkeypatch, capsys):
    original = lab.MockPcoCamera.iter_frames
    calls = []
    def tracked(self, *args, **kwargs):
        calls.append(args[0])
        yield from original(self, *args, **kwargs)
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", tracked)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
    assert capsys.readouterr().out == ""
    assert report["status"] == "EXECUTED", report.get("error")
    assert calls == [7]
    drive = report["drives"]["selected"]
    summary = drive["summary"]
    assert summary["frames_captured"] == summary["frames_written"] == summary["frames_verified"] == 7
    assert summary["files_verified"] == 2 and summary["all_files_verified"]
    assert summary["complete_verified_fps"] > 0
    assert summary["all_verified_s"] >= summary["all_synced_s"] > 0
    ids = []
    for row in drive["files"]:
        assert row["verification_status"] == "VERIFIED" and row["pixels_equal_source"]
        with h5py.File(Path(drive["output_directory"]) / row["file"]) as h5:
            assert h5.attrs["schema_version"] == "2.0" and h5.attrs["complete"]
            ids += [json.loads(item)["frame_id"] for item in h5["frame_metadata/json"][:]]
    assert ids == list(range(ids[0], ids[0]+7))
    saved = json.loads(Path(report["report_file"]).read_text())
    assert saved["source"] == "mock" and saved["section"] == "4f"
    assert saved["drives"]["selected"]["summary"]["complete_verified_fps"] > 0


def test_cross_file_frame_gap_stops_and_preserves_report(cfg, monkeypatch):
    original = lab.MockPcoCamera.iter_frames
    def gap(self, *args, **kwargs):
        count = 0
        for images, metadata in original(self, *args, **kwargs):
            if count == 4:
                metadata[0]["frame_id"] += 1
            count += len(images)
            yield images, metadata
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", gap)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
    assert report["status"] == "FAILED"
    assert "Frame-ID gap" in report["error"]
    assert Path(report["report_file"]).is_file()
    assert report["drives"]["selected"]["summary"]["whole_trial_fps"] is None


def test_worker_rejects_corrupt_pixels(cfg, tmp_path):
    path, manifest = tmp_path / "corrupt.h5", tmp_path / "corrupt.pixels.sha256.json"
    # The saved file is complete and has valid IDs, but the source hash differs.
    with h5py.File(path, "w") as h5:
        h5.attrs.update(complete=True, frames_written=1)
        h5.create_dataset("images", data=[[[1, 2]]], dtype="uint16")
    manifest.write_text(json.dumps({"shape_frame_y_x": [1, 1, 2],
                                   "blocks": [{"start": 0, "frames": 1, "sha256": "invalid"}]}))
    from queue import Queue
    tasks, results = Queue(), Queue()
    tasks.put((0, str(path), str(manifest)))
    # Isolate the pixel verifier from schema validation to exercise corruption.
    from unittest.mock import patch
    with patch.object(lab, "inspect_file", return_value={}):
        trial._verify_worker(tasks, results, cfg)
    assert results.get()[0] == "ready"
    kind, error = results.get()
    assert kind == "error" and "pixels differ" in error


def test_invalid_plan_does_not_start_worker_or_camera(cfg, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Must fail before camera/worker startup")
    monkeypatch.setattr(trial._Verifier, "start", forbidden)
    monkeypatch.setattr(lab, "configured_camera", forbidden)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=3)
    assert report["status"] == "FAILED" and "multiple" in report["error"]


def test_verification_backlog_fails_without_claiming_complete_fps(cfg, monkeypatch):
    class BlockedVerifier(trial._Verifier):
        def start(self):
            pass  # Simulate verification never completing; no child process.
        def collect(self, timeout=0):
            pass
    monkeypatch.setattr(trial, "_Verifier", BlockedVerifier)
    report = trial.run_complete_pipeline_trial(cfg, frames=6, frames_per_file=2, max_pending_files=1)
    assert report["status"] == "FAILED" and "backlog" in report["error"]
    summary = report["drives"]["selected"]["summary"]
    assert summary["frames_verified"] == 0 and summary["whole_trial_fps"] is None
    assert not summary.get("complete_verified_fps")


@pytest.mark.parametrize("budget", [40.01, 100, 200])
def test_hard_total_cap_rejects_larger_stage_before_camera(cfg, monkeypatch, budget):
    def forbidden(*args, **kwargs):
        raise AssertionError("Budget must fail before worker/camera access")
    monkeypatch.setattr(trial._Verifier, "start", forbidden)
    report = trial.run_complete_pipeline_trial(cfg, stage_output_gb=budget)
    assert report["status"] == "FAILED" and "at most 40 GB" in report["error"]
