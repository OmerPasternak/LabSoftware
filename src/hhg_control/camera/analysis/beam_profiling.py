"""
Optical & HHG Beam Profiling Functions.
Provides beam centroid, waist radius (sigma), FWHM, and Gaussian spatial fitting routines.
"""

from typing import Dict, Optional, Tuple
import numpy as np


def _beam_signal(frame, background):
    """Return separate clipped analysis weights and the common background in ADU.

    Automatic background is the median of four nonoverlapping corner patches,
    each at most one eighth of the corresponding image dimension. This is an
    empirical ROI background, not a claim that a cropped beam has dark borders.
    Supply a measured background for broad/truncated beams. All returned widths
    are moments of the observed image, not an inferred full Gaussian beam.
    """
    img = np.asarray(frame, dtype=np.float64)
    if img.ndim != 2 or min(img.shape) < 2 or not np.all(np.isfinite(img)):
        raise ValueError("Beam profile requires a finite 2D image at least 2x2.")
    if background is None:
        h, w = img.shape
        rows, cols = max(1, h//8), max(1, w//8)
        corners = np.concatenate([img[:rows,:cols].ravel(), img[:rows,-cols:].ravel(),
                                  img[-rows:,:cols].ravel(), img[-rows:,-cols:].ravel()])
        background = float(np.median(corners))
    if not np.isfinite(background):
        raise ValueError("Background must be finite ADU.")
    signal = np.maximum(img - background, 0.0)
    if signal.sum() <= 0:
        raise ValueError("No positive beam signal above background; centroid and width are undefined.")
    return signal, float(background)


def compute_beam_centroid(
    frame: np.ndarray,
    background_threshold: Optional[float] = None
) -> Tuple[float, float]:
    """
    Compute center-of-mass centroid coordinates (x_c, y_c) of an optical beam image.

    Args:
        frame: 2D numpy array representing a single camera image.
        background_threshold: Optional intensity floor below which pixels are zeroed.
            If None, uses a size-aware corner median (see _beam_signal).
            Empty/constant signals above this floor are reported as undefined.

    Returns:
        (centroid_x, centroid_y): Floating point pixel coordinates.
    """
    signal, _ = _beam_signal(frame, background_threshold)
    total_signal = signal.sum()

    # Axis projections avoid allocating two full-resolution index arrays.
    x_projection = signal.sum(axis=0)
    y_projection = signal.sum(axis=1)
    centroid_x = float(np.dot(np.arange(signal.shape[1], dtype=np.float64), x_projection) / total_signal)
    centroid_y = float(np.dot(np.arange(signal.shape[0], dtype=np.float64), y_projection) / total_signal)

    return centroid_x, centroid_y


def fit_gaussian_beam_profile(frame: np.ndarray, background: Optional[float] = None) -> Dict[str, float]:
    """
    Compute observed-image moments and Gaussian-equivalent FWHM in pixels.

    These are truncated-image moments, not a Gaussian fit or inferred full-beam
    parameters. background is an optional measured scalar in ADU; otherwise use
    the same size-aware corner median as compute_beam_centroid.

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
    signal, bg = _beam_signal(frame, background)
    total_sig = float(signal.sum())
    cx = float(np.dot(np.arange(signal.shape[1]), signal.sum(axis=0)) / total_sig)
    cy = float(np.dot(np.arange(signal.shape[0]), signal.sum(axis=1)) / total_sig)

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
        "total_power": round(total_sig, 1),
        "background_adu": bg
    }
