"""Check frame-number and host-timing analysis without a physical camera."""

import json

import h5py
import numpy as np

from scripts.inspect_scan_timing import inspect_scan


def test_inspect_scan_reports_frame_gaps_and_host_rate(tmp_path):
    path = tmp_path / "scan.h5"
    with h5py.File(path, "w") as h5f:
        h5f.attrs["num_frames"] = 3
        h5f.attrs["frames_written"] = 3
        h5f.attrs["complete"] = True
        h5f.attrs["roi_bounds"] = (0, 0, 8, 6)
        h5f.attrs["camera_model"] = "fake pco"
        h5f.attrs["readout_mode"] = "GLOBAL_SHUTTER"
        h5f.create_dataset("images", data=np.zeros((3, 6, 8), dtype=np.uint16))
        group = h5f.create_group("frame_metadata")
        items = [
            {"frame_id": frame_id, "host_frame_read_monotonic_ns": timestamp,
             "timestamp_source": "host_fallback"}
            for frame_id, timestamp in ((10, 0), (11, 1_000_000), (13, 2_000_000))
        ]
        group.create_dataset(
            "json", data=[json.dumps(item) for item in items],
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
    result = inspect_scan(path)
    assert result["host_read_fps"] == 1000.0
    assert result["missing_frame_ids"] == 1
    assert not result["consecutive_frame_ids"]
    assert result["host_interval_median_ms"] == 1.0
