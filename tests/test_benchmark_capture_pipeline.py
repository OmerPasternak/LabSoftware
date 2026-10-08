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
    timing = result["stage_timing_s"]
    assert timing["pipeline_total"] >= timing["ideal_paced_acquisition"]
    assert timing["final_os_fsync"] >= 0
    assert timing["hdf5_batch_writes"] > 0
    assert timing["hdf5_batch_write_max"] >= timing["hdf5_batch_write_p50"]
    assert result["writer_queue_max_batches_used"] <= result["writer_queue_capacity_batches"]
    assert result["bottleneck_diagnosis"]["dominant_stage"]
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
    assert result["bottleneck_diagnosis"]["dominant_stage"] == "hdf5_batch_writes"


def test_paced_pipeline_identifies_slow_final_fsync(monkeypatch, tmp_path):
    """A delayed OS durability call is reported separately from HDF5 writing."""
    from scripts import benchmark_capture_pipeline as benchmark

    original_fsync = benchmark.os.fsync

    def slow_fsync(file_descriptor):
        time.sleep(0.15)
        return original_fsync(file_descriptor)

    monkeypatch.setattr(benchmark.os, "fsync", slow_fsync)
    result = run_benchmark(
        tmp_path, frames=8, width=16, height=8,
        fps=100.0, batch_size=2,
    )
    assert result["stage_timing_s"]["final_os_fsync"] >= 0.15
    assert result["bottleneck_diagnosis"]["dominant_stage"] == "final_os_fsync"
