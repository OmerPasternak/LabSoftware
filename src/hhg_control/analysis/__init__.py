"""
Data Analysis Package for HHG & Attosecond Spectroscopy.
Provides MATLAB-compatible loaders and beam/spectral analysis functions.
"""

from .hdf5_io import load_scan_step, load_full_scan, get_scan_metadata, iter_scan_step_frames
from .beam_profiling import fit_gaussian_beam_profile, compute_beam_centroid
from .correlation import StreamingPixelG2, compute_g2_map, acquire_and_compute_g2, compute_g2_from_scan

__all__ = [
    "load_scan_step",
    "load_full_scan",
    "get_scan_metadata",
    "iter_scan_step_frames",
    "fit_gaussian_beam_profile",
    "compute_beam_centroid",
    "compute_g2_map",
    "StreamingPixelG2",
    "acquire_and_compute_g2",
    "compute_g2_from_scan",
]
