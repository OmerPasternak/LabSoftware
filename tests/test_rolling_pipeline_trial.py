"""Mock-only rolling acquisition and strict, local benchmark-file recycling."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import complete_pipeline_trial as pipeline
from scripts import lab_computer_acceptance as lab
from scripts import rolling_pipeline_trial as rolling
from hhg_control.safe_io import filesystem_path


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    class SmallMock(lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32
    monkeypatch.setattr(lab, "MockPcoCamera", SmallMock)
    monkeypatch.setattr(pipeline, "available_physical_memory_bytes", lambda: 64 * 2**30)
    return lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32), batch_size=2,
                        writer_memory_reserve_gib=0)


def test_rolling_pipeline_uses_one_iterator_and_recycles_only_verified_files(cfg, monkeypatch):
    calls = []
    original = lab.MockPcoCamera.iter_frames
    def tracked(self, *args, **kwargs):
        calls.append(args[0])
        yield from original(self, *args, **kwargs)
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", tracked)
    unrelated = Path(cfg.output_dir) / "research.h5"
    unrelated.write_bytes(b"leave existing files alone")
    report = rolling.run_rolling_pipeline_trial(cfg, frames=20, frames_per_file=4,
                                               disk_budget_gb=0.21)
    assert report["status"] == "EXECUTED", report.get("error")
    assert calls == [20]
    drive = report["drives"]["selected"]
    assert drive["summary"]["frames_captured"] == drive["summary"]["frames_verified"] == 20
    assert drive["summary"]["frames_synced"] == 20
    assert drive["rolling_storage"]["recycled_files"] == 5
    assert drive["rolling_storage"]["peak_live_GB"] <= 0.21
    assert drive["rolling_storage"]["live_file_slots"] == 0
    assert all(row["recycled"] and row["pixels_equal_source"] for row in drive["files"])
    folder = filesystem_path(drive["output_directory"])
    assert not list(folder.glob("*.h5")) and not list(folder.glob("*.pixels.sha256.json"))
    journal = [json.loads(line) for line in (folder / "rolling_verification.jsonl").read_text().splitlines()]
    assert len(journal) == 5 and sum(row["frames"] for row in journal) == 20
    assert unrelated.read_bytes() == b"leave existing files alone"
    saved = json.loads(Path(report["report_file"]).read_text())
    assert saved["section"] == "4g" and saved["source"] == "mock"
    assert saved["diagnostic_settings"]["preview_copy_behavior"] == "unchanged production baseline"
    assert not saved["drives"]["selected"]["sustainability"]["long_run_validated"]


@pytest.mark.parametrize("overflow", [False, True])
def test_duration_stops_at_file_boundary_with_one_owned_iterator(cfg, monkeypatch, overflow):
    tick, stopped = [0.0], []
    def frames(self, num_frames, batch_size=2, stop_check=None):
        try:
            for start in range(0, num_frames, batch_size):
                tick[0] += 1.0
                yield np.zeros((batch_size, 32, 64), dtype=np.uint16), [
                    {"frame_id": start + j + 1} for j in range(batch_size)]
        finally:
            stopped.append(True)
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", frames)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(perf_counter=lambda: tick[0]))
    with lab.configured_camera(cfg) as camera:
        camera._cam = SimpleNamespace(rec=SimpleNamespace(get_status=lambda:
            {"bFIFOOverflow": overflow, "dwLastError": 0}))
        source = pipeline._ContinuousCamera(camera, cfg, 100, lambda: None,
            pipeline._buffer_plan(cfg, 100, 64 * 32 * 2), duration_s=3, file_frames=4)
        try:
            if overflow:
                with pytest.raises(RuntimeError, match="at rolling capture end"):
                    list(source.iter_frames(4, batch_size=2))
                    list(source.iter_frames(4, batch_size=2))
                assert not source.read_complete
            else:
                batches = list(source.iter_frames(4, batch_size=2))
                batches += list(source.iter_frames(4, batch_size=2))
                assert sum(len(images) for images, _ in batches) == 8
                assert source.frames == source.count == source.delivered == 8
                assert source.duration_reached and source.read_complete and source.done.is_set()
            assert stopped == [True]
        finally:
            source.close_iterator()


def _pair(folder, index):
    path = folder / f"BENCHMARK_ONLY_4g_{index}.h5"
    manifest = path.with_suffix(".pixels.sha256.json")
    path.write_bytes(b"x" * 4000)
    manifest.write_text("{}")
    return path, manifest


def _verified(index):
    return {"index": index, "pixels_equal_source": True, "consecutive_ids": True,
            "complete": True, "frames_verified": 2, "synced_at": 1, "verified_at": 2}


def test_slots_bound_live_storage_while_cumulative_writes_exceed_budget(tmp_path):
    folder = tmp_path / "BENCHMARK_ONLY_section_4g_test"
    folder.mkdir()
    store = rolling._RollingFiles(folder, budget_bytes=12000, slot_bytes=6000)
    for index in range(2):
        assert store.reserve(index)
        store.register(index, *_pair(folder, index))
    assert not store.reserve(2)
    row = {"frames": 2, "first_frame_id": 1, "last_frame_id": 2}
    store.recycle(_verified(0), dict(row))
    assert store.reserve(2)
    store.register(2, *_pair(folder, 2))
    store.recycle(_verified(1), dict(row))
    store.recycle(_verified(2), dict(row))
    summary = store.summary()
    assert summary["cumulative_file_bytes_written"] > 12000
    assert summary["peak_live_GB"] * 1e9 <= 12000
    assert summary["recycled_files"] == 3 and not store.live


def test_recycling_rejects_unverified_and_outside_files(tmp_path):
    folder = tmp_path / "BENCHMARK_ONLY_section_4g_test"
    folder.mkdir()
    store = rolling._RollingFiles(folder, budget_bytes=12000, slot_bytes=6000)
    assert store.reserve(0)
    paths = _pair(folder, 0)
    store.register(0, *paths)
    with pytest.raises(AssertionError, match="full successful verification"):
        store.recycle(dict(_verified(0), pixels_equal_source=False), {"frames": 2})
    assert all(p.exists() for p in paths)
    outside = _pair(tmp_path, 1)
    assert store.reserve(1)
    with pytest.raises(ValueError, match="escapes"):
        store.register(1, *outside)
    assert all(p.exists() for p in outside)


def test_verification_failure_retains_files_instead_of_recycling(cfg, monkeypatch):
    class FailingVerifier(pipeline._Verifier):
        def collect(self, timeout=0):
            if self.pending:
                raise RuntimeError("Injected verification failure")
            super().collect(timeout)
    monkeypatch.setattr(pipeline, "_Verifier", FailingVerifier)
    report = rolling.run_rolling_pipeline_trial(cfg, frames=12, frames_per_file=4,
                                               disk_budget_gb=0.21)
    assert report["status"] == "FAILED" and "Injected verification failure" in report["error"]
    drive = report["drives"]["selected"]
    assert drive["rolling_storage"]["recycled_files"] == 0
    assert list(filesystem_path(drive["output_directory"]).glob("*.h5"))
    assert not drive["summary"].get("all_files_verified", False)


def test_rolling_plan_rejects_small_budget_and_physical_frame_override_before_camera(cfg, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid rolling plan must not start hardware/workers")
    monkeypatch.setattr(lab, "configured_camera", forbidden)
    monkeypatch.setattr(pipeline._Verifier, "start", forbidden)
    report = rolling.run_rolling_pipeline_trial(cfg, disk_budget_gb=0.1)
    assert report["status"] == "FAILED" and "at least two" in report["error"]
    with pytest.raises(ValueError, match="mock-only"):
        rolling.run_rolling_pipeline_trial(replace(cfg, source="physical"), frames=4)
