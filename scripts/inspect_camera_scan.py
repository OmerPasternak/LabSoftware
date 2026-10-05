"""Inspect completed camera scan HDF5 files without loading full stacks into RAM.

Run ``python -m scripts.inspect_camera_scan PATH`` for one .h5 file or a
directory of completed scan steps. Pixel values and mean frames are raw ADU.
"""

import argparse
import csv
import json
from pathlib import Path

import h5py
import numpy as np


CSV_FIELDS = (
    "file", "experiment_name", "run_id", "step_index", "scan_parameter_name",
    "scan_parameter_value", "exposure_time_s", "frames", "height", "width",
    "mean_adu", "timestamp_utc",
)


def scan_files(path: Path) -> list[Path]:
    """Return completed .h5 step files from a file or directory, sorted by name."""
    if path.is_file() and path.suffix.lower() == ".h5":
        return [path]
    if path.is_dir():
        files = sorted(path.glob("*.h5"))
        if files:
            return files
        raise FileNotFoundError(f"No completed .h5 files in {path}")
    raise ValueError(f"Expected a completed .h5 file or directory: {path}")


def read_frame_metadata(path: Path, frame_index: int) -> dict:
    """Read one frame's JSON metadata; indices are zero based, timestamps retain their source."""
    with h5py.File(path, "r") as h5f:
        _validate(h5f, path)
        records = h5f["frame_metadata/json"]
        if not 0 <= frame_index < len(records):
            raise IndexError(f"Frame index {frame_index} outside 0..{len(records) - 1}")
        return json.loads(records[frame_index])


def _validate(h5f: h5py.File, path: Path) -> h5py.Dataset:
    """Check the current camera schema and reject incomplete or inconsistent data."""
    if str(h5f.attrs.get("schema_version", "")) != "2.0":
        raise ValueError(f"Unsupported camera schema in {path}; expected 2.0")
    if not bool(h5f.attrs.get("complete", False)):
        raise ValueError(f"Incomplete scan step: {path}")
    if "images" not in h5f or "frame_metadata/json" not in h5f:
        raise ValueError(f"Missing camera data or frame metadata in {path}")
    images = h5f["images"]
    if not isinstance(images, h5py.Dataset) or images.ndim != 3 or images.dtype != np.dtype("uint16"):
        raise ValueError(f"Expected uint16 images [frame, y, x] in {path}")
    if images.attrs.get("dimension_order") != "[frame, y, x]":
        raise ValueError(f"Unexpected image axis order in {path}")
    if images.shape[0] == 0 or images.shape[1] == 0 or images.shape[2] == 0:
        raise ValueError(f"Empty image stack in {path}")
    if int(h5f.attrs.get("frames_written", -1)) != images.shape[0]:
        raise ValueError(f"Frame count mismatch in {path}")
    if len(h5f["frame_metadata/json"]) != images.shape[0]:
        raise ValueError(f"Frame metadata count mismatch in {path}")
    return images


def summarize_step(path: Path, batch_size: int = 4) -> tuple[dict, np.ndarray]:
    """Return step metadata and a float64 mean frame (ADU) using bounded batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if path.suffix.lower() != ".h5":
        raise ValueError(f"Expected a completed .h5 file: {path}")
    with h5py.File(path, "r") as h5f:
        images = _validate(h5f, path)
        count, height, width = images.shape
        total = np.zeros((height, width), dtype=np.float64)
        for start in range(0, count, batch_size):
            total += images[start:start + batch_size].sum(axis=0, dtype=np.float64)
        attrs = h5f.attrs
        summary = {
            "file": str(path),
            "experiment_name": str(attrs["experiment_name"]),
            "run_id": str(attrs["run_id"]),
            "step_index": int(attrs["scan_step_index"]),
            "scan_parameter_name": str(attrs["scan_parameter_name"]),
            "scan_parameter_value": float(attrs["scan_parameter_value"]),
            "exposure_time_s": float(attrs["exposure_time_s"]),
            "frames": count,
            "height": height,
            "width": width,
            "mean_adu": float(total.sum() / (count * height * width)),
            "timestamp_utc": str(attrs["timestamp_utc"]),
        }
        return summary, total / count


def main(argv: list[str] | None = None) -> int:
    """Print per-step summaries and optionally write CSV and mean-frame NPY files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Completed .h5 file or directory of step files")
    parser.add_argument("--batch-size", type=int, default=4, help="Frames read per batch (default: 4)")
    parser.add_argument("--csv", type=Path, help="Write one summary row per step")
    parser.add_argument("--mean-dir", type=Path, help="Save each mean frame as float64 .npy (ADU)")
    args = parser.parse_args(argv)
    try:
        files = scan_files(args.path)
        rows = []
        if args.mean_dir:
            args.mean_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            row, mean_frame = summarize_step(path, args.batch_size)
            rows.append(row)
            print(f"{path.name}: step {row['step_index']}, {row['frames']} frames, "
                  f"{row['height']}x{row['width']}, mean {row['mean_adu']:.3f} ADU")
            if args.mean_dir:
                np.save(args.mean_dir / f"{path.stem}_mean.npy", mean_frame)
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            with args.csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
