"""
Runnable script: Quantum Optics g^(2) Autocorrelation Analysis on Saved Scan Data.
Loads extracted HDF5 scan datasets and computes a 2D map of g^(2) per pixel.
"""

import argparse
from pathlib import Path
import sys
import numpy as np

# Ensure UTF-8 output on Windows consoles with Hebrew or international paths
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hhg_control.analysis import compute_g2_from_scan


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run per-pixel g^(2) intensity autocorrelation analysis on saved HDF5 scan data."
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Path to folder containing saved HDF5 scan step files (default: 'data').",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="HHG_Scan",
        help="Experiment file prefix filter (default: 'HHG_Scan').",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.0,
        help="Smoothing parameter added to denominator to prevent divergence (default: 0.0).",
    )
    parser.add_argument(
        "--background",
        type=float,
        default=100.0,
        help="Dark pedestal / background counts in ADU to subtract (default: 100.0).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum number of scan files to process (default: all available).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to save the 2D g^(2) map (.npy or .h5).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    data_path = Path(args.data_dir)

    print("=" * 65)
    print("  SAVED DATA ANALYSIS: 2D QUANTUM OPTICS g^(2) MAP")
    print("=" * 65)
    print(f"Data Directory : {data_path.resolve()}")
    print(f"File Prefix    : {args.prefix}")
    print(f"Epsilon (ε)    : {args.epsilon}")
    print(f"Background ADU : {args.background}")
    if args.max_steps:
        print(f"Step Limit     : {args.max_steps}")
    print("-" * 65)

    try:
        g2_map, stats = compute_g2_from_scan(
            storage_dir=data_path,
            experiment_prefix=args.prefix,
            epsilon=args.epsilon,
            background=args.background,
            max_steps=args.max_steps,
        )
    except Exception as exc:
        print(f"[ERROR] Failed to compute g^(2) from scan: {exc}")
        return 1

    print("[SUCCESS] Data loaded and processed across all steps.")
    print("-" * 65)
    print("ACQUISITION & DATASET METRICS:")
    print(f"  Total Scan Files Analyzed : {stats['num_files']}")
    print(f"  Total Frames Processed    : {stats['total_frames']}")
    print(f"  2D Map Dimensions (H x W) : {g2_map.shape[0]} x {g2_map.shape[1]}")
    print(f"  Peak Intensity (signal)   : {stats['peak_intensity']:.1f} ADU")

    first_meta = stats.get("first_file_metadata") or {}
    exp_time = first_meta.get("exposure_duration_seconds", first_meta.get("exposure_time_s"))
    param_name = first_meta.get("scan_parameter_display_name", first_meta.get("scan_parameter_name"))
    if exp_time is not None:
        print(f"  Sensor Exposure Duration  : {float(exp_time) * 1e3:.2f} ms")
    if param_name:
        print(f"  Scan Parameter            : {param_name}")

    print("-" * 65)
    print("g^(2) AUTOCORRELATION RESULTS:")
    center_y, center_x = g2_map.shape[0] // 2, g2_map.shape[1] // 2
    print(f"  Beam Center Pixel (x, y)  : ({center_x}, {center_y})")
    print(f"  g^(2) at Beam Center      : {stats['g2_center']:.5f}")
    print(f"  g^(2) Mean over Active ROI: {stats['g2_mean_active']:.5f}")

    # Physical verification check
    if stats["g2_mean_active"] is not None:
        deviation = abs(stats["g2_mean_active"] - 1.0)
        print(f"  Deviation from Coherent 1 : {deviation:.5f}")
        if deviation < 0.05:
            print("  Physical Consistency Check: PASSED (g^(2) ≈ 1.0, Poissonian coherent light)")
        else:
            print("  Physical Consistency Check: WARNING (deviation > 5%)")

    # Optional saving of the g2_map
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix == ".npy":
            np.save(out_path, g2_map)
            print(f"\n[INFO] Saved g^(2) array to: {out_path.resolve()}")
        elif out_path.suffix in [".h5", ".hdf5"]:
            import h5py
            with h5py.File(out_path, "w") as h5f:
                dset = h5f.create_dataset("g2_map", data=g2_map, compression="gzip")
                dset.attrs["description"] = "2D normalized intensity autocorrelation map g^(2)(x, y)"
                dset.attrs["epsilon"] = args.epsilon
                dset.attrs["background_subtracted"] = args.background
                dset.attrs["total_frames"] = stats["total_frames"]
            print(f"\n[INFO] Saved g^(2) HDF5 dataset to: {out_path.resolve()}")
        else:
            np.save(out_path.with_suffix(".npy"), g2_map)
            print(f"\n[INFO] Saved g^(2) array to: {out_path.with_suffix('.npy').resolve()}")

    print("=" * 65)
    return 0


if __name__ == "__main__":
    exit(main())
