"""Mock-only acceptance of the acquisition path used by the measurement button."""

import json
import threading
from queue import Queue

import h5py
import numpy as np
import pytest

from hhg_control.drivers.base_camera import TriggerMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer import capture_pipeline as pipeline
from hhg_control.sequencer.scan_manager import AcquisitionAborted, AcquisitionBackpressure, CameraScanManager, _Timings


@pytest.fixture
def camera():
    cam = MockPcoCamera()
    cam.connect()
    cam.set_roi((0, 1072, 64, 1088))
    cam.set_exposure_time(0.001)
    yield cam
    cam.close()


def save_source(camera, folder, *, frames=19, batch=4, poll=lambda: None):
    """Build a tiny production file/reference pair without spawning a camera."""
    source = pipeline.BufferedCamera(camera, frames, batch, 512 * 2**20, 0, poll)
    manager = CameraScanManager(source, folder)
    try:
        path, last = manager._acquire_and_save_step("source", 0, "Delay (fs)", 1.0, frames,
            run_id="test", batch_size=batch, queue_batches=1, abort_check=None, source=source)
        return path, source.finish_manifest(), last
    finally:
        source.close()


def test_measurement_scan_uses_reader_and_finishes_all_checks(camera, tmp_path):
    """Normal GUI sequencer completion means every step is synced and verified."""
    camera.set_trigger_mode(TriggerMode.EXTERNAL_EXPOSURE_START)
    manager = CameraScanManager(camera, tmp_path)
    starts, reader_threads = [], []
    original = camera.iter_frames

    def observed(*args, **kwargs):
        assert len(starts) == len(reader_threads) + 1
        reader_threads.append(threading.current_thread().name)
        yield from original(*args, **kwargs)

    camera.iter_frames = observed
    rows = list(manager.execute_scan("production", "Delay (fs)", 1.0, 0.5, 2, 9,
                                    on_step_start=lambda step, _: starts.append(step)))
    assert starts == [0, 1]
    assert reader_threads == ["camera-sdk-reader"] * 2
    assert manager.camera is camera and manager.state == "IDLE"
    assert manager.last_scan_pipeline["files_verified"] == 2
    assert manager.last_step_timing["reader"]["reader_stopped"]
    for step, _, path, preview in rows:
        record = json.loads(path.with_suffix(".verification.json").read_text())
        assert record["status"] == "VERIFIED"
        assert record["frames_verified"] == 9 and record["pixels_equal_source"]
        assert record["external_trigger_accounting"] == "NOT_TESTED"
        with h5py.File(path) as h5:
            assert h5.attrs["trigger_mode"] == TriggerMode.EXTERNAL_EXPOSURE_START.value
            assert h5.attrs["scan_step_index"] == step
            assert h5.attrs["schema_version"] == "2.0"
            assert h5["images"].compression is None
            assert np.array_equal(preview, h5["images"][-1])
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("file_frames", [1, 3, 4, 9])
def test_continuous_measurement_slices_one_iterator_without_frame_loss(camera, tmp_path, file_frames):
    calls = []
    original = camera.iter_frames
    def counted(*args, **kwargs):
        calls.append(args[0])
        yield from original(*args, **kwargs)
    camera.iter_frames = counted
    manager = CameraScanManager(camera, tmp_path)
    rows = list(manager.execute_scan("continuous", "metadata", 0, 1, 3, file_frames, continuous=True))
    assert calls == [3 * file_frames]
    identifiers = []
    for _, _, path, last in rows:
        record = json.loads(path.with_suffix(".verification.json").read_text())
        assert record["status"] == "VERIFIED" and record["frames_verified"] == file_frames
        with h5py.File(path) as h5:
            assert len(h5["images"]) == file_frames
            identifiers.extend(json.loads(item)["frame_id"] for item in h5["frame_metadata/json"])
            assert np.array_equal(last, h5["images"][-1])
    assert identifiers == list(range(identifiers[0], identifiers[0] + 3 * file_frames))
    assert manager.last_scan_pipeline["continuous_recording"]
    assert not any(t.name == "camera-sdk-reader" for t in threading.enumerate())


def test_stop_between_continuous_files_closes_reader_before_finishing_checks(camera, tmp_path, monkeypatch):
    stop = threading.Event()
    manager = CameraScanManager(camera, tmp_path)
    original_finish = pipeline.ClosedFilePipeline.finish
    def observed_finish(workers):
        assert not any(t.name == "camera-sdk-reader" for t in threading.enumerate())
        original_finish(workers)
    monkeypatch.setattr(pipeline.ClosedFilePipeline, "finish", observed_finish)
    iterator = manager.execute_scan("continuous_stop", "metadata", 0, 1, 3, 9,
                                    continuous=True, abort_check=stop.is_set)
    first = next(iterator)
    stop.set()
    assert list(iterator) == []
    assert len(list(tmp_path.glob("*.h5"))) == 1
    assert json.loads(first[2].with_suffix(".verification.json").read_text())["status"] == "VERIFIED"


def test_continuous_resume_preserves_step_numbers_and_does_not_overwrite(camera, tmp_path):
    manager = CameraScanManager(camera, tmp_path)
    first = list(manager.execute_scan("resume", "metadata", 10, 2, 3, 5,
                                     start_step=1, run_id="same", continuous=True))
    second = list(manager.execute_scan("resume", "metadata", 10, 2, 3, 5,
                                      start_step=1, run_id="same", continuous=True))
    assert [row[0] for row in first] == [1, 2]
    assert [row[1] for row in first] == [12, 14]
    assert {row[2] for row in first}.isdisjoint(row[2] for row in second)
    assert len(list(tmp_path.glob("*.h5"))) == 4


def test_continuous_reader_rejects_cross_file_frame_id_gap(camera, tmp_path):
    original = camera.iter_frames
    def skipped(*args, **kwargs):
        for index, (images, metadata) in enumerate(original(*args, **kwargs)):
            if index == 1:
                metadata[0]["frame_id"] += 1
            yield images, metadata
    camera.iter_frames = skipped
    manager = CameraScanManager(camera, tmp_path)
    with pytest.raises(RuntimeError, match="frame-ID gap"):
        list(manager.execute_scan("gap", "metadata", 0, 1, 3, 4, continuous=True))
    assert manager.state == "IDLE"
    assert not any(t.name == "camera-sdk-reader" for t in threading.enumerate())


@pytest.mark.parametrize("read_frames", [1, 3, 11, 30])
def test_pixel_checks_cover_read_and_source_block_boundaries(camera, tmp_path, read_frames):
    path, manifest, _ = save_source(camera, tmp_path)
    checked = pipeline.verify_file(path, manifest, read_bytes=read_frames * 64 * 16 * 2)
    assert checked["frames_verified"] == 19
    assert checked["pixels_equal_source"] and checked["consecutive_frame_ids"]


@pytest.mark.parametrize("damage", ["pixels", "ids", "settings", "manifest"])
def test_every_integrity_failure_is_detected(camera, tmp_path, damage):
    path, manifest, _ = save_source(camera, tmp_path)
    if damage == "manifest":
        lines = manifest.read_text().splitlines()
        manifest.write_text("\n".join(lines[:-1]) + "\n")
    else:
        with h5py.File(path, "r+") as h5:
            if damage == "pixels":
                h5["images"][8, 1, 2] ^= np.uint16(1)
            elif damage == "ids":
                meta = json.loads(h5["frame_metadata/json"][6])
                meta["frame_id"] += 1
                h5["frame_metadata/json"][6] = json.dumps(meta)
            else:
                h5.attrs["trigger_mode"] = "wrong trigger"
    with pytest.raises(ValueError):
        pipeline.verify_file(path, manifest)


def test_spawned_verifier_rejects_corruption_and_preserves_data(camera, tmp_path):
    path, manifest, _ = save_source(camera, tmp_path)
    with h5py.File(path, "r+") as h5:
        h5["images"][1, 0, 0] ^= np.uint16(1)
    workers = pipeline.ClosedFilePipeline()
    try:
        workers.start()
        workers.submit(path, manifest)
        with pytest.raises(RuntimeError, match="pixels differ"):
            workers.finish()
    finally:
        workers.close()
    assert path.exists() and manifest.exists()
    assert json.loads(path.with_suffix(".verification.json").read_text())["status"] == "FAILED"
    assert not workers.sync.is_alive() and not workers.verify.is_alive()


def test_sync_failure_never_reports_success(camera, tmp_path):
    path, manifest, _ = save_source(camera, tmp_path)
    manifest.unlink()
    workers = pipeline.ClosedFilePipeline()
    try:
        workers.start()
        workers.submit(path, manifest)
        with pytest.raises(RuntimeError, match="sync/pixel verification failed"):
            workers.finish()
    finally:
        workers.close()
    assert json.loads(path.with_suffix(".verification.json").read_text())["status"] == "FAILED"
    assert path.exists()


def test_dead_worker_cannot_leave_a_silent_infinite_wait():
    workers = pipeline.ClosedFilePipeline()
    try:
        workers.start()
        workers.sync.terminate()
        workers.sync.join(timeout=5)
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            workers.collect()
    finally:
        workers.close()


def test_durable_scan_rate_uses_final_sync_not_verification_completion():
    """Controlled timestamps cover file transitions without real sleep timing."""
    workers = pipeline.ClosedFilePipeline()
    workers.results.close()
    workers.results = Queue()
    tasks = [
        {"path": "first", "timeout_s": 120, "synced_at": 110.0,
         "acquisition": {"started_at": 100.0, "writer": {"written_frames": 40}}},
        {"path": "second", "timeout_s": 120, "synced_at": 120.0,
         "acquisition": {"started_at": 115.0, "writer": {"written_frames": 60}}},
    ]
    try:
        for task in tasks:
            workers.pending[task["path"]] = task
            workers.results.put(("synced", task))
        workers.collect()
        for task in tasks:
            workers.results.put(("verified", {**task, "verified_at": 999.0}))
        workers.collect()
        assert workers.completed == 2
        assert workers.durable_summary() == {
            "frames_synced": 100, "durable_elapsed_s": 20.0, "durable_fps": 5.0}
    finally:
        # The in-memory results control has no OS queue handle to close.
        workers.results.close = lambda: None
        workers.close()


def test_reader_queue_stops_unpausable_camera_instead_of_dropping(camera, monkeypatch):
    monkeypatch.setattr(type(camera), "can_pause_acquisition", property(lambda self: False))
    source = pipeline.BufferedCamera(camera, 100, 4, 3 * 4 * 64 * 16 * 2, 0, lambda: None)
    try:
        source.reader.start()
        assert source.done.wait(timeout=5)
        assert isinstance(source.error, AcquisitionBackpressure)
        assert source.full_events == 1
        assert source.peak_batches <= source.capacity == 2
    finally:
        source.close()
    assert not source.reader.is_alive()


def test_reader_advances_while_source_hash_is_blocked(camera, tmp_path, monkeypatch):
    hashing, release, camera_done = threading.Event(), threading.Event(), threading.Event()
    original_hash, original_frames = pipeline.hashlib.sha256, camera.iter_frames

    def counted(*args, **kwargs):
        try:
            yield from original_frames(*args, **kwargs)
        finally:
            camera_done.set()

    def gated_hash(*args, **kwargs):
        hashing.set()
        if not release.wait(timeout=10):
            raise TimeoutError("Test hash gate was not released")
        return original_hash(*args, **kwargs)

    camera.iter_frames = counted
    monkeypatch.setattr(pipeline.hashlib, "sha256", gated_hash)
    errors = []

    def run():
        try:
            save_source(camera, tmp_path, frames=20)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert hashing.wait(timeout=5)
        assert camera_done.wait(timeout=5), "Source hashing blocked SDK retrieval"
    finally:
        release.set()
        thread.join(timeout=10)
    assert not thread.is_alive() and errors == []


def test_reader_owns_reusable_driver_pixels_before_reading_ahead(camera, tmp_path):
    """A driver reusing one array cannot alter a batch already queued for hashing."""
    original = camera.iter_frames
    source_images = []

    def reusable(*args, **kwargs):
        buffer = None
        for images, metadata in original(*args, **kwargs):
            if buffer is None or buffer.shape != images.shape:
                buffer = np.empty_like(images)
            buffer[:] = images
            source_images.append(images.copy())
            yield buffer, metadata

    camera.iter_frames = reusable
    path, manifest, _ = save_source(camera, tmp_path, frames=20)
    with h5py.File(path) as h5:
        assert np.array_equal(h5["images"][:], np.concatenate(source_images))
    assert pipeline.verify_file(path, manifest)["pixels_equal_source"]


def test_production_pipeline_keeps_long_path_support(camera, tmp_path):
    """Sidecar sync and spawned readback use the same extended paths as HDF5."""
    folder = tmp_path / ("measurement_" * 8) / ("scan_" * 16)
    manager = CameraScanManager(camera, folder)
    rows = list(manager.execute_scan("long_path", "setpoint", 0, 1, 1, 4))
    path = rows[0][2]
    from hhg_control.safe_io import filesystem_path
    checked = json.loads(filesystem_path(path.with_suffix(".verification.json")).read_text())
    assert checked["status"] == "VERIFIED"


def test_short_step_with_large_configured_budget_still_works_on_development_pc(camera, monkeypatch):
    monkeypatch.setattr(pipeline, "available_physical_memory_bytes", lambda: 4 * 2**30)
    source = pipeline.BufferedCamera(camera, 4, 4, 16 * 2**30, 16 * 2**30, lambda: None)
    assert not source.expanded
    assert source.capacity_bytes == 4 * 64 * 16 * 2
    source.close()


def test_expanded_reader_memory_reserve_is_enforced_before_recording(camera, monkeypatch):
    monkeypatch.setattr(pipeline, "available_physical_memory_bytes", lambda: int(1.25 * 2**30))
    with pytest.raises(MemoryError, match="preflight"):
        pipeline.BufferedCamera(camera, 1_000_000, 4, 2**30, 2**30, lambda: None)


def test_stop_before_scan_starts_does_not_start_camera_or_publish_files(camera, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError("An already stopped scan started the camera")
    camera.iter_frames = unexpected
    manager = CameraScanManager(camera, tmp_path)
    assert list(manager.execute_scan("stop", "setpoint", 0, 1, 1, 20, abort_check=lambda: True)) == []
    assert manager.state == "IDLE" and not list(tmp_path.iterdir())


def test_stop_before_reader_start_never_sends_sdk_commands(camera, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError("An already stopped reader started the camera")
    camera.iter_frames = unexpected
    source = pipeline.BufferedCamera(camera, 4, 4, 512 * 2**20, 0, lambda: None)
    source.prepare_manifest(tmp_path / "stop.h5", {
        "num_frames": 4, "camera_serial": "MOCK", "roi_bounds": camera.get_roi(), "exposure_time_s": .001,
        "trigger_mode": "auto sequence", "readout_mode": "ROLLING_SHUTTER", "scan_step_index": 0})
    try:
        with pytest.raises(AcquisitionAborted):
            next(source.iter_frames(4, stop_check=lambda: True))
        assert source.reader.ident is None
    finally:
        source.close()


def test_stop_retains_partial_and_releases_camera_owner(camera, tmp_path):
    stop = threading.Event()
    original = camera.iter_frames

    def interrupted(*args, **kwargs):
        for images, metadata in original(*args, **kwargs):
            stop.set()
            yield images, metadata

    camera.iter_frames = interrupted
    manager = CameraScanManager(camera, tmp_path)
    assert list(manager.execute_scan("stop", "setpoint", 0, 1, 2, 100, abort_check=stop.is_set)) == []
    assert manager.state == "IDLE" and camera.is_connected
    assert len(list(tmp_path.glob("*.h5.partial"))) == 1
    assert not list(tmp_path.glob("*.h5"))
    assert not any(t.name == "camera-sdk-reader" for t in threading.enumerate())


def test_long_run_timing_history_is_bounded_without_losing_totals():
    values = _Timings()
    for _ in range(10_000):
        values.append(1.0)
    assert len(values.samples) == 4096
    assert values.total == 10_000.0 and values.maximum == 1.0


def test_gui_measurement_button_waits_for_verified_completion(qtbot, camera, tmp_path, monkeypatch):
    from hhg_control.ui.camera.camera_panel import CameraMainWindow
    from PyQt6.QtWidgets import QMessageBox
    def unexpected_warning(*args):
        pytest.fail(f"Unexpected GUI warning: {args[1:]}")
    monkeypatch.setattr(QMessageBox, "warning", unexpected_warning)
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window._on_camera_connected(camera, True, 0.0)
    window.txt_storage_dir.setText(str(tmp_path))
    window.scan_manager.set_storage_dir(tmp_path)
    for widget, value in zip((window.spn_roi_x0, window.spn_roi_y0,
                              window.spn_roi_x1, window.spn_roi_y1), camera.get_roi()):
        widget.setValue(value)
    assert window.scan_manager.writer_queue_byte_budget == 16 * 2**30
    assert not hasattr(window, "spn_writer_buffer_gib")
    window.spn_frames.setValue(9)
    window.spn_num_steps.setValue(2)
    qtbot.waitUntil(lambda: not window._configuration_busy(), timeout=5000)
    window._toggle_measurement_scan()
    qtbot.waitUntil(lambda: window.active_scan_task is None, timeout=15000)
    assert "[SCAN COMPLETE][SIMULATED]" in window.txt_activity_log.toPlainText()
    assert window.scan_manager.last_scan_pipeline["files_verified"] == 2
    assert window.scan_manager.last_scan_pipeline["continuous_recording"]
    rate = window.scan_manager.last_scan_pipeline["durable_fps"]
    assert rate > 0
    assert f"[MEASUREMENT COMPLETE] Durable FPS: {rate:.1f}" in window.txt_activity_log.toPlainText()
    records = list(tmp_path.glob("*.verification.json"))
    assert len(records) == 2
    assert all(json.loads(p.read_text())["status"] == "VERIFIED" for p in records)
    window.close()
