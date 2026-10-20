"""Compute temporal per-harmonic g2(0) for completed HDF5 camera steps."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from hhg_control.analysis import compute_harmonic_g2_from_scan
from hhg_control.safe_io import unique_output


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


def _plot(results: list[dict], path: Path) -> None:
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
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze each marked harmonic's frame-to-frame integrated intensity g2(0)."
    )
    parser.add_argument("input", type=Path, help="One completed HDF5 step or folder of step files.")
    parser.add_argument("harmonic_map", type=Path, help="Reusable *_harmonics.json saved by Mark Harmonics.")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)
    try:
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
        _plot(results, plot_path)
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
