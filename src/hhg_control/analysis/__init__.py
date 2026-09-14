"""
Data Analysis Package for HHG & Attosecond Spectroscopy.
Provides MATLAB-compatible loaders and beam/spectral analysis functions.
"""

from .hdf5_io import load_scan_step, load_full_scan, get_scan_metadata
from .beam_profiling import fit_gaussian_beam_profile, compute_beam_centroid

__all__ = [
    "load_scan_step",
    "load_full_scan",
    "get_scan_metadata",
    "fit_gaussian_beam_profile",
    "compute_beam_centroid",
]

