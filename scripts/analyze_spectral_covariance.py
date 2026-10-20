"""Compute and show x-pixel Pearson correlations (legacy script filename)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import h5py
import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import numpy as np

from hhg_control.analysis import compute_spectral_pearson_from_scan
from hhg_control.safe_io import unique_output
from hhg_control.analysis.plot_windows import show_analysis_figures


def _default_output(input_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if input_path.is_dir():
        parent = input_path / "analysis"
        stem = input_path.name or "scan"
    else:
        parent = input_path.parent / "analysis"
        stem = input_path.stem
    parent.mkdir(parents=True, exist_ok=True)
    return unique_output(parent / f"{stem}_spectral_pearson_{stamp}.h5")


def _save_result(
    output_path: Path,
    correlation: np.ndarray,
    mean_spectrum: np.ndarray,
    summary: dict,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(output_path, "x") as h5f:
        h5f.attrs["analysis_type"] = "x-pixel Pearson correlation"
        h5f.attrs["correlation_estimator"] = "centered sample covariance / (std_i * std_j)"
        h5f.attrs["constant_pixel_policy"] = "NaN (undefined), including diagonal"
        h5f.attrs["timing_s_json"] = json.dumps(summary["timing_s"])
        h5f.attrs["spectra_block_frames"] = summary["spectra_block_frames"]
        h5f.attrs["undefined_constant_pixels"] = summary["undefined_constant_pixels"]
        h5f.attrs["total_frames"] = int(summary["total_frames"])
        h5f.attrs["source_files_json"] = json.dumps(summary["source_files"], ensure_ascii=False)
        h5f.attrs["source_metadata_json"] = json.dumps(
            summary["source_metadata"], ensure_ascii=False, default=str
        )
        h5f.attrs["image_shape_yx"] = summary["image_shape_yx"]
        h5f.attrs["y_range_local_half_open"] = summary["y_range_local"]
        correlation_dset = h5f.create_dataset(
            "frequency_pixel_pearson_r", data=correlation, compression="gzip", compression_opts=1
        )
        correlation_dset.attrs["axes"] = "x_pixel_i, x_pixel_j"
        correlation_dset.attrs["description"] = (
            "Pearson correlation across frames of each pair of x-axis spectral pixels; "
            "each per-frame spectrum is summed over the selected y rows."
        )
        correlation_dset.attrs["units"] = "dimensionless"
        h5f.create_dataset("x_pixel_index", data=summary["x_pixel_indices"])
        mean_dset = h5f.create_dataset("mean_spectrum_adu", data=mean_spectrum)
        mean_dset.attrs["axis"] = "x_pixel_index"
        mean_dset.attrs["units"] = "ADU"


def _plot_result(
    correlation: np.ndarray,
    x_pixels: np.ndarray,
    y_range: tuple[int, int],
    output_png: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(9, 7), constrained_layout=True)
    figure.canvas.manager.set_window_title("X Pearson correlation")
    cmap = plt.get_cmap("coolwarm").with_extremes(bad="0.75")
    image = axis.imshow(
        np.ma.masked_invalid(correlation),
        origin="lower",
        interpolation="nearest",
        aspect="auto",
        cmap=cmap,
        vmin=-1,
        vmax=1,
        extent=(x_pixels[0] - 0.5, x_pixels[-1] + 0.5,
                x_pixels[0] - 0.5, x_pixels[-1] + 0.5),
    )
    axis.set_xlabel("x pixel j")
    axis.set_ylabel("x pixel i")
    axis.set_title("Pearson correlation (gray = undefined)")
    figure.colorbar(image, ax=axis, label="Pearson r")
    figure.savefig(output_png, dpi=160)
    print(f"Saved Pearson heatmap: {output_png.resolve()}")
    show_analysis_figures()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute Pearson correlation between x-axis frequency/wavelength pixels "
            "across frames, after summing the selected y rows."
        )
    )
    parser.add_argument("input", type=Path, help="One completed .h5 step file or a folder of matching step files.")
    parser.add_argument(
        "--y-range", nargs=2, type=int, metavar=("START", "STOP"),
        help="Optional zero-based, half-open y range [START, STOP); default uses all rows.",
    )
    parser.add_argument("--batch-size", type=int, default=4, help="Frames read per HDF5 batch (default: 4).")
    parser.add_argument("--spectra-block-frames", type=int, default=256,
                        help="Reduced spectra per matrix update; does not buffer raw image stacks.")
    parser.add_argument("--output", type=Path, help="Output HDF5 path; existing files are never overwritten.")
    parser.add_argument("--no-plot", action="store_true", help="Save only the HDF5 result, without opening a plot.")
    args = parser.parse_args(argv)

    try:
        y_range = tuple(args.y_range) if args.y_range else None
        correlation, mean_spectrum, summary = compute_spectral_pearson_from_scan(
            args.input, y_range=y_range, batch_size=args.batch_size,
            spectra_block_frames=args.spectra_block_frames,
        )
        actual_y_range = tuple(summary["y_range_local"])
        output_path = args.output or _default_output(args.input)
        _save_result(output_path, correlation, mean_spectrum, summary)
        print(f"Frames: {summary['total_frames']}")
        print(f"Matrix: {correlation.shape[0]} x {correlation.shape[1]} x-pixel pairs")
        print(f"Input files: {len(summary['source_files'])}")
        print(f"Saved Pearson HDF5: {output_path.resolve()}")
        print(f"Timing (s): {json.dumps(summary['timing_s'])}")
        if not args.no_plot:
            output_png = unique_output(output_path.with_suffix(".png"))
            _plot_result(correlation, summary["x_pixel_indices"], actual_y_range, output_png)
    except Exception as exc:
        print(f"[ERROR] Spectral Pearson correlation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
