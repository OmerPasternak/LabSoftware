"""Read-only sums of raw camera counts inside one reusable rectangular window."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Sequence

import h5py
import numpy as np

from .hdf5_io import iter_scan_step_frames
from .spectral import _read_source_metadata, _source_files


def default_intensity_window_path() -> Path:
    """Return the per-user window location, independent of measurement folders."""
    override = os.environ.get("HHG_INTENSITY_WINDOW")
    if override:
        return Path(override).expanduser()
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
    return root / "HHGControl" / "harmonic_intensity_window.json"


def validate_intensity_window(window: dict, shape_yx: Sequence[int], roi_bounds) -> tuple[int, int, int, int]:
    """Check saved geometry and return integer, half-open `(x0, y0, x1, y1)` bounds."""
    if window.get("kind") != "harmonic_intensity_window" or window.get("schema_version") != "1.0":
        raise ValueError("Unsupported harmonic intensity window format.")
    if list(shape_yx) != window.get("image_shape_yx"):
        raise ValueError("Window image shape differs from the measurement; select a new window.")
    roi = list(roi_bounds) if roi_bounds is not None else None
    if window.get("roi_bounds_xyxy") != roi:
        raise ValueError("Window camera ROI differs from the measurement; select a new window.")
    bounds = window.get("bounds_xyxy", [])
    if len(bounds) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in bounds):
        raise ValueError("Window bounds must be four integer pixel edges.")
    x0, y0, x1, y1 = bounds
    height, width = shape_yx
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("Window bounds must lie inside the image and have positive width and height.")
    return x0, y0, x1, y1


def reference_frame(filepath: str | Path) -> tuple[np.ndarray, dict]:
    """Read only frame zero of a completed saved file, including a triggered run."""
    path = Path(filepath)
    if path.suffix.lower() not in {".h5", ".hdf5"}:
        raise ValueError("Choose a completed .h5/.hdf5 measurement, rather than a partial file.")
    metadata, shape = _read_source_metadata(path)
    if shape[0] == 0:
        raise ValueError("The reference measurement contains no frames.")
    with h5py.File(path, "r") as source:
        return source["images"][0], metadata


def make_intensity_window(bounds_xyxy: Sequence[int], reference_path: str | Path) -> dict:
    """Build a reusable rectangle with saved-reference geometry and trigger provenance."""
    path = Path(reference_path)
    if path.suffix.lower() not in {".h5", ".hdf5"}:
        raise ValueError("Choose a completed .h5/.hdf5 reference file.")
    metadata, shape = _read_source_metadata(path)
    if shape[0] == 0:
        raise ValueError("The reference measurement contains no frames.")
    window = {
        "kind": "harmonic_intensity_window", "schema_version": "1.0",
        "bounds_xyxy": list(bounds_xyxy), "image_shape_yx": list(shape[1:]),
        "roi_bounds_xyxy": list(metadata["roi_bounds"]) if metadata.get("roi_bounds") is not None else None,
        "reference_file": str(path.resolve()), "reference_frame_index": 0,
        "reference_trigger_mode": metadata.get("trigger_mode"),
        "reference_camera_serial": metadata.get("camera_serial"),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "range_convention": "zero-based half-open /images indices: images[:, y0:y1, x0:x1]",
    }
    validate_intensity_window(window, shape[1:], metadata.get("roi_bounds"))
    return window


def save_intensity_window(filepath: str | Path, window: dict) -> None:
    """Atomically save a user-selected reference rectangle, preserving the old file on failure."""
    validate_intensity_window(window, window["image_shape_yx"], window.get("roi_bounds_xyxy"))
    path = Path(filepath)
    if path.suffix.lower() != ".json":
        raise ValueError("The intensity window must be saved as a .json file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(window, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def measurement_files(inputs: Sequence[str | Path]) -> list[Path]:
    """Resolve saved measurement files in order, excluding directory .partial entries."""
    files = [file for entry in inputs for file in _source_files(Path(entry))]
    if not files:
        raise ValueError("Choose at least one saved measurement file.")
    if len({file.resolve() for file in files}) != len(files):
        raise ValueError("The same measurement file was supplied more than once.")
    return files


def compute_harmonic_intensity(inputs: Sequence[str | Path], window: dict, *, batch_size: int = 16) -> dict:
    """Return mean and sample STDEV of per-frame window sums in raw ADU.

    Pool every frame in the selected measurement files with equal weight. Scan
    labels are irrelevant. Each signal is sum(images[frame, y0:y1, x0:x1]);
    STDEV uses N-1 across those signals, not pixels or the mean's uncertainty.
    Read inputs only; perform no background subtraction, clipping or filtering.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    files = measurement_files(inputs)
    count, mean, m2 = 0, 0.0, 0.0
    for path in files:
        metadata, shape = _read_source_metadata(path)
        x0, y0, x1, y1 = validate_intensity_window(window, shape[1:], metadata.get("roi_bounds"))
        if shape[0] == 0:
            raise ValueError(f"Measurement contains no frames: {path}")
        for frames in iter_scan_step_frames(path, batch_size=batch_size):
            signals = frames[:, y0:y1, x0:x1].sum(axis=(1, 2), dtype=np.float64)
            if not np.all(np.isfinite(signals)):
                raise ValueError(f"Nonfinite window counts in {path}; no frames were discarded.")
            size = len(signals)
            batch_mean = float(signals.mean())
            delta = batch_mean - mean
            total = count + size
            m2 += float(np.sum((signals - batch_mean)**2)) + delta**2 * count * size / total
            mean += delta * size / total
            count = total
    if count < 2:
        raise ValueError("At least two images are required to report sample STDEV.")
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "average_adu": mean, "stdev_adu": float(np.sqrt(m2 / (count - 1))),
        "total_frames": count, "stdev_ddof": 1,
        "method": "Sum raw ADU inside the rectangle per frame; mean and sample STDEV across all frames",
        "source_files": [str(path.resolve()) for path in files], "window": window,
    }
