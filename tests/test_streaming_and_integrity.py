"""Tests for bounded analysis, atomic storage, and serialized live acquisition."""

import json
import threading
import time

import h5py
import numpy as np
import pytest

from hhg_control.analysis.correlation import StreamingPixelG2, compute_g2_map
from hhg_control.analysis.hdf5_io import iter_scan_step_frames
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import (
    AcquisitionAborted,
    AcquisitionBackpressure,
    CameraScanManager,
    _frame_chunk_shape,
    sanitize_filename_component,
)


def _small_camera() -> MockPcoCamera:
    camera = MockPcoCamera()
    camera.connect()
    camera.set_roi((800, 980, 1056, 1180))
    return camera


def test_streaming_g2_matches_array_implementation():
    rng = np.random.default_rng(4)
    frames = rng.integers(1, 1000, size=(7, 12, 9), dtype=np.uint16)
    accumulator = StreamingPixelG2()
    accumulator.update(frames[:3])
    accumulator.update(frames[3:])
    streamed, mean_intensity = accumulator.finalize()
    assert np.allclose(streamed, compute_g2_map(frames))
    assert np.allclose(mean_intensity, frames.mean(axis=0))


def test_atomic_hdf5_schema_metadata_and_no_overwrite(tmp_path):
    camera = _small_camera()
    manager = CameraScanManager(camera, tmp_path)
    first, _ = manager.acquire_and_save_step(
        "unsafe:/name?", 0, "delay_mm", 1.0, 5, run_id="run-1", batch_size=2
    )
    second, _ = manager.acquire_and_save_step(
        "unsafe:/name?", 0, "delay_mm", 1.0, 2, run_id="run-1", batch_size=1
    )
    assert first != second
    assert first.exists() and second.exists()
    assert not list(tmp_path.glob("*.partial"))
    assert sanitize_filename_component("../CON") == "_CON"

    with h5py.File(first, "r") as h5f:
        assert h5f.attrs["schema_version"] == "2.0"
        assert bool(h5f.attrs["complete"])
        assert len(h5f["frame_metadata/json"]) == 5
        assert np.prod(h5f["images"].chunks) * 2 <= 1024 * 1024
        assert h5f["images"].compression is None
        assert h5f.attrs["storage_compression"] == "none"
        assert h5f["images"].attrs["dimension_order"] == "[frame, y, x]"
        assert h5f.attrs["frames_written"] == 5
        first_frame_metadata = json.loads(h5f["frame_metadata/json"][0])
        assert first_frame_metadata["timestamp_source"] == "simulated_host_clock"

    batches = list(iter_scan_step_frames(first, batch_size=2))
    assert [batch.shape[0] for batch in batches] == [2, 2, 1]
    with h5py.File(first, "r") as h5f:
        ids = [json.loads(item)["frame_id"] for item in h5f["frame_metadata/json"]]
    assert ids == sorted(set(ids))
    camera.close()


def test_mock_scan_saves_more_than_ten_thousand_frames_in_batches(tmp_path, monkeypatch):
    """A long mock step is fully written without buffering its frames together."""
    monkeypatch.setattr("hhg_control.drivers.mock_camera.time.sleep", lambda _: None)
    camera = MockPcoCamera()
    camera.connect()
    camera.set_roi((0, 1072, 64, 1088))
    manager = CameraScanManager(camera, tmp_path)
    frame_count = 10_001
    try:
        path, last_frame = manager.acquire_and_save_step(
            "long_step", 0, "setpoint", 1.0, frame_count, batch_size=256,
        )
        assert last_frame.shape == (16, 64)
        assert not list(tmp_path.glob("*.partial"))
        with h5py.File(path, "r") as h5f:
            assert bool(h5f.attrs["complete"])
            assert h5f.attrs["frames_written"] == frame_count
            assert h5f["images"].shape == (frame_count, 16, 64)
            assert h5f["frame_metadata/json"].shape == (frame_count,)
    finally:
        camera.close()


def test_abort_retains_partial_file(tmp_path):
    camera = _small_camera()
    manager = CameraScanManager(camera, tmp_path)
    with pytest.raises(AcquisitionAborted):
        manager.acquire_and_save_step(
            "abort", 0, "delay_mm", 0.0, 8, run_id="run", abort_check=lambda: True
        )
    partials = list(tmp_path.glob("*.partial"))
    assert len(partials) == 1
    assert manager.state == "IDLE"
    camera.close()


def test_serialized_mock_live_lifecycle(tmp_path):
    camera = _small_camera()
    manager = CameraScanManager(camera, tmp_path)
    manager.start_live()
    assert manager.state == "LIVE"
    frame, metadata = manager.acquire_live_frame()
    assert frame.shape == (200, 256)
    assert metadata["simulated"] is True
    with pytest.raises(RuntimeError, match="busy"):
        manager.acquire_and_save_step("busy", 0, "x", 0.0, 1)
    manager.stop_live()
    assert manager.state == "IDLE"
    camera.close()


def test_bounded_writer_queue_fails_instead_of_silently_losing_frames(tmp_path):
    """A slow disk must stop capture and leave a visible partial step."""
    class SlowWriter(CameraScanManager):
        def _write_batch(self, image_dset, metadata_dset, start, images, metadata):
            time.sleep(0.2)
            super()._write_batch(image_dset, metadata_dset, start, images, metadata)

    class UnpausableCamera(MockPcoCamera):
        @property
        def can_pause_acquisition(self):
            return False

    camera = UnpausableCamera()
    camera.connect()
    camera.set_roi((800, 980, 1056, 1180))
    manager = SlowWriter(camera, tmp_path)
    with pytest.raises(AcquisitionBackpressure, match="queue filled"):
        manager.acquire_and_save_step(
            "backpressure", 0, "delay_mm", 0.0, 20,
            batch_size=1, queue_batches=1,
        )
    partials = list(tmp_path.glob("*.partial"))
    assert len(partials) == 1
    with h5py.File(partials[0], "r") as h5f:
        assert not h5f.attrs["complete"]
        assert h5f.attrs["frames_written"] < 20
    assert manager.state == "IDLE"
    camera.close()


def test_mock_waits_for_slow_writer_without_unbounded_queue(tmp_path):
    """Synthetic capture may slow down to storage speed without losing frames."""
    class SlowWriter(CameraScanManager):
        def _write_batch(self, image_dset, metadata_dset, start, images, metadata):
            time.sleep(0.08)
            super()._write_batch(image_dset, metadata_dset, start, images, metadata)

    camera = _small_camera()
    manager = SlowWriter(camera, tmp_path)
    path, _ = manager.acquire_and_save_step(
        "slow_mock", 0, "delay_mm", 0.0, 8,
        batch_size=1, queue_batches=1,
    )
    with h5py.File(path) as h5f:
        assert h5f.attrs["complete"]
        assert h5f.attrs["frames_written"] == 8
    camera.close()


@pytest.mark.parametrize("height,width", [(2160, 2560), (1072, 2144), (56, 2560)])
def test_uncompressed_chunks_fit_frame_without_padding(height, width):
    """Chunk allocation must not inflate large full-sensor scans."""
    _, rows, cols = _frame_chunk_shape(height, width)
    assert height % rows == 0
    assert width % cols == 0
    assert rows * cols * 2 <= 1024 * 1024


def test_camera_can_capture_while_writer_is_busy(tmp_path):
    """The camera producer advances before a blocked HDF5 write completes."""
    writing = threading.Event()
    release = threading.Event()

    class GatedWriter(CameraScanManager):
        def _write_batch(self, image_dset, metadata_dset, start, images, metadata):
            if start == 0:
                writing.set()
                if not release.wait(timeout=3):
                    raise TimeoutError("Test writer was not released.")
            super()._write_batch(image_dset, metadata_dset, start, images, metadata)

    class CountedCamera(MockPcoCamera):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def acquire_frames(self, num_frames):
            self.calls += 1
            return super().acquire_frames(num_frames)

    camera = CountedCamera()
    camera.connect()
    camera.set_roi((800, 980, 1056, 1180))
    manager = GatedWriter(camera, tmp_path)
    errors = []

    def run():
        try:
            manager.acquire_and_save_step(
                "overlap", 0, "x", 0.0, 4, batch_size=1, queue_batches=4
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert writing.wait(timeout=3)
        assert camera.calls > 1
    finally:
        release.set()
        thread.join(timeout=5)
        camera.close()
    assert not thread.is_alive()
    assert errors == []


def test_writer_error_retains_incomplete_step(tmp_path):
    """Disk write errors must not produce a completed scan file."""
    class BrokenWriter(CameraScanManager):
        def _write_batch(self, image_dset, metadata_dset, start, images, metadata):
            raise OSError("simulated disk failure")

    camera = _small_camera()
    manager = BrokenWriter(camera, tmp_path)
    with pytest.raises(RuntimeError, match="writer failed"):
        manager.acquire_and_save_step("disk_error", 0, "x", 0.0, 3)
    assert not list(tmp_path.glob("*.h5"))
    assert len(list(tmp_path.glob("*.partial"))) == 1
    assert manager.state == "IDLE"
    camera.close()


def test_optional_gzip_remains_matlab_readable(tmp_path):
    """Existing gzip files and new uncompressed files share one HDF5 schema."""
    camera = _small_camera()
    manager = CameraScanManager(camera, tmp_path, compression="gzip")
    path, _ = manager.acquire_and_save_step("compressed", 0, "x", 0.0, 2)
    with h5py.File(path, "r") as h5f:
        assert h5f["images"].compression == "gzip"
        assert h5f.attrs["storage_compression"] == "gzip"
        assert h5f["images"].shape == (2, 200, 256)
    camera.close()
