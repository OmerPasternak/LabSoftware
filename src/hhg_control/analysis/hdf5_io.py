"""
HDF5 Dataset Loaders & Utilities.
Parses HHG experiment scan files into NumPy arrays compatible with scientific analysis and MATLAB pipelines.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import h5py
import numpy as np


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
    with h5py.File(filepath, "r") as h5f:
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

    with h5py.File(filepath, "r") as h5f:
        if "images" not in h5f:
            raise KeyError(f"Corrupted scan file: missing '/images' dataset in {filepath}")
        images = h5f["images"][:]
        metadata = dict(h5f.attrs)

    return images, metadata


def load_full_scan(
    storage_dir: Union[str, Path],
    experiment_prefix: Optional[str] = None
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

    # Read steps and sort by scan_step_index
    steps_data = []
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
        except Exception:
            continue

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
        "step_indices": step_indices,
        "setpoints": setpoints,
        "param_name": param_name,
        "exposure_s": exposure_s,
        "image_stacks": image_stacks,
        "mean_frames": mean_frames,
        "filepaths": [item["filepath"] for item in steps_data],
    }
