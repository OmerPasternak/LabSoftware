"""Tests for bounded analysis, atomic storage, and serialized live acquisition."""

import json

import h5py
import numpy as np
import pytest

from hhg_control.analysis.correlation import StreamingPixelG2, compute_g2_map
from hhg_control.analysis.hdf5_io import iter_scan_step_frames
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import (
    AcquisitionAborted,
    CameraScanManager,
    sanitize_filename_component,
)


def _small_camera() -> MockPcoCamera:
    camera = MockPcoCamera(fast_simulation=True)
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
        assert h5f["images"].shuffle
        assert h5f["images"].compression == "gzip"
        assert h5f["images"].compression_opts == 1
        assert h5f["images"].attrs["dimension_order"] == "[frame, y, x]"
        assert h5f.attrs["frames_written"] == 5
        first_frame_metadata = json.loads(h5f["frame_metadata/json"][0])
        assert first_frame_metadata["timestamp_source"] == "simulated_host_clock"

    batches = list(iter_scan_step_frames(first, batch_size=2))
    assert [batch.shape[0] for batch in batches] == [2, 2, 1]
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
