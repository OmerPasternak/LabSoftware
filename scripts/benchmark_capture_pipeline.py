"""Test paced synthetic capture through the production HDF5 scan writer.

This never opens camera hardware. It replays prepared uint16 frames at a
requested arrival rate, checks every saved frame ID, and times file durability.
The result measures software/storage headroom, not physical camera timing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hhg_control.sequencer.scan_manager import CameraScanManager
from scripts.stress_camera_storage import ReplayCamera


class PacedReplayCamera(ReplayCamera):
    """Deliver prepared frames at a target rate in frames per second."""

    def __init__(self, width: int, height: int, batch_size: int, fps: float) -> None:
        super().__init__(width, height, batch_size, pattern="noise")
        if fps <= 0:
            raise ValueError("fps must be positive.")
        self.target_fps = fps
        self.frames_issued = 0
        self.schedule_start: float | None = None
        self.pace_wait_s = 0.0
        self.max_late_s = 0.0

    def acquire_frames(self, num_frames: int) -> tuple[np.ndarray, list[dict]]:
        """Return uint16 ADU frames after their scheduled arrival time."""
        if self.schedule_start is None:
            self.schedule_start = time.perf_counter()
        due = self.schedule_start + (self.frames_issued + num_frames) / self.target_fps
        wait_s = max(0.0, due - time.perf_counter())
        if wait_s:
            time.sleep(wait_s)
            self.pace_wait_s += wait_s
        self.max_late_s = max(self.max_late_s, time.perf_counter() - due)
        images, metadata = super().acquire_frames(num_frames)
        for index, item in enumerate(metadata):
            frame_id = self.frames_issued + index
            item["frame_id"] = frame_id
            item["timestamp_source"] = "paced_synthetic_replay"
            # The scan manager copies each yielded batch before requesting the
            # next one, so this reusable replay bank can carry a frame marker.
            images[index].flat[0] = frame_id % 65536
        self.frames_issued += num_frames
        return images, metadata


def run_benchmark(
    output_dir: Path, *, frames: int, width: int, height: int,
    fps: float, batch_size: int = 4, keep_data: bool = True,
) -> dict:
    """Save and verify one paced scan; dimensions are pixels and rate is fps."""
    if min(frames, width, height, batch_size) < 1:
        raise ValueError("frames, width, height, and batch_size must be positive.")
    from hhg_control.safe_io import benchmark_directory
    if not keep_data:
        raise ValueError("Benchmark data is retained; delete its BENCHMARK_ONLY run folder manually.")
    output_dir = benchmark_directory(output_dir.resolve(), "paced_pipeline")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = frames * width * height * 2
    free_bytes = shutil.disk_usage(output_dir).free
    if free_bytes < int(raw_bytes * 1.25) + 100_000_000:
        raise OSError("Not enough free space for the paced capture benchmark.")

    camera = PacedReplayCamera(width, height, min(batch_size, frames), fps)
    camera.connect()
    manager = CameraScanManager(camera, output_dir)
    started = time.perf_counter()
    try:
        path, _ = manager.acquire_and_save_step(
            "BENCHMARK_ONLY_paced_pipeline", 0, "synthetic_step", 0.0, frames,
            batch_size=batch_size, run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"),
        )
        pipeline_s = time.perf_counter() - started
        file_bytes = path.stat().st_size
        with path.open("r+b", buffering=0) as handle:
            os.fsync(handle.fileno())
        durable_s = time.perf_counter() - started
        with h5py.File(path, "r") as h5f:
            images = h5f["images"]
            if not bool(h5f.attrs["complete"]) or int(h5f.attrs["frames_written"]) != frames:
                raise AssertionError("HDF5 scan did not complete with the requested frame count.")
            if images.shape != (frames, height, width) or images.dtype != np.dtype("uint16"):
                raise AssertionError("Saved frame shape or dtype differs from the requested source.")
            ids = [json.loads(item)["frame_id"] for item in h5f["frame_metadata/json"]]
            if ids != list(range(frames)):
                raise AssertionError("Saved frame IDs are missing or out of order.")
            for start in range(0, frames, 256):
                end = min(start + 256, frames)
                stamps = images[start:end, 0, 0]
                expected = np.arange(start, end, dtype=np.uint64) % 65536
                if not np.array_equal(stamps, expected):
                    raise AssertionError(f"Saved image frame markers differ at {start}:{end}.")
        result = {
            "source": "paced_synthetic_replay",
            "frames": frames, "width": width, "height": height,
            "target_fps": fps,
            "raw_bytes": raw_bytes, "file_bytes": file_bytes,
            "pipeline_s": pipeline_s, "durable_s": durable_s,
            "pipeline_fps": frames / pipeline_s,
            "durable_fps": frames / durable_s,
            "pace_wait_s": camera.pace_wait_s,
            "max_schedule_late_s": camera.max_late_s,
            "verified_consecutive_frame_ids": True,
            "verified_image_frame_markers": True,
            "kept_data": keep_data,
            "note": "No camera/USB/trigger is exercised; OS and device caching may still affect timing.",
        }
        result["data_path"] = str(path)
        return result
    finally:
        camera.close()


def main() -> None:
    """Run one paced benchmark and retain a compact JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=10000)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=56)
    parser.add_argument("--fps", type=float, default=1000.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    result = run_benchmark(
        args.output_dir, frames=args.frames, width=args.width,
        height=args.height, fps=args.fps, batch_size=args.batch_size,
        keep_data=True,
    )
    report = Path(result["data_path"]).parent / f"BENCHMARK_ONLY_paced_pipeline_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}.json"
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Report: {report.name}")


if __name__ == "__main__":
    main()
