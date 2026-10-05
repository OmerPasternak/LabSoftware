"""Inspect saved camera-frame integrity and host read timing without hardware."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import h5py
import numpy as np


def inspect_scan(path: Path) -> dict:
    """Summarize a scan file; timing uses host monotonic nanoseconds when present."""
    with h5py.File(path, "r") as h5f:
        expected = int(h5f.attrs["num_frames"])
        written = int(h5f.attrs["frames_written"])
        images = h5f["images"]
        metadata = [json.loads(item) for item in h5f["frame_metadata/json"][:written]]
        if len(metadata) != written or images.shape[0] != expected:
            raise ValueError("HDF5 image or metadata count disagrees with scan attributes.")
        frame_ids = [int(item["frame_id"]) for item in metadata]
        gaps = [
            (current - previous) % (2**32) - 1
            for previous, current in zip(frame_ids, frame_ids[1:])
        ]
        times = [item.get("host_frame_read_monotonic_ns") for item in metadata]
        host_rate = None
        median_interval_ms = None
        p99_interval_ms = None
        if written > 1 and all(value is not None for value in times):
            intervals_ns = np.diff(np.asarray(times, dtype=np.int64))
            if np.all(intervals_ns > 0):
                host_rate = (written - 1) * 1e9 / int(times[-1] - times[0])
                median_interval_ms = float(np.median(intervals_ns) / 1e6)
                p99_interval_ms = float(np.percentile(intervals_ns, 99) / 1e6)
        return {
            "file": str(path.resolve()),
            "complete": bool(h5f.attrs["complete"]),
            "frames_requested": expected,
            "frames_written": written,
            "shape_frame_yx": list(images.shape),
            "roi_bounds": [int(value) for value in h5f.attrs["roi_bounds"]],
            "camera_model": str(h5f.attrs["camera_model"]),
            "readout_mode": str(h5f.attrs["readout_mode"]),
            "compression": images.compression or "none",
            "consecutive_frame_ids": all(gap == 0 for gap in gaps),
            "missing_frame_ids": sum(gap for gap in gaps if gap > 0),
            "timestamp_sources": dict(Counter(item.get("timestamp_source", "unknown") for item in metadata)),
            "host_read_fps": host_rate,
            "host_interval_median_ms": median_interval_ms,
            "host_interval_p99_ms": p99_interval_ms,
            "timing_note": "Host read times are software observations, not sensor exposure or laser timestamps.",
        }


def main() -> None:
    """Print a JSON timing and integrity report for one completed scan step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_scan(args.file), indent=2))


if __name__ == "__main__":
    main()
