"""
Quantum Optics Second-Order Correlation Analysis.
Provides routines to calculate normalized intensity autocorrelation maps g^(2)(0)
per pixel across acquired camera frame stacks.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import numpy as np

from ..drivers.base_camera import BaseCamera
from .hdf5_io import get_scan_metadata, iter_scan_step_frames


class StreamingPixelG2:
    """Incrementally accumulate per-pixel g²(0) without retaining frame stacks."""

    def __init__(
        self,
        epsilon: float = 0.0,
        background: Optional[Union[float, np.ndarray]] = None,
    ) -> None:
        self.epsilon = float(epsilon)
        self.background = background
        self.sum_i: np.ndarray | None = None
        self.sum_i2: np.ndarray | None = None
        self.count = 0

    def update(self, frames: np.ndarray) -> None:
        """Add a `[frame, y, x]` batch of sensor counts to the accumulator."""
        if frames.ndim != 3:
            raise ValueError(f"Expected [frame, y, x] data, got shape {frames.shape}.")
        if self.sum_i is None:
            self.sum_i = np.zeros(frames.shape[1:], dtype=np.float64)
            self.sum_i2 = np.zeros(frames.shape[1:], dtype=np.float64)
        elif frames.shape[1:] != self.sum_i.shape:
            raise ValueError(
                f"Frame shape changed from {self.sum_i.shape} to {frames.shape[1:]} during accumulation."
            )
        assert self.sum_i2 is not None
        for frame in frames:
            values = frame.astype(np.float64)
            if self.background is not None:
                values = np.maximum(values - self.background, 0.0)
            self.sum_i += values
            self.sum_i2 += values * values
            self.count += 1

    def finalize(self) -> tuple[np.ndarray, np.ndarray]:
        """Return `(g2_map, mean_intensity)` after at least two accumulated frames."""
        if self.count < 2 or self.sum_i is None or self.sum_i2 is None:
            raise ValueError(f"At least 2 frames are required to compute g²(0), found {self.count}.")
        mean_i = self.sum_i / self.count
        mean_i2 = self.sum_i2 / self.count
        denominator = mean_i * mean_i + self.epsilon
        g2_map = np.full(mean_i.shape, np.nan, dtype=np.float64)
        valid = denominator > 0.0
        g2_map[valid] = mean_i2[valid] / denominator[valid]
        return g2_map, mean_i


def compute_g2_map(
    frames: np.ndarray,
    epsilon: float = 0.0,
    background: Optional[Union[float, np.ndarray]] = None,
) -> np.ndarray:
    """
    Calculate the 2D map of the normalized second-order intensity autocorrelation
    function g^(2)(x, y) per pixel over a stack of camera frames:

        g^(2)(x, y) = <I(x, y)^2> / (<I(x, y)>^2 + epsilon)

    where <...> denotes temporal averaging across all acquired frames.

    Physical context (Quantum / Statistical Optics):
        - Coherent light (Poissonian laser beam, as simulated in Mock camera):
          g^(2) = 1 + Var(I) / <I>^2 = 1 + 1/<I> ≈ 1.0 (for macroscopic counts).
        - Chaotic / Thermal light: g^(2)(0) = 2.0 (intensity bunching).
        - Non-classical single-photon state: g^(2)(0) < 1.0 (anti-bunching).

    Args:
        frames: 3D numpy array of shape (num_frames, height, width), typically uint16.
            Must contain at least 2 frames (num_frames >= 2).
        epsilon: Smoothing / regularization parameter added to the denominator to
            prevent divergence in low-signal or dark regions. Defaults to 0.0.
        background: Optional constant offset (float) or 2D dark frame array to subtract
            from each frame before calculating correlations. Values below zero are clipped.

    Returns:
        g2_map: 2D numpy array of shape (height, width) with float64 values representing
            g^(2) for each pixel. Where the denominator is zero and epsilon is 0.0, NaN is returned.

    Raises:
        ValueError: If frames is not a 3D array or contains fewer than 2 frames.
    """
    if frames.ndim != 3:
        raise ValueError(
            f"Expected a 3D array of shape (num_frames, height, width), got ndim={frames.ndim} with shape {frames.shape}."
        )

    num_frames, height, width = frames.shape
    if num_frames < 2:
        raise ValueError(
            f"At least 2 frames are required to calculate temporal correlation g^(2), received {num_frames}."
        )

    accumulator = StreamingPixelG2(epsilon=epsilon, background=background)
    accumulator.update(frames)
    return accumulator.finalize()[0]


def acquire_and_compute_g2(
    camera: BaseCamera,
    num_frames: int = 50,
    epsilon: float = 0.0,
    background: Optional[Union[float, np.ndarray]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Acquire a sequence of frames from an active camera and compute the 2D g^(2) map.

    Args:
        camera: Connected BaseCamera instance (or MockPcoCamera).
        num_frames: Number of frames to acquire for temporal averaging (default: 50).
        epsilon: Regularization parameter added to denominator (default: 0.0).
        background: Optional dark count pedestal or 2D background array to subtract.

    Returns:
        g2_map: 2D numpy array of shape (height, width) containing g^(2) per pixel.
        summary_stats: Dictionary containing diagnostic metrics:
            - 'num_frames': Number of frames acquired
            - 'mean_intensity_map': 2D mean intensity array
            - 'g2_mean_active': Average g^(2) over pixels with signal > 10% of peak
            - 'g2_center': g^(2) value at sensor center
            - 'exposure_s': Exposure time used during acquisition
    """
    frames, metadata = camera.acquire_frames(num_frames)

    # Calculate 2D g^(2) map
    g2_map = compute_g2_map(frames, epsilon=epsilon, background=background)

    # Diagnostic statistics
    mean_intensity = np.mean(frames.astype(np.float64), axis=0)
    peak_intensity = float(mean_intensity.max())
    active_mask = mean_intensity > (0.1 * peak_intensity)

    center_y, center_x = frames.shape[1] // 2, frames.shape[2] // 2
    g2_center = float(g2_map[center_y, center_x]) if not np.isnan(g2_map[center_y, center_x]) else None
    g2_active_mean = float(np.nanmean(g2_map[active_mask])) if np.any(active_mask) else None

    summary_stats = {
        "num_frames": num_frames,
        "mean_intensity_map": mean_intensity,
        "peak_intensity": peak_intensity,
        "g2_center": g2_center,
        "g2_mean_active": g2_active_mean,
        "exposure_s": camera.get_exposure_time(),
    }

    return g2_map, summary_stats


def compute_g2_from_scan(
    storage_dir: Union[str, Path],
    experiment_prefix: Optional[str] = None,
    epsilon: float = 0.0,
    background: Optional[Union[float, np.ndarray]] = None,
    max_steps: Optional[int] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Load saved HDF5 scan files from a storage directory and compute the 2D g^(2) map
    across all steps using memory-efficient streaming.

    Args:
        storage_dir: Path to directory containing .h5 scan files (e.g. 'data/').
        experiment_prefix: Optional prefix filter (e.g. 'HHG_Scan').
        epsilon: Regularization parameter added to denominator (default: 0.0).
        background: Optional dark pedestal or background offset to subtract (e.g. 100.0).
        max_steps: Maximum number of scan step files to analyze. If None, analyzes all.

    Returns:
        g2_map: 2D numpy array of shape (height, width) containing g^(2) per pixel.
        summary_stats: Diagnostic metrics across the loaded dataset.
    """
    dir_path = Path(storage_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Directory not found: {dir_path}")

    pattern = f"{experiment_prefix}*.h5" if experiment_prefix else "*.h5"
    matched_files = sorted(dir_path.glob(pattern))

    if not matched_files:
        raise FileNotFoundError(f"No scan files matching '{pattern}' in {dir_path}")

    if max_steps is not None and max_steps > 0:
        matched_files = matched_files[:max_steps]

    accumulator = StreamingPixelG2(epsilon=epsilon, background=background)
    first_metadata = None

    for filepath in matched_files:
        metadata = get_scan_metadata(filepath)
        if first_metadata is None:
            first_metadata = metadata
        for batch in iter_scan_step_frames(filepath, batch_size=4):
            accumulator.update(batch)

    g2_map, mean_i = accumulator.finalize()

    peak_intensity = float(mean_i.max())
    active_mask = mean_i > (0.1 * peak_intensity)
    center_y, center_x = mean_i.shape[0] // 2, mean_i.shape[1] // 2

    g2_center = float(g2_map[center_y, center_x]) if not np.isnan(g2_map[center_y, center_x]) else None
    g2_active_mean = float(np.nanmean(g2_map[active_mask])) if np.any(active_mask) else None

    summary_stats = {
        "num_files": len(matched_files),
        "total_frames": accumulator.count,
        "mean_intensity_map": mean_i,
        "peak_intensity": peak_intensity,
        "g2_center": g2_center,
        "g2_mean_active": g2_active_mean,
        "first_file_metadata": first_metadata,
    }

    return g2_map, summary_stats

