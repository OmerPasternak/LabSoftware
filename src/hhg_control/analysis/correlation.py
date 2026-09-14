"""
Quantum Optics Second-Order Correlation Analysis.
Provides routines to calculate normalized intensity autocorrelation maps g^(2)(0)
per pixel across acquired camera frame stacks.
"""

from typing import Any, Dict, Optional, Tuple, Union
import numpy as np

from ..drivers.base_camera import BaseCamera


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

    # Accumulate running sums in float64 to avoid allocating a massive full float64 3D array in memory
    sum_i = np.zeros((height, width), dtype=np.float64)
    sum_i2 = np.zeros((height, width), dtype=np.float64)

    for i in range(num_frames):
        frame_flt = frames[i].astype(np.float64)

        if background is not None:
            frame_flt = np.maximum(frame_flt - background, 0.0)

        sum_i += frame_flt
        sum_i2 += frame_flt * frame_flt

    mean_i = sum_i / num_frames
    mean_i2 = sum_i2 / num_frames

    # Denominator with future smoothing parameter epsilon
    denom = (mean_i ** 2) + float(epsilon)

    # Safe vectorized division: avoid division by zero warnings, assign NaN where denom == 0
    g2_map = np.full((height, width), np.nan, dtype=np.float64)
    valid_mask = denom > 0.0
    g2_map[valid_mask] = mean_i2[valid_mask] / denom[valid_mask]

    return g2_map


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
