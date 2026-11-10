"""
HDF5 Dataset Loaders & Utilities.
Parses HHG experiment scan files into NumPy arrays compatible with scientific analysis and MATLAB pipelines.
"""

from pathlib import Path
from collections.abc import Iterator
from typing import Any, Dict, List, Optional, Tuple, Union
import h5py
import numpy as np
from ...safe_io import filesystem_path


def normalize_roi_bounds(value):
    """Normalize validated zero-based half-open xyxy bounds without truncation."""
    if value is None:
        return None
    values = np.asarray(value)
    if values.shape != (4,) or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("ROI bounds must be four integer xyxy coordinates.")
    x0, y0, x1, y1 = map(int, values)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError("ROI bounds must be nonnegative ordered half-open coordinates.")
    return (x0, y0, x1, y1)


def validate_scan_inputs(paths, *, run_id=None, repeat_policy="reject", pool_steps=False, require_full_grid=False):
    """Validate complete compatible inputs before aggregation; return headers/warnings.

    Distinct runs require selection by run_id. Repeated step indices are rejected
    unless explicitly allowed. Pooling different coordinates is a separate opt-in.
    Legacy missing metadata is reported as unverified, never inferred.
    Low-level readers remain available for explicit partial-file diagnostics.
    """
    if repeat_policy not in ("reject", "allow"):
        raise ValueError("repeat_policy must be 'reject' or 'allow'.")
    headers, warnings = [], []
    for path in paths:
        meta = get_scan_metadata(path)
        if run_id is not None and str(meta.get("run_id")) != str(run_id):
            continue
        shape = meta.get("dataset_shape", ())
        if len(shape) != 3 or shape[0] < 1:
            raise ValueError(f"Invalid image dimensions in {path}.")
        if meta.get("schema_version") == "2.0" and any(key not in meta for key in ("complete", "frames_written")):
            raise ValueError(f"Schema-2 completion metadata is missing in {path}.")
        if "complete" in meta and not bool(meta["complete"]):
            raise ValueError(f"Incomplete measurement cannot be aggregated: {path}.")
        for key in ("num_frames", "frames_written", "frame_accumulation_count"):
            if key in meta and meta[key] != shape[0]:
                raise ValueError(f"Frame-count mismatch in {path}: {key}.")
        if meta.get("schema_version") not in (None, "2.0"):
            raise ValueError(f"Unsupported camera schema in {path}.")
        with h5py.File(filesystem_path(path), "r") as h5:
            if "frame_metadata/json" in h5 and len(h5["frame_metadata/json"]) != shape[0]:
                raise ValueError(f"Per-frame metadata-count mismatch in {path}.")
        if "complete" not in meta:
            warnings.append(f"{path}: legacy completion is unverified.")
        headers.append((Path(path), meta))
    if not headers:
        raise ValueError("No files match the selected run.")
    known_runs = {str(meta["run_id"]) for _, meta in headers if meta.get("run_id")}
    if any(not meta.get("run_id") for _, meta in headers):
        warnings.append("Run identity is unverified for legacy inputs without run_id.")
    if len(known_runs) > 1:
        raise ValueError("Multiple run IDs found; select one run explicitly.")
    settings = ("roi_bounds", "camera_serial", "camera_model", "exposure_time_s", "readout_mode", "trigger_mode", "scan_parameter_name", "scan_parameter_units", "dataset_dtype", "scan_num_steps", "scan_start_value", "scan_step_size")
    for key in settings:
        values = [meta.get(key) for _, meta in headers]
        if key == "roi_bounds": values = [normalize_roi_bounds(v) for v in values]
        known = [v for v in values if v is not None]
        if known and any(not np.array_equal(v, known[0]) for v in known[1:]):
            raise ValueError(f"Acquisition settings mismatch: {key}.")
        if len(known) != len(headers): warnings.append(f"{key}: matching is unverified (legacy metadata missing).")
    if any(meta["dataset_shape"][1:] != headers[0][1]["dataset_shape"][1:] for _, meta in headers):
        raise ValueError("Image geometry differs between scan files.")
    indices = [meta.get("scan_step_index", meta.get("step_index")) for _, meta in headers]
    known_indices = [index for index in indices if index is not None]
    if len(set(known_indices)) != len(known_indices) and repeat_policy != "allow":
        raise ValueError("Duplicate scan step indices; select legitimate repeats explicitly.")
    if len(known_indices) != len(indices): warnings.append("Step identity is unverified for legacy inputs.")
    coordinates = [meta.get("scan_parameter_value", meta.get("scan_parameter_setpoint_value")) for _, meta in headers]
    known_coordinates = [v for v in coordinates if v is not None]
    if any(not np.isfinite(v) for v in known_coordinates):
        raise ValueError("Nonfinite scan coordinates cannot be aggregated.")
    if not pool_steps and known_coordinates and any(v != known_coordinates[0] for v in known_coordinates[1:]):
        raise ValueError("Pooling different scan coordinates requires pool_steps=True.")
    declared = [meta.get("scan_num_steps") for _, meta in headers]
    if any(count is not None for count in declared):
        if len(set(declared)) != 1 or declared[0] is None:
            raise ValueError("Declared scan grids differ between inputs.")
        count = int(declared[0])
        for _, meta in headers:
            index = meta.get("scan_step_index", meta.get("step_index"))
            if index is None or not 0 <= index < count:
                raise ValueError("Step index lies outside the declared scan grid.")
            if "scan_start_value" in meta and "scan_step_size" in meta:
                expected = meta["scan_start_value"] + index * meta["scan_step_size"]
                if not np.isclose(meta.get("scan_parameter_value", float("nan")), expected, rtol=1e-12, atol=1e-12):
                    raise ValueError("Setpoint differs from the declared scan grid.")
        if require_full_grid and len(set(known_indices)) != count:
            raise ValueError("Scan grid is incomplete; use explicit partial-file diagnostic readers.")
    else:
        warnings.append("Scan grid was not declared; completeness across steps is unverified.")
    return headers, warnings


def iter_scan_step_frames(
    filepath: Union[str, Path], batch_size: int = 4
) -> Iterator[np.ndarray]:
    """Yield HDF5 image batches without loading a complete scan step into RAM."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1.")
    path = Path(filepath)
    with h5py.File(filesystem_path(path), "r") as h5f:
        if "images" not in h5f:
            raise KeyError(f"Missing 'images' dataset in {path}")
        images = h5f["images"]
        if images.ndim != 3:
            raise ValueError(f"Expected [frame, y, x] data in {path}, got shape {images.shape}.")
        for start in range(0, images.shape[0], batch_size):
            yield images[start:min(start + batch_size, images.shape[0])]


def get_scan_metadata(filepath: Union[str, Path]) -> Dict[str, Any]:
    """
    Read metadata attributes from an HDF5 scan file without loading image data into memory.

    Args:
        filepath: Path to the .h5 scan file.

    Returns:
        Dictionary of all file-level and dataset attributes.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Scan file not found: {filepath}")

    metadata: Dict[str, Any] = {}
    with h5py.File(filesystem_path(filepath), "r") as h5f:
        for key, val in h5f.attrs.items():
            metadata[key] = val
        if "images" in h5f:
            dset = h5f["images"]
            metadata["dataset_shape"] = dset.shape
            metadata["dataset_dtype"] = str(dset.dtype)
            for key, val in dset.attrs.items():
                metadata[f"images_{key}"] = val

    return metadata


def load_scan_step(filepath: Union[str, Path]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Load an individual scan step HDF5 file.

    Args:
        filepath: Path to the .h5 scan file.

    Returns:
        images: 3D numpy array of shape (num_frames, height, width), dtype uint16.
        metadata: Dictionary containing scan setpoint, exposure, timestamp, and instrument info.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Scan file not found: {filepath}")

    with h5py.File(filesystem_path(filepath), "r") as h5f:
        if "images" not in h5f:
            raise KeyError(f"Corrupted scan file: missing '/images' dataset in {filepath}")
        images = h5f["images"][:]
        metadata = dict(h5f.attrs)

    return images, metadata


def load_full_scan(
    storage_dir: Union[str, Path],
    experiment_prefix: Optional[str] = None,
    *, run_id: str | None = None, repeat_policy: str = "reject",
) -> Dict[str, Any]:
    """
    Load and aggregate all step files belonging to a completed scan sequence.

    Args:
        storage_dir: Directory containing the .h5 step files.
        experiment_prefix: Optional file prefix filter (e.g. 'HHG_Scan'). If None, loads all steps found.

    Returns:
        Dictionary containing:
            - 'step_indices': list of integer step indices
            - 'setpoints': numpy 1D array of physical scan values
            - 'param_name': display name of the scanned physical parameter
            - 'exposure_s': sensor exposure time in seconds
            - 'image_stacks': list of 3D numpy arrays (one per step)
            - 'mean_frames': 3D numpy array of shape (num_steps, height, width) of averaged frames per step
            - 'filepaths': list of Path objects
    """
    dir_path = Path(storage_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Directory not found: {dir_path}")

    pattern = f"{experiment_prefix}*.h5" if experiment_prefix else "*.h5"
    matched_files = sorted(dir_path.glob(pattern))

    if not matched_files:
        raise FileNotFoundError(f"No scan files matching '{pattern}' in {dir_path}")

    headers, validation_warnings = validate_scan_inputs(matched_files, run_id=run_id, repeat_policy=repeat_policy, pool_steps=True, require_full_grid=True)
    matched_files = [path for path, _ in headers]

    # Read steps and sort by scan_step_index
    steps_data = []
    read_errors = []
    for f in matched_files:
        try:
            imgs, meta = load_scan_step(f)
            idx = int(meta.get("scan_step_index", meta.get("step_index", 0)))
            setpoint = float(meta.get("scan_parameter_setpoint_value", meta.get("scan_parameter_value", 0.0)))
            steps_data.append({
                "index": idx,
                "setpoint": setpoint,
                "filepath": f,
                "images": imgs,
                "metadata": meta,
            })
        except Exception as exc:
            read_errors.append(f"{f.name}: {exc}")

    if read_errors:
        raise ValueError(
            "One or more scan files could not be read; refusing a silently incomplete dataset: "
            + "; ".join(read_errors)
        )

    if not steps_data:
        raise ValueError(f"No valid scan datasets could be parsed in {dir_path}")

    # Sort strictly by step index
    steps_data.sort(key=lambda item: item["index"])

    step_indices = [item["index"] for item in steps_data]
    setpoints = np.array([item["setpoint"] for item in steps_data], dtype=np.float64)
    image_stacks = [item["images"] for item in steps_data]
    mean_frames = np.stack([item["images"].mean(axis=0) for item in steps_data], axis=0)

    first_meta = steps_data[0]["metadata"]
    param_name = str(first_meta.get("scan_parameter_display_name", first_meta.get("scan_parameter_name", "Setpoint")))
    exposure_s = float(first_meta.get("exposure_duration_seconds", first_meta.get("exposure_time_s", 0.0)))

    return {
        "validation_warnings": validation_warnings,
        "repeat_policy": repeat_policy,
        "step_indices": step_indices,
        "setpoints": setpoints,
        "param_name": param_name,
        "exposure_s": exposure_s,
        "image_stacks": image_stacks,
        "mean_frames": mean_frames,
        "filepaths": [item["filepath"] for item in steps_data],
    }
