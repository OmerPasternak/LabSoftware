from pathlib import Path
import time
"""Verify that the paced offline benchmark uses and checks the scan writer."""

from hhg_control.sequencer.scan_manager import CameraScanManager
from scripts.benchmark_capture_pipeline import run_benchmark


def test_paced_pipeline_writes_consecutive_frames_and_retains_labelled_data(tmp_path):
    result = run_benchmark(
        tmp_path, frames=8, width=16, height=8,
        fps=100.0, batch_size=2,
    )
    assert result["verified_consecutive_frame_ids"]
    assert result["verified_image_frame_markers"]
    assert result["pipeline_fps"] > 0
    assert result["durable_s"] >= result["pipeline_s"]
    assert Path(result["data_path"]).exists()
    assert Path(result["data_path"]).name.startswith("BENCHMARK_ONLY_")


def test_paced_synthetic_replay_waits_for_slow_writer(monkeypatch, tmp_path):
    """Offline pacing reports lateness instead of inventing physical FIFO loss."""
    original_write = CameraScanManager._write_batch

    def slow_write(self, image_dset, metadata_dset, start, images, metadata):
        time.sleep(0.02)
        return original_write(self, image_dset, metadata_dset, start, images, metadata)

    monkeypatch.setattr(CameraScanManager, "_write_batch", slow_write)
    result = run_benchmark(
        tmp_path, frames=20, width=16, height=8,
        fps=1000.0, batch_size=1,
    )
    assert result["verified_consecutive_frame_ids"]
    assert result["verified_image_frame_markers"]
    assert result["max_schedule_late_s"] > 0
