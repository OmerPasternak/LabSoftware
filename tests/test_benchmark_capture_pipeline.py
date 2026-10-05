from pathlib import Path
"""Verify that the paced offline benchmark uses and checks the scan writer."""

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
