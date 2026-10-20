"""Estimate SI MCP-corrected harmonic g2 from saved ON/OFF/background stacks."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import h5py
import matplotlib
matplotlib.use("Agg" if "--no-show" in sys.argv else "QtAgg")
import matplotlib.pyplot as plt
import numpy as np

from hhg_control.analysis import compute_harmonic_g2_from_scan
from hhg_control.safe_io import unique_output
from hhg_control.analysis.spectral import default_harmonic_map_path
from hhg_control.analysis.harmonic_statistics import compute_si_harmonic_g2
from hhg_control.analysis.plot_windows import show_analysis_figures


def _si_outputs(args) -> int:
    """Save SI fit diagnostics and estimates without modifying acquisition data."""
    if not args.input.is_file():
        raise ValueError("SI mode needs one matched ON/OFF/BACKGROUND file set per delay, not a mixed folder.")
    print("Analyzing saved ON/OFF/BACKGROUND frames (no camera connection)...", flush=True)
    result = compute_si_harmonic_g2(args.input, args.bsv_off, args.background,
                                  args.harmonic_map, batch_size=args.batch_size)
    output = _output_directory(args.input)
    base = output / f"{args.input.stem}_si_g2_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    report = unique_output(base.with_suffix(".json"))
    table = unique_output(base.with_suffix(".csv"))
    plot = unique_output(base.with_suffix(".png"))
    calibration_plot = unique_output(base.with_name(base.name + "_calibration").with_suffix(".png"))
    count = len(result["harmonics"])
    columns = min(3, count)
    nrows = (count + columns - 1) // columns
    fig, axes = plt.subplots(nrows, columns, figsize=(4*columns, 3*nrows),
                             squeeze=False, constrained_layout=True)
    rows = []
    for axis, harmonic in zip(axes.ravel(), result["harmonics"]):
        fit = harmonic["fit"]
        x = np.array(fit.get("inverse_mean_adu", []))
        y = np.array(fit.get("detected_g2", []))
        axis.scatter(x, y, s=5, alpha=0.4)
        if "unconstrained_intercept" in fit:
            xx = np.linspace(0, x.max(), 100)
            axis.plot(xx, fit["unconstrained_intercept"] + fit["mcp_coefficient_adu"]*xx)
        estimate = f"{fit['g2_light']:.3g}" if fit['g2_light'] is not None else "unavailable"
        axis.set(xlabel="1 / mean (1/ADU)", ylabel="Pixel g²",
                 title=f"{harmonic['label']}: g² = {estimate}")
        rows.append(dict(harmonic=harmonic["label"], order=harmonic["order"],
                         reference=harmonic["reference"], status=fit["status"], g2_light=fit["g2_light"],
                         pixels_used=fit["pixels_used"], integrated_net_mean_adu=harmonic["integrated_net_mean_adu"],
                         integrated_background_corrected_g2=harmonic["background_corrected_integrated_g2"]))
    for axis in axes.ravel()[count:]:
        axis.set_visible(False)
    fig.canvas.manager.set_window_title("MCP calibration")
    fig.savefig(calibration_plot, dpi=160)
    summary, axis = plt.subplots(figsize=(9, 5), constrained_layout=True)
    summary.canvas.manager.set_window_title("Harmonic g²")
    available = [h for h in result["harmonics"] if h["fit"]["g2_light"] is not None]
    available.sort(key=lambda h: h['order'])
    axis.plot([h["order"] for h in available], [h["fit"]["g2_light"] for h in available], "o")
    axis.set(xlabel="Harmonic order", ylabel="Estimated light g²", title="Harmonic g²")
    axis.grid(True, alpha=.2)
    missing = [h["label"] for h in result["harmonics"] if h["fit"]["g2_light"] is None]
    if missing:
        axis.text(.02, .98, "Unavailable: " + ", ".join(missing), transform=axis.transAxes, va="top")
    summary.savefig(plot, dpi=160)
    with report.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    with table.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved SI estimates and fit diagnostics: {report}\n{table}\n{plot}\n{calibration_plot}", flush=True)
    if any(h["fit"]["g2_light"] is None for h in result["harmonics"]):
        print("[WARNING] Some harmonics could not be calibrated; see fit status. No raw fallback was substituted.")
    if args.no_show:
        plt.close(fig)
        plt.close(summary)
    else:
        show_analysis_figures()
    return 0


def _input_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    candidates = sorted((*path.glob("*.h5"), *path.glob("*.hdf5")))
    files = []
    for candidate in candidates:
        try:
            with h5py.File(candidate, "r") as source:
                if "images" in source:
                    files.append(candidate)
        except OSError as exc:
            raise ValueError(f"Could not inspect {candidate}: {exc}") from exc
    if not files:
        raise FileNotFoundError(f"No HDF5 camera step files found in {path}.")
    return files


def _output_directory(path: Path) -> Path:
    parent = path if path.is_dir() else path.parent
    output = parent / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    return output


def _plot(results: list[dict], path: Path, *, show: bool = True) -> None:
    labels = list(dict.fromkeys(h["label"] for result in results for h in result["harmonics"]))
    fig, axis = plt.subplots(figsize=(9, 5), constrained_layout=True)
    x = np.arange(len(results))
    for label in labels:
        ys, errors = [], []
        for result in results:
            item = next((h for h in result["harmonics"] if h["label"] == label), None)
            ys.append(np.nan if item is None or item["g2_zero_delay"] is None else item["g2_zero_delay"])
            errors.append(np.nan if item is None or item["standard_error"] is None else 1.96 * item["standard_error"])
        axis.errorbar(x, ys, yerr=errors, marker="o", capsize=3, label=label)
    axis.set_xticks(x, [Path(r["source_file"]).stem for r in results], rotation=45, ha="right")
    axis.set_ylabel("Per-harmonic g²(0)")
    axis.set_xlabel("HDF5 scan step")
    axis.set_title("Temporal g²(0) by integrated harmonic signal (95% normal-approximation CI)")
    axis.grid(True, alpha=0.25)
    axis.legend(title="Harmonic")
    fig.savefig(path, dpi=160)
    if show:
        show_analysis_figures()
    else:
        plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate light g2 with the SI background/MCP calibration from saved frames."
    )
    parser.add_argument("input", type=Path, help="One completed HDF5 step or folder of step files.")
    parser.add_argument("harmonic_map", type=Path, nargs="?", default=None,
                        help="Optional map override; otherwise the persistent confirmed map is used.")
    parser.add_argument("--bsv-off", type=Path, help="Matched coherent-only reference HDF5 file.")
    parser.add_argument("--background", type=Path, help="Matched no-XUV reference HDF5 file.")
    parser.add_argument("--raw-intensity", action="store_true",
                        help="Explicit legacy raw ADU statistic; does not estimate corrected light g2.")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--no-show", action="store_true", help="Save plots without opening windows (automation only).")
    args = parser.parse_args(argv)
    args.harmonic_map = args.harmonic_map or default_harmonic_map_path()
    try:
        if not args.raw_intensity:
            if args.bsv_off is None or args.background is None:
                raise ValueError("SI analysis requires --bsv-off and --background (or explicitly choose --raw-intensity).")
            return _si_outputs(args)
        if args.bsv_off is not None or args.background is not None:
            raise ValueError("Do not combine --raw-intensity with SI reference inputs.")
        files = _input_files(args.input)
        results = [
            compute_harmonic_g2_from_scan(path, args.harmonic_map, batch_size=args.batch_size)
            for path in files
        ]
        output_dir = _output_directory(args.input)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = args.input.name if args.input.is_dir() else args.input.stem
        base = output_dir / f"{stem}_harmonic_g2_{stamp}"
        report_path = unique_output(base.with_suffix(".json"))
        csv_path = unique_output(base.with_suffix(".csv"))
        plot_path = unique_output(base.with_suffix(".png"))

        report = {
            "analysis_type": "per-harmonic temporal g2(0)",
            "harmonic_map_file": str(args.harmonic_map.resolve()),
            "estimator": results[0]["estimator"],
            "uncertainty": results[0]["uncertainty"],
            "results": results,
        }
        with report_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
        with csv_path.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "source_file", "total_frames", "harmonic", "x_start", "x_stop",
                "mean_signal_adu", "g2_zero_delay", "standard_error", "ci95_low", "ci95_high",
            ])
            writer.writeheader()
            for result in results:
                for item in result["harmonics"]:
                    ci = item["ci95"] or [None, None]
                    writer.writerow({
                        "source_file": result["source_file"], "total_frames": result["total_frames"],
                        "harmonic": item["label"], "x_start": item["x_start"], "x_stop": item["x_stop"],
                        "mean_signal_adu": item["mean_signal_adu"], "g2_zero_delay": item["g2_zero_delay"],
                        "standard_error": item["standard_error"], "ci95_low": ci[0], "ci95_high": ci[1],
                    })
        _plot(results, plot_path, show=not args.no_show)
        print(f"Analyzed {len(files)} HDF5 step(s), {sum(r['total_frames'] for r in results)} frames.")
        print(f"Saved results: {report_path}")
        print(f"Saved table: {csv_path}")
        print(f"Saved plot: {plot_path}")
    except Exception as exc:
        print(f"[ERROR] Harmonic g2 analysis failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
