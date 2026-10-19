"""Small mock-only 4F checks, including a real spawned verifier process."""

from pathlib import Path
import json
import threading
from dataclasses import replace
from math import ceil
from types import SimpleNamespace

import h5py
import pytest

from scripts import complete_pipeline_trial as trial
from scripts import lab_computer_acceptance as lab
from scripts import storage_diagnostics as storage


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    class SmallMock(lab.MockPcoCamera):
        WIDTH, HEIGHT = 64, 32

    monkeypatch.setattr(lab, "MockPcoCamera", SmallMock)
    monkeypatch.setattr(trial, "available_physical_memory_bytes", lambda: 64 * 2**30)
    return lab.Settings(output_dir=tmp_path, roi=(0, 0, 64, 32), batch_size=2,
                        writer_memory_reserve_gib=0)


@pytest.mark.parametrize("verification_mode", ["concurrent", "after_capture"])
def test_continuous_files_use_one_iterator_and_verify_all_pixels(cfg, monkeypatch, capsys, verification_mode):
    original = lab.MockPcoCamera.iter_frames
    calls = []
    def tracked(self, *args, **kwargs):
        calls.append(args[0])
        yield from original(self, *args, **kwargs)
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", tracked)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4,
                                              verification_mode=verification_mode)
    assert capsys.readouterr().out == ""
    assert report["status"] == "EXECUTED", report.get("error")
    assert calls == [7]
    drive = report["drives"]["selected"]
    summary = drive["summary"]
    assert summary["frames_captured"] == summary["frames_written"] == summary["frames_verified"] == 7
    assert summary["files_verified"] == 2 and summary["all_files_verified"]
    assert summary["files_synced"] == 2 and summary["frames_synced"] == 7
    assert summary["verification_timing_totals"]["pixel_read_calls"] == 2
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
    assert saved["diagnostic_settings"]["stage_output_GB"] == 30.0
    assert saved["diagnostic_settings"]["camera_reader"] == "dedicated_thread"
    assert drive["reader_buffer"]["read_complete"] and drive["reader_buffer"]["reader_stopped"]
    assert drive["reader_buffer"]["frames_retrieved"] == drive["reader_buffer"]["frames_delivered"] == 7
    assert not drive["sustainability"]["long_run_validated"]
    if verification_mode == "after_capture":
        ended = drive["reader_buffer"]["reader_started_at_capture_s"] + drive["reader_buffer"]["reader_elapsed_s"]
        assert summary["verification_released_at_s"] >= ended
        assert all(row["verification_started_at_s"] >= ended for row in drive["files"])


@pytest.mark.parametrize("read_frames", [2, 7])
def test_coalesced_pixel_reads_keep_hash_boundaries_and_detect_corruption(tmp_path, read_frames):
    """Read windows may split or combine reference hashes without losing coverage."""
    import hashlib
    import numpy as np
    pixels = np.arange(11 * 6, dtype=np.uint16).reshape(11, 2, 3)
    path, manifest = tmp_path / "pixels.h5", tmp_path / "pixels.json"
    with h5py.File(path, "w") as h5:
        h5.attrs.update(complete=True, frames_written=11)
        h5.create_dataset("images", data=pixels)
    blocks = [{"start": start, "frames": len(pixels[start:start+3]),
               "sha256": hashlib.sha256(memoryview(pixels[start:start+3]).cast("B")).hexdigest()}
              for start in range(0, 11, 3)]
    manifest.write_text(json.dumps({"shape_frame_y_x": list(pixels.shape), "blocks": blocks}))
    result = storage.verify_hdf5_pixels(path, manifest, read_block_bytes=read_frames * 12)
    assert result["pixels_equal_source"] and result["frames_verified"] == 11
    assert result["pixel_read_calls"] == ceil(11/read_frames)
    assert result["pixel_read_buffer_bytes"] == read_frames * 12
    with h5py.File(path, "r+") as h5:
        h5["images"][7, 0, 0] += 1
    with pytest.raises(AssertionError, match="pixels differ.*6:9"):
        storage.verify_hdf5_pixels(path, manifest, read_block_bytes=read_frames * 12)


def test_sync_completion_is_independent_of_deferred_verification(cfg, monkeypatch):
    """Synced results remain visible while all pixel checks are deliberately held."""
    from queue import Queue
    class LocalQueue(Queue):
        def close(self):
            pass
        def cancel_join_thread(self):
            pass
    class IdleProcess:
        pid = exitcode = None
        def __init__(self, **kwargs):
            pass
    monkeypatch.setattr(trial.multiprocessing, "get_context", lambda _: SimpleNamespace(
        Queue=LocalQueue, Process=IdleProcess))
    verifier = trial._Verifier(cfg, 2, 10, verification_mode="after_capture")
    try:
        for index in range(2):
            verifier.submit(index, f"{index}.h5", f"{index}.json")
            verifier.tasks.get_nowait()  # Controlled stand-in for the sync worker.
            verifier.results.put(("synced", {"index": index, "sync_started_at": index,
                                             "synced_at": index + 0.5}))
            verifier.collect()
        assert len(verifier.synced) == 2 and verifier.sync_pending == 0
        assert not verifier.completed and verifier.pending == 2 and verifier.verify_tasks.empty()
        verifier.release_verification()
        for index in range(2):
            assert verifier.verify_tasks.get_nowait()[0] == index
            verifier.results.put(("verified", {"index": index, "verified_at": index + 5,
                                               "verification_started_at": index + 4}))
        verifier.collect()
        assert len(verifier.completed) == 2 and verifier.pending == 0
    finally:
        verifier.close()


def test_sync_failure_reports_error_without_claiming_durability(cfg, tmp_path):
    from queue import Queue
    tasks, results = Queue(), Queue()
    tasks.put((0, str(tmp_path / "absent.h5"), str(tmp_path / "absent.json")))
    trial._sync_worker(tasks, results)
    assert results.get()[0] == "sync_ready"
    kind, error = results.get()
    assert kind == "error" and "Sync worker" in error and "FileNotFoundError" in error


def test_sustainability_reports_conditional_headroom_without_claiming_long_run():
    source = SimpleNamespace(buffer_samples=SimpleNamespace(rows=[
        [0, 0, 0, 0], [10, 100, 90, 5], [20, 200, 180, 10], [40, 400, 360, 20]]),
        max_pending=20, cfg=SimpleNamespace(batch_size=2), reader_started=0, reader_finished=40,
        backlog_at_reader_stop_frames=40, plan={"capacity_bytes": 1000})
    result = trial._sustainability(source, frame_bytes=10, reference_fps=100)
    assert result["status"] == "GROWING_BACKLOG" and not result["long_run_validated"]
    assert result["last_window_backlog_growth_MB_per_s"] == pytest.approx(10/1e6)
    assert result["headroom_s_if_last_window_growth_persists"] == pytest.approx(60)


def test_policy_tagged_pipeline_handles_long_windows_hdf5_paths(cfg, tmp_path):
    """Creation, publication and verification must all support deep lab checkouts."""
    cfg = replace(cfg, output_dir=tmp_path / ("nested_" + "x" * 35))
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4,
                                              verification_mode="after_capture")
    assert report["status"] == "EXECUTED", report.get("error")
    drive = report["drives"]["selected"]
    assert drive["summary"]["frames_verified"] == drive["summary"]["frames_synced"] == 7
    saved = json.loads(Path(report["report_file"]).read_text())
    assert saved["report_schema_version"] == "3.0"


@pytest.mark.parametrize("stall", ["hashing", "file_rotation", "HDF5_writer"])
def test_reader_keeps_draining_while_downstream_is_stalled(cfg, monkeypatch, stall):
    """Reproduce the old FIFO starvation: a downstream gate must not stop reads.

    Events, not assumed scheduling or throughput thresholds, establish that all
    later frames were read/stopped before releasing hashing, rotation or writing.
    """
    read_finished = threading.Event()
    owners = []
    original = lab.MockPcoCamera.iter_frames

    def tracked(self, *args, **kwargs):
        owners.append(threading.get_ident())
        try:
            yield from original(self, *args, **kwargs)
        finally:
            owners.append(threading.get_ident())
            read_finished.set()

    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", tracked)
    gates = []
    if stall == "hashing":
        original_reference = storage._PixelReferenceCamera.iter_frames
        def gate_hash(self, *args, **kwargs):
            for images, metadata in original_reference(self, *args, **kwargs):
                if not gates:
                    gates.append(True)
                    assert read_finished.wait(5), "Hashing stalled camera retrieval"
                yield images, metadata
        monkeypatch.setattr(storage._PixelReferenceCamera, "iter_frames", gate_hash)
    elif stall == "file_rotation":
        original_save = storage._WindowManager.acquire_and_save_step
        def gate_rotation(self, *args, **kwargs):
            result = original_save(self, *args, **kwargs)
            if not gates:
                gates.append(True)
                assert read_finished.wait(5), "File rotation stalled camera retrieval"
            return result
        monkeypatch.setattr(storage._WindowManager, "acquire_and_save_step", gate_rotation)
    else:
        original_write = storage._WindowManager._write_batch
        def gate_write(self, *args, **kwargs):
            if not gates:
                gates.append(True)
                assert read_finished.wait(5), "HDF5 writing stalled camera retrieval"
            return original_write(self, *args, **kwargs)
        monkeypatch.setattr(storage._WindowManager, "_write_batch", gate_write)

    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
    assert report["status"] == "EXECUTED", report.get("error")
    assert gates and len(set(owners)) == 1 and owners[0] != threading.get_ident()
    drive = report["drives"]["selected"]
    assert drive["summary"]["frames_verified"] == 7
    assert drive["reader_buffer"]["max_pending_batches"] > 0
    assert drive["reader_buffer"]["queue_full_events"] == 0


def test_reader_copies_reusable_driver_buffers_before_reading_ahead(cfg, monkeypatch):
    """A mutable SDK/mock buffer must not overwrite frames waiting in RAM."""
    import numpy as np
    def reusable(self, num_frames, batch_size=2, stop_check=None):
        images = np.empty((batch_size, 32, 64), dtype=np.uint16)
        metadata = [{"frame_id": 0, "camera_timestamp": 0} for _ in range(batch_size)]
        for start in range(0, num_frames, batch_size):
            for offset in range(batch_size):
                images[offset].fill(start + offset + 1)
                metadata[offset]["frame_id"] = start + offset + 1
            yield images, metadata
        images.fill(999)
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", reusable)
    report = trial.run_complete_pipeline_trial(cfg, frames=8, frames_per_file=4)
    assert report["status"] == "EXECUTED", report.get("error")
    drive = report["drives"]["selected"]
    pixels = []
    for row in drive["files"]:
        with h5py.File(Path(drive["output_directory"]) / row["file"]) as h5:
            pixels.extend(h5["images"][:, 0, 0].tolist())
    assert pixels == list(range(1, 9))


def test_reader_buffer_is_bounded_and_stops_on_full_without_a_consumer(cfg):
    """An exhausted RAM buffer aborts and stops its owned iterator; no drop/retry."""
    with lab.configured_camera(cfg) as camera:
        plan = trial._buffer_plan(cfg, 10, 64 * 32 * 2)
        source = trial._ContinuousCamera(camera, cfg, 10, lambda: None,
                                       dict(plan, capacity_batches=1))
        try:
            source.reader.start()
            assert source.done.wait(5), "Reader failed to stop at its finite queue limit"
            with pytest.raises(RuntimeError, match="camera-reader RAM queue filled"):
                source.check_error()
            assert source.pending.qsize() == source.max_pending == 1
            assert source.full_events == 1 and not source.read_complete
        finally:
            source.close_iterator()
        assert not source.reader.is_alive() and source.discarded_on_abort == 2


def test_reader_failure_is_reported_and_camera_iterator_is_closed(cfg, monkeypatch):
    stopped = threading.Event()
    original = lab.MockPcoCamera.iter_frames
    def overflow(self, *args, **kwargs):
        iterator = original(self, *args, **kwargs)
        try:
            yield next(iterator)
            raise RuntimeError("PCO FIFO overflow TEST DOUBLE")
        finally:
            iterator.close()
            stopped.set()
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", overflow)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
    assert report["status"] == "FAILED" and "PCO FIFO overflow TEST DOUBLE" in report["error"]
    assert stopped.is_set()
    drive = report["drives"]["selected"]
    assert drive["reader_buffer"]["reader_stopped"]
    assert not drive["summary"].get("complete_verified_fps")
    saved = json.loads(Path(report["report_file"]).read_text())
    assert saved["drives"]["selected"]["reader_buffer"]["error"]


def test_abort_stops_reader_on_its_owner_thread_before_camera_cleanup(cfg, monkeypatch):
    """Closing a file slice must cancel SDK waiting without concurrent close()."""
    waiting, stopped = threading.Event(), threading.Event()
    owners, cancelled = [], []
    original = lab.MockPcoCamera.iter_frames
    def wait_for_trigger(self, *args, stop_check=None, **kwargs):
        iterator = original(self, *args, stop_check=stop_check, **kwargs)
        owners.append(threading.get_ident())
        try:
            yield next(iterator)
            waiting.set()
            # A bounded stand-in for the driver's cancellable image wait.
            for _ in range(100):
                if stop_check():
                    cancelled.append(True)
                    return
                stopped.wait(0.05)
            raise TimeoutError("Test camera wait was not cancelled")
        finally:
            iterator.close()
            owners.append(threading.get_ident())
            stopped.set()
    monkeypatch.setattr(lab.MockPcoCamera, "iter_frames", wait_for_trigger)
    with lab.configured_camera(cfg) as camera:
        source = trial._ContinuousCamera(camera, cfg, 10, lambda: None,
                                        trial._buffer_plan(cfg, 10, 64 * 32 * 2))
        segment = source.iter_frames(4, batch_size=2)
        try:
            next(segment)
            assert waiting.wait(5)
        finally:
            # The segment is suspended mid-file: this exercises GeneratorExit.
            segment.close()
            source.close_iterator()
        assert stopped.is_set() and cancelled
        assert len(set(owners)) == 1 and owners[0] != threading.get_ident()
        assert source.done.is_set() and not source.reader.is_alive()
        assert not source.read_complete


def test_ram_reserve_is_checked_before_camera_start_and_during_reads(cfg, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Must fail RAM preflight before camera/worker startup")
    with monkeypatch.context() as patch:
        patch.setattr(trial, "available_physical_memory_bytes", lambda: 0)
        patch.setattr(trial._Verifier, "start", forbidden)
        patch.setattr(lab, "configured_camera", forbidden)
        report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
        assert report["status"] == "FAILED" and "RAM reserve need" in report["error"]

    monkeypatch.setattr(trial, "available_physical_memory_bytes", lambda:
                        0 if threading.current_thread().name == "4f-camera-reader" else 64 * 2**30)
    report = trial.run_complete_pipeline_trial(cfg, frames=7, frames_per_file=4)
    assert report["status"] == "FAILED" and "reached the configured RAM reserve" in report["error"]
    assert report["drives"]["selected"]["reader_buffer"]["reader_stopped"]


def test_default_stage_fits_drive_with_50gb_free(cfg, tmp_path, monkeypatch):
    """The default 30-GB plan fits 50 GB free under the 1.5x reserve rule."""
    cfg = replace(cfg, roi=(0, 0, 2560, 128))
    stage_gb, file_gb = 30.0, 2.0
    frame_bytes = 2560 * 128 * 2
    chunk = max(cfg.batch_size, int(file_gb * 1e9 / frame_bytes) // cfg.batch_size * cfg.batch_size)
    files_upper = ceil(stage_gb * 1e9 / (chunk * frame_bytes))
    total = int((stage_gb * 1e9 - files_upper * 100_000_000) // (frame_bytes + 4096))
    files = ceil(total / chunk)
    raw = total * frame_bytes
    reserve = total * 4096 + files * 100_000_000
    required_free = 1.5 * (raw + reserve) + 100_000_000
    assert required_free < 50e9
    monkeypatch.setattr(lab, "prepare", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=50e9))

    storage._preflight(cfg, {"C": tmp_path}, total, files, stage_gb, approved_stage=True)

    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=required_free - 1))
    with pytest.raises(OSError, match="Not enough free space"):
        storage._preflight(cfg, {"C": tmp_path}, total, files, stage_gb, approved_stage=True)


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
    assert results.get()[0] == "verify_ready"
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
