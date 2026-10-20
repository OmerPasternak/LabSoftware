"""Offline SI S8/S9-S11 MCP calibration for matched ON/OFF/background stacks.

Reference: Even Tzur et al., arXiv:2511.18362v1, SI pp. S4-S7.
No hardware access; source HDF5 files are read only. The inferred intercept is
conditional on linear MCP response, constant light statistics and approximately
constant MCP response coefficient over the selected pixels, independent additive
background, and single-shot frames. These assumptions cannot be proved by a fit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

import numpy as np

from .hdf5_io import iter_scan_step_frames
from .spectral import _read_source_metadata, load_harmonic_regions


def _moments(path: Path, regions: list[dict], batch_size: int) -> dict:
    """Stream per-pixel and integrated-band unbiased moments using stable merges."""
    metadata, shape = _read_source_metadata(path)
    if shape[0] < 3:
        raise ValueError(f"At least 3 single-shot frames are required: {path}")
    count = 0
    mean = np.zeros(shape[1:], dtype=float)
    m2 = np.zeros_like(mean)
    integrated_mean = np.zeros(len(regions))
    integrated_m2 = np.zeros(len(regions))
    clipped = np.zeros(shape[1:], dtype=bool)
    for batch in iter_scan_step_frames(path, batch_size=batch_size):
        values = batch.astype(np.float64)
        if not np.all(np.isfinite(values)):
            raise ValueError(f"Nonfinite image samples in {path}")
        if np.issubdtype(batch.dtype, np.integer):
            clipped |= np.any(batch == np.iinfo(batch.dtype).max, axis=0)
        bands = np.column_stack([
            values[:, :, r["x_start"]:r["x_stop"]].sum(axis=(1, 2)) for r in regions
        ])
        n = len(values)
        total = count + n
        for data, current_mean, current_m2 in (
            (values, mean, m2), (bands, integrated_mean, integrated_m2)
        ):
            batch_mean = data.mean(axis=0)
            delta = batch_mean - current_mean
            current_m2 += np.sum((data - batch_mean)**2, axis=0) + delta**2 * count * n / total
            current_mean += delta * n / total
        count = total
    return dict(mean=mean, variance=m2/(count-1), count=count, metadata=metadata,
                shape=shape, clipped=clipped, integrated_mean=integrated_mean,
                integrated_variance=integrated_m2/(count-1))


def fit_mcp_intercept(mean: np.ndarray, variance: np.ndarray, valid: np.ndarray) -> dict:
    """Fit SI S8: g_detected = g_light + k/mean, without clipping negative data.

    Equal-weight least squares is performed in g-space; fit residuals are
    diagnostics, not a confidence interval (nearby detector pixels are correlated).
    Require at least eight usable pixels and a twofold intensity range to avoid
    fitting an intercept from an almost constant-brightness window.
    """
    mu = np.asarray(mean, dtype=float)[valid].ravel()
    var = np.asarray(variance, dtype=float)[valid].ravel()
    if mu.size < 8:
        return {"status": "INSUFFICIENT_PIXELS", "pixels_used": int(mu.size), "g2_light": None}
    ratio = float(np.quantile(mu, 0.9) / np.quantile(mu, 0.1))
    if ratio < 2:
        return {"status": "INSUFFICIENT_INTENSITY_RANGE", "pixels_used": int(mu.size),
                "intensity_ratio_p90_p10": ratio, "g2_light": None}
    x = 1 / mu
    y = 1 + var / mu**2
    scale = float(np.mean(x))
    design = np.column_stack((np.ones_like(x), x / scale))
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    intercept, slope = float(coefficients[0]), float(coefficients[1] / scale)
    residual = y - design @ coefficients
    ss = float(np.sum((y - y.mean())**2))
    status = "MODEL_ESTIMATE" if rank == 2 and intercept >= 0 and slope >= 0 else "UNPHYSICAL_FIT"
    # Retain representative diagnostic points; all usable pixels enter the fit.
    sample = np.linspace(0, len(x)-1, min(len(x), 256), dtype=int)
    return {
        "status": status, "g2_light": intercept if status == "MODEL_ESTIMATE" else None,
        "unconstrained_intercept": intercept, "mcp_coefficient_adu": slope,
        "pixels_used": int(mu.size), "intensity_ratio_p90_p10": ratio,
        "fit_residual_rms": float(np.sqrt(np.mean(residual**2))),
        "fit_r_squared": 1 - float(np.sum(residual**2))/ss if ss > 0 else None,
        "uncertainty": "Not assigned: pixels share shot fluctuations; OLS pixel errors would overstate precision.",
        "inverse_mean_adu": x[sample].tolist(), "detected_g2": y[sample].tolist(),
    }


def compute_si_harmonic_g2(
    bsv_on: str | Path, bsv_off: str | Path, background: str | Path,
    harmonic_map_path: str | Path, *, batch_size: int = 16, min_mean_snr: float = 3.0,
) -> dict[str, Any]:
    """Apply SI reference subtraction and pixel-intensity MCP fit per harmonic.

    Odd orders use no-XUV BACKGROUND; even/half-integer orders use BSV OFF.
    Independent reference runs supply mean/variance, never shotwise subtraction.
    Every harmonic needs its actual order in the map; pixel positions alone do
    not establish order. Missing metadata is reported, not invented. No photon
    count conversion, persistence deconvolution or temporal de-mixing is assumed.
    """
    if batch_size < 1 or not np.isfinite(min_mean_snr) or min_mean_snr <= 0:
        raise ValueError("batch_size and min_mean_snr must be positive.")
    paths = [Path(p) for p in (bsv_on, bsv_off, background)]
    if len({p.resolve() for p in paths}) != 3:
        raise ValueError("BSV ON, BSV OFF and BACKGROUND must be three distinct files.")
    harmonic_map = load_harmonic_regions(harmonic_map_path)
    regions = harmonic_map["harmonics"]
    if any("order" not in r for r in regions):
        raise ValueError("Enter the actual harmonic orders in Mark Harmonics and save before SI analysis.")
    warnings = []
    headers = [_read_source_metadata(path) for path in paths]
    for path, (metadata, shape) in zip(paths, headers):
        if shape[0] < 3:
            raise ValueError(f"At least 3 single-shot frames are required: {path}")
        if tuple(harmonic_map["image_shape_yx"]) != shape[1:]:
            raise ValueError(f"Harmonic map image shape does not match {path.name}.")
        roi = metadata.get("roi_bounds")
        if roi is not None and harmonic_map.get("roi_bounds_xyxy") != roi:
            raise ValueError(f"Harmonic map ROI does not match {path.name}.")
        if "complete" not in metadata:
            warnings.append(f"{path.name}: completion metadata absent (legacy file); confirm acquisition completed.")
    for key in ("roi_bounds", "readout_mode", "camera_serial", "gain", "mcp_voltage", "binning"):
        entries = [metadata.get(key) for metadata, _ in headers]
        known = [entry for entry in entries if entry is not None]
        if known and any(entry != known[0] for entry in known[1:]):
            raise ValueError(f"Reference mismatch in {key}; use matched detector settings.")
        if len(known) != 3:
            warnings.append(f"{key}: not recorded in all inputs; matching settings not verified.")
    exposures = [m.get("exposure_time_s", m.get("exposure_duration_seconds")) for m, _ in headers]
    known_exposures = [float(value) for value in exposures if value is not None]
    if known_exposures and not np.allclose(known_exposures, known_exposures[0], rtol=1e-9, atol=1e-12):
        raise ValueError("Reference mismatch in exposure; use matched detector settings.")
    if len(known_exposures) != 3:
        warnings.append("exposure: not recorded in all inputs; matching settings not verified.")
    stacks = [_moments(path, regions, batch_size) for path in paths]
    on, off, bg = stacks
    results = []
    for index, region in enumerate(regions):
        order = region["order"]
        odd = order.is_integer() and int(order) % 2 == 1
        reference = bg if odd else off
        a, b = region["x_start"], region["x_stop"]
        mu = on["mean"][:, a:b] - reference["mean"][:, a:b]
        var = on["variance"][:, a:b] - reference["variance"][:, a:b]
        mean_error = np.sqrt(on["variance"][:, a:b]/on["count"] +
                             reference["variance"][:, a:b]/reference["count"])
        clipped = on["clipped"][:, a:b] | reference["clipped"][:, a:b]
        valid = (mu > 0) & (mu >= min_mean_snr * mean_error) & (var >= 0) & ~clipped
        fit = fit_mcp_intercept(mu, var, valid)
        integrated_mu = float(on["integrated_mean"][index] - reference["integrated_mean"][index])
        integrated_var = float(on["integrated_variance"][index] - reference["integrated_variance"][index])
        results.append({
            **region, "reference": "BACKGROUND" if odd else "BSV_OFF", "fit": fit,
            "integrated_net_mean_adu": integrated_mu,
            "background_corrected_integrated_g2": 1 + integrated_var/integrated_mu**2 if integrated_mu > 0 else None,
            "pixels_total": int(mu.size), "pixels_nonpositive_mean": int(np.sum(mu <= 0)),
            "pixels_negative_net_variance": int(np.sum(var < 0)),
            "pixels_below_mean_snr": int(np.sum(mu < min_mean_snr*mean_error)),
            "pixels_at_dtype_ceiling": int(np.sum(clipped)),
        })
    return {
        "analysis_type": "SI background-corrected pixel-intensity MCP intercept estimate",
        "method_reference": "Even Tzur et al., arXiv:2511.18362v1, SI S8-S11, pp. S4-S7",
        "source_files": dict(zip(("BSV_ON", "BSV_OFF", "BACKGROUND"), map(lambda p: str(p.resolve()), paths))),
        "frame_counts": dict(zip(("BSV_ON", "BSV_OFF", "BACKGROUND"), (s["count"] for s in stacks))),
        "harmonic_map": harmonic_map,
        "harmonic_map_sha256": hashlib.sha256(json.dumps(harmonic_map, sort_keys=True).encode()).hexdigest(),
        "min_mean_snr": float(min_mean_snr), "warnings": warnings,
        "assumptions": [
            "Independent additive background, stable gain/settings and alignment across reference runs.",
            "Linear unsaturated MCP/camera response; dtype-ceiling check does not establish MCP linearity or ADC headroom.",
            "Constant light g2 and approximately constant MCP coefficient across fitted pixels within each harmonic.",
            "Single-shot timing and negligible/independently calibrated phosphor persistence; no 1.05-pulse factor assumed.",
            "SI high-photon-number approximation, not exact photon-number reconstruction or nonclassicality proof.",
            "Separate reference runs cannot measure signal-background covariance; subtraction assumes it vanishes.",
        ],
        "harmonics": results,
    }
