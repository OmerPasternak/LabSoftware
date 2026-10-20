"""
Optical & HHG Beam Profiling Functions.
Provides beam centroid, waist radius (sigma), FWHM, and Gaussian spatial fitting routines.
"""

from typing import Dict, Optional, Tuple
import numpy as np


def compute_beam_centroid(
    frame: np.ndarray,
    background_threshold: Optional[float] = None
) -> Tuple[float, float]:
    """
    Compute center-of-mass centroid coordinates (x_c, y_c) of an optical beam image.

    Args:
        frame: 2D numpy array representing a single camera image.
        background_threshold: Optional intensity floor below which pixels are zeroed.
            If None, uses mean background level estimated from the image corners.

    Returns:
        (centroid_x, centroid_y): Floating point pixel coordinates.
    """
    img = frame.astype(np.float64)

    if background_threshold is None:
        # Estimate background from the outer 50-pixel margin
        corners = np.concatenate([
            img[:50, :50].ravel(),
            img[:50, -50:].ravel(),
            img[-50:, :50].ravel(),
            img[-50:, -50:].ravel()
        ])
        background_threshold = float(np.median(corners) + 3.0 * np.std(corners))

    # Subtract background threshold and clip below zero
    signal = np.maximum(img - background_threshold, 0.0)
    total_signal = signal.sum()

    if total_signal <= 0.0:
        h, w = frame.shape
        return float(w / 2.0), float(h / 2.0)

    # Axis projections avoid allocating two full-resolution index arrays.
    x_projection = signal.sum(axis=0)
    y_projection = signal.sum(axis=1)
    centroid_x = float(np.dot(np.arange(signal.shape[1], dtype=np.float64), x_projection) / total_signal)
    centroid_y = float(np.dot(np.arange(signal.shape[0], dtype=np.float64), y_projection) / total_signal)

    return centroid_x, centroid_y


def fit_gaussian_beam_profile(frame: np.ndarray) -> Dict[str, float]:
    """
    Compute 2D second moments and Gaussian beam parameters (waist, FWHM, peak intensity).

    Args:
        frame: 2D numpy array of sensor intensity counts.

    Returns:
        Dictionary with:
            - 'centroid_x': Beam center on X-axis (pixels)
            - 'centroid_y': Beam center on Y-axis (pixels)
            - 'sigma_x': Beam waist radius along X (pixels)
            - 'sigma_y': Beam waist radius along Y (pixels)
            - 'fwhm_x': Full-width at half-maximum on X (2.355 * sigma_x)
            - 'fwhm_y': Full-width at half-maximum on Y (2.355 * sigma_y)
            - 'peak_intensity': Maximum sensor count (ADU)
            - 'total_power': Integrated counts above background
    """
    img = frame.astype(np.float64)
    cx, cy = compute_beam_centroid(img)

    # Estimate background from corners
    corners = np.concatenate([
        img[:40, :40].ravel(),
        img[:40, -40:].ravel(),
        img[-40:, :40].ravel(),
        img[-40:, -40:].ravel()
    ])
    bg = float(np.median(corners))
    signal = np.maximum(img - bg, 0.0)
    total_sig = float(signal.sum())

    if total_sig <= 0.0:
        return {
            "centroid_x": cx,
            "centroid_y": cy,
            "sigma_x": 0.0,
            "sigma_y": 0.0,
            "fwhm_x": 0.0,
            "fwhm_y": 0.0,
            "peak_intensity": float(frame.max()),
            "total_power": 0.0
        }

    x_projection = signal.sum(axis=0)
    y_projection = signal.sum(axis=1)
    x_offsets = np.arange(signal.shape[1], dtype=np.float64) - cx
    y_offsets = np.arange(signal.shape[0], dtype=np.float64) - cy
    var_x = float(np.dot(x_offsets * x_offsets, x_projection) / total_sig)
    var_y = float(np.dot(y_offsets * y_offsets, y_projection) / total_sig)

    sigma_x = float(np.sqrt(max(var_x, 0.0)))
    sigma_y = float(np.sqrt(max(var_y, 0.0)))

    # FWHM = 2 * sqrt(2 * ln(2)) * sigma ≈ 2.35482 * sigma
    fwhm_factor = 2.354820045

    return {
        "centroid_x": round(cx, 2),
        "centroid_y": round(cy, 2),
        "sigma_x": round(sigma_x, 2),
        "sigma_y": round(sigma_y, 2),
        "fwhm_x": round(sigma_x * fwhm_factor, 2),
        "fwhm_y": round(sigma_y * fwhm_factor, 2),
        "peak_intensity": float(frame.max()),
        "total_power": round(total_sig, 1)
    }
