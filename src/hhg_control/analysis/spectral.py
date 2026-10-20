"""Spectral-axis covariance and reusable harmonic x-range helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import h5py
import numpy as np

from .hdf5_io import iter_scan_step_frames


class StreamingSpectralCovariance:
    """Accumulate sample covariance between x pixels without retaining frames."""

    def __init__(self, width: int) -> None:
        if width < 1:
            raise ValueError("width must be >= 1.")
        self.width = int(width)
        self.count = 0
        self.mean = np.zeros(self.width, dtype=np.float64)
        self._m2 = np.zeros((self.width, self.width), dtype=np.float64)

    def update(self, spectra: np.ndarray) -> None:
        """Add `[frame, x]` spectra, where each entry is an ADU sum over y."""
        values = np.asarray(spectra, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != self.width:
            raise ValueError(
                f"Expected [frame, {self.width}] spectra, got shape {values.shape}."
            )
        if values.shape[0] == 0:
            return
        if not np.all(np.isfinite(values)):
            raise ValueError("Spectra must contain only finite values.")

        batch_count = values.shape[0]
        batch_mean = values.mean(axis=0)
        centered = values - batch_mean
        batch_m2 = centered.T @ centered
        if self.count == 0:
            self.mean = batch_mean
            self._m2 = batch_m2
            self.count = batch_count
            return

        combined_count = self.count + batch_count
        delta = batch_mean - self.mean
        self._m2 += batch_m2 + np.outer(delta, delta) * (
            self.count * batch_count / combined_count
        )
        self.mean += delta * (batch_count / combined_count)
        self.count = combined_count

    def finalize(self) -> np.ndarray:
        """Return the unbiased sample covariance matrix in ADU²."""
        if self.count < 2:
            raise ValueError(
                f"At least 2 frames are required for covariance, found {self.count}."
            )
        return self._m2 / (self.count - 1)


def _validate_y_range(y_range: Optional[tuple[int, int]], height: int) -> tuple[int, int]:
    if y_range is None:
        return 0, height
    if len(y_range) != 2:
        raise ValueError("y_range must be a (start, stop) pair.")
    start, stop = map(int, y_range)
    if start < 0 or stop > height or start >= stop:
        raise ValueError(
            f"y_range must satisfy 0 <= start < stop <= {height}; got {(start, stop)}."
        )
    return start, stop


def _spectra_from_frames(
    frames: np.ndarray, y_range: Optional[tuple[int, int]] = None
) -> np.ndarray:
    values = np.asarray(frames)
    if values.ndim != 3:
        raise ValueError(f"Expected [frame, y, x] frames, got shape {values.shape}.")
    start, stop = _validate_y_range(y_range, values.shape[1])
    return values[:, start:stop, :].astype(np.float64).sum(axis=1)


def compute_spectral_covariance(
    frames: np.ndarray, y_range: Optional[tuple[int, int]] = None
) -> tuple[np.ndarray, np.ndarray]:
    """Compute x-pixel sample covariance across frames after summing selected y rows.

    Args:
        frames: Raw camera counts with shape `[frame, y, x]`.
        y_range: Optional zero-based, half-open row interval `(start, stop)`.
            `None` sums every y row. The x axis is the dispersed frequency /
            wavelength axis; no wavelength calibration is applied.

    Returns:
        `(covariance_adu2, mean_spectrum_adu)`. The covariance is `[x, x]`
        with the unbiased `N - 1` sample estimator.
    """
    spectra = _spectra_from_frames(frames, y_range)
    accumulator = StreamingSpectralCovariance(spectra.shape[1])
    accumulator.update(spectra)
    return accumulator.finalize(), accumulator.mean.copy()


def _decode_h5_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        return tuple(_decode_h5_value(item) for item in value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def _read_source_metadata(path: Path) -> tuple[dict[str, Any], tuple[int, int, int]]:
    with h5py.File(path, "r") as h5f:
        if "images" not in h5f:
            raise KeyError(f"Missing '/images' dataset in {path}.")
        images = h5f["images"]
        if images.ndim != 3:
            raise ValueError(f"Expected [frame, y, x] images in {path}, got {images.shape}.")
        if "complete" in h5f.attrs:
            completion = _decode_h5_value(h5f.attrs["complete"])
            if isinstance(completion, str):
                is_complete = completion.strip().lower() in {"true", "1", "yes"}
            else:
                is_complete = bool(completion)
            if not is_complete:
                raise ValueError(f"Refusing incomplete HDF5 scan file: {path}")
        if "frames_written" in h5f.attrs and int(h5f.attrs["frames_written"]) != images.shape[0]:
            raise ValueError(f"frames_written does not match '/images' in {path}.")
        metadata = {key: _decode_h5_value(value) for key, value in h5f.attrs.items()}
        return metadata, tuple(int(value) for value in images.shape)


_COMPATIBILITY_KEYS = (
    "scan_step_index",
    "scan_parameter_name",
    "scan_parameter_value",
    "exposure_time_s",
    "roi_bounds",
    "readout_mode",
    "camera_serial",
)


def _assert_compatible_sources(
    first_path: Path,
    first_meta: dict[str, Any],
    first_shape: tuple[int, int, int],
    path: Path,
    metadata: dict[str, Any],
    shape: tuple[int, int, int],
) -> None:
    if shape[1:] != first_shape[1:]:
        raise ValueError(f"Image dimensions differ between {first_path.name} and {path.name}.")
    for key in _COMPATIBILITY_KEYS:
        left, right = first_meta.get(key), metadata.get(key)
        if left is None or right is None:
            continue
        if key == "scan_parameter_value" and np.isscalar(left) and np.isscalar(right):
            matches = bool(np.isclose(float(left), float(right), rtol=1e-9, atol=1e-12))
        else:
            matches = left == right
        if not matches:
            raise ValueError(
                f"Input files differ in {key}: {first_path.name} has {left!r}, "
                f"but {path.name} has {right!r}. Select one scan step with matching settings."
            )


def _source_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in {".h5", ".hdf5"}:
            raise ValueError(f"Expected an HDF5 file, got {input_path}.")
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    candidates = sorted((*input_path.glob("*.h5"), *input_path.glob("*.hdf5")))
    paths: list[Path] = []
    for path in candidates:
        try:
            with h5py.File(path, "r") as h5f:
                if "images" in h5f:
                    paths.append(path)
        except OSError as exc:
            raise ValueError(f"Could not inspect HDF5 input file {path}: {exc}") from exc
    if not paths:
        raise FileNotFoundError(f"No completed camera HDF5 files containing '/images' in {input_path}.")
    return paths


def compute_spectral_covariance_from_scan(
    input_path: str | Path,
    y_range: Optional[tuple[int, int]] = None,
    batch_size: int = 4,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Stream one completed scan step or matching step files into x-x covariance.

    A directory input may contain repeat files for one scan step. Files must
    agree on image dimensions, scan step/setpoint, exposure, ROI, camera serial,
    and readout mode so coordinate mismatches are rejected before aggregation.
    """
    path = Path(input_path)
    files = _source_files(path)
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1.")

    first_metadata: Optional[dict[str, Any]] = None
    first_shape: Optional[tuple[int, int, int]] = None
    accumulator: Optional[StreamingSpectralCovariance] = None
    source_metadata: list[dict[str, Any]] = []
    selected_y_range: Optional[tuple[int, int]] = None
    for source in files:
        metadata, shape = _read_source_metadata(source)
        if first_metadata is None:
            first_metadata, first_shape = metadata, shape
            selected_y_range = _validate_y_range(y_range, shape[1])
            accumulator = StreamingSpectralCovariance(shape[2])
        else:
            assert first_shape is not None
            _assert_compatible_sources(
                files[0], first_metadata, first_shape, source, metadata, shape
            )
        assert accumulator is not None and selected_y_range is not None
        y_start, y_stop = selected_y_range
        for batch in iter_scan_step_frames(source, batch_size=batch_size):
            accumulator.update(batch[:, y_start:y_stop, :].astype(np.float64).sum(axis=1))
        source_metadata.append(metadata)

    assert accumulator is not None and first_metadata is not None and first_shape is not None
    covariance = accumulator.finalize()
    roi = first_metadata.get("roi_bounds")
    if roi is not None and len(roi) == 4:
        x_start = int(roi[0])
    else:
        x_start = 0
    summary = {
        "source_files": [str(source.resolve()) for source in files],
        "source_metadata": first_metadata,
        "total_frames": accumulator.count,
        "image_shape_yx": [first_shape[1], first_shape[2]],
        "y_range_local": list(selected_y_range or (0, first_shape[1])),
        "x_pixel_indices": np.arange(x_start, x_start + first_shape[2], dtype=np.int64),
    }
    return covariance, accumulator.mean.copy(), summary


def load_mean_snapshot(filepath: str | Path, batch_size: int = 16) -> tuple[np.ndarray, dict[str, Any]]:
    """Return the float64 mean image of a completed HDF5 step and its metadata."""
    path = Path(filepath)
    metadata, shape = _read_source_metadata(path)
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1.")
    total = np.zeros(shape[1:], dtype=np.float64)
    count = 0
    for frames in iter_scan_step_frames(path, batch_size=batch_size):
        total += frames.astype(np.float64).sum(axis=0)
        count += frames.shape[0]
    if count < 1:
        raise ValueError(f"No frames found in {path}.")
    metadata = dict(metadata)
    metadata["snapshot_frame_count"] = count
    metadata["source_file"] = str(path.resolve())
    return total / count, metadata


def validate_harmonic_regions(
    regions: Iterable[dict[str, Any]], width: int
) -> list[dict[str, Any]]:
    """Validate and sort named `[x_start, x_stop)` ranges against image width."""
    validated: list[dict[str, Any]] = []
    labels: set[str] = set()
    for item in regions:
        label = str(item.get("label", "")).strip()
        start, stop = int(item["x_start"]), int(item["x_stop"])
        if not label:
            raise ValueError("Every harmonic region needs a non-empty label.")
        if label in labels:
            raise ValueError(f"Harmonic label {label!r} is duplicated.")
        if start < 0 or stop > width or start >= stop:
            raise ValueError(
                f"Harmonic {label!r} must satisfy 0 <= x_start < x_stop <= {width}."
            )
        labels.add(label)
        validated.append({"label": label, "x_start": start, "x_stop": stop})
    if not validated:
        raise ValueError("Mark at least one harmonic range before saving.")
    validated.sort(key=lambda item: item["x_start"])
    for previous, current in zip(validated, validated[1:]):
        if current["x_start"] < previous["x_stop"]:
            raise ValueError(
                f"Harmonic ranges {previous['label']!r} and {current['label']!r} overlap."
            )
    return validated


def save_harmonic_regions(
    output_path: str | Path,
    regions: Sequence[dict[str, Any]],
    source_path: str | Path,
    image_shape_yx: tuple[int, int],
    roi_bounds: Optional[Sequence[int]] = None,
    annotated_snapshot_path: Optional[str | Path] = None,
) -> Path:
    """Save harmonic boundaries as a JSON sidecar for later per-harmonic analysis.

    Pixel ranges are zero-based and half-open, relative to the `/images` x axis.
    The stored dimensions and ROI allow downstream tools to detect mismatches.
    Existing sidecars are never overwritten.
    """
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing harmonic map: {output}")
    height, width = map(int, image_shape_yx)
    selected = validate_harmonic_regions(regions, width)
    payload = {
        "schema_version": "1.0",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": str(Path(source_path).resolve()),
        "annotated_snapshot_png": (
            str(Path(annotated_snapshot_path).resolve())
            if annotated_snapshot_path is not None else None
        ),
        "image_shape_yx": [height, width],
        "roi_bounds_xyxy": list(map(int, roi_bounds)) if roi_bounds is not None else None,
        "x_axis": "detector x pixel (frequency/wavelength dispersion; no calibration applied)",
        "range_convention": "zero-based, half-open [x_start, x_stop), relative to /images",
        "harmonics": selected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except Exception:
        # Do not leave a truncated file that looks like a complete reference map.
        if created:
            output.unlink()
        raise
    return output


def load_harmonic_regions(
    filepath: str | Path,
    image_shape_yx: Optional[tuple[int, int]] = None,
    roi_bounds: Optional[Sequence[int]] = None,
) -> dict[str, Any]:
    """Load a harmonic sidecar and optionally reject image-shape or ROI mismatches."""
    path = Path(filepath)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != "1.0":
        raise ValueError(f"Unsupported harmonic map schema in {path}.")
    saved_shape = tuple(map(int, payload.get("image_shape_yx", ())))
    if len(saved_shape) != 2:
        raise ValueError(f"Invalid image_shape_yx in harmonic map {path}.")
    if image_shape_yx is not None and tuple(map(int, image_shape_yx)) != saved_shape:
        raise ValueError(
            f"Harmonic map image shape {saved_shape} does not match requested shape {image_shape_yx}."
        )
    saved_roi = payload.get("roi_bounds_xyxy")
    normalized_roi = list(map(int, roi_bounds)) if roi_bounds is not None else None
    if roi_bounds is not None and saved_roi != normalized_roi:
        raise ValueError(
            f"Harmonic map ROI {saved_roi!r} does not match requested ROI {normalized_roi!r}."
        )
    payload["harmonics"] = validate_harmonic_regions(payload.get("harmonics", []), saved_shape[1])
    return payload


def compute_harmonic_g2_from_scan(
    filepath: str | Path,
    harmonic_map_path: str | Path,
    batch_size: int = 16,
) -> dict[str, Any]:
    """Compute per-harmonic temporal ``g2(0)`` from one completed HDF5 step.

    For each frame, signal is the sum of raw ADU values over all detector rows
    and the harmonic's half-open x range. The reported estimator is
    ``mean(signal**2) / mean(signal)**2``. Frames are streamed in batches; a
    second pass estimates a normal-approximation 95% confidence interval using
    the delta method. The interval assumes independent frames and does not
    account for camera background, photon conversion, or trigger accounting.
    The source HDF5 file is never modified.
    """
    path = Path(filepath)
    metadata, shape = _read_source_metadata(path)
    if shape[0] < 2:
        raise ValueError(f"At least 2 frames are required for g2 analysis; found {shape[0]}.")
    roi = metadata.get("roi_bounds")
    harmonic_map = load_harmonic_regions(
        harmonic_map_path,
        image_shape_yx=(shape[1], shape[2]),
        roi_bounds=roi,
    )
    regions = harmonic_map["harmonics"]
    totals = {region["label"]: 0.0 for region in regions}
    squares = {region["label"]: 0.0 for region in regions}
    count = 0
    for batch in iter_scan_step_frames(path, batch_size=batch_size):
        values = batch.astype(np.float64, copy=False)
        for region in regions:
            signal = values[:, :, region["x_start"]:region["x_stop"]].sum(axis=(1, 2))
            totals[region["label"]] += float(np.sum(signal, dtype=np.float64))
            squares[region["label"]] += float(np.sum(signal * signal, dtype=np.float64))
        count += batch.shape[0]

    results = []
    for region in regions:
        label = region["label"]
        mean_signal = totals[label] / count
        mean_square = squares[label] / count
        if mean_signal <= 0:
            results.append({
                **region, "mean_signal_adu": mean_signal, "g2_zero_delay": None,
                "standard_error": None, "ci95": None,
            })
            continue
        g2 = mean_square / (mean_signal * mean_signal)
        influence_sum = 0.0
        influence_square_sum = 0.0
        for batch in iter_scan_step_frames(path, batch_size=batch_size):
            values = batch.astype(np.float64, copy=False)
            signal = values[:, :, region["x_start"]:region["x_stop"]].sum(axis=(1, 2))
            influence = (signal * signal - mean_square) / (mean_signal * mean_signal)
            influence -= 2.0 * mean_square * (signal - mean_signal) / (mean_signal ** 3)
            influence_sum += float(np.sum(influence, dtype=np.float64))
            influence_square_sum += float(np.sum(influence * influence, dtype=np.float64))
        influence_mean = influence_sum / count
        influence_variance = max(
            0.0, (influence_square_sum - count * influence_mean * influence_mean) / (count - 1)
        )
        standard_error = float(np.sqrt(influence_variance / count))
        results.append({
            **region,
            "mean_signal_adu": mean_signal,
            "g2_zero_delay": g2,
            "standard_error": standard_error,
            "ci95": [g2 - 1.96 * standard_error, g2 + 1.96 * standard_error],
        })
    return {
        "source_file": str(path.resolve()),
        "harmonic_map_file": str(Path(harmonic_map_path).resolve()),
        "total_frames": count,
        "image_shape_yx": [shape[1], shape[2]],
        "roi_bounds_xyxy": roi,
        "y_range_local_half_open": [0, shape[1]],
        "estimator": "mean(S^2) / mean(S)^2; S is summed raw ADU per frame in the harmonic x range and all y rows",
        "uncertainty": "delta-method standard error and normal-approximation 95% CI; assumes independent frames",
        "harmonics": results,
    }
