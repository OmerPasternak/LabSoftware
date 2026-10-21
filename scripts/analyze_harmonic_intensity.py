"""Report average and STDEV of a harmonic's raw integrated intensity across saved images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from hhg_control.camera.analysis.harmonic_intensity import (
    compute_harmonic_intensity, default_intensity_window_path, make_intensity_window,
    measurement_files, reference_frame, save_intensity_window,
)


def main(argv: list[str] | None = None) -> int:
    """Choose a window once, reuse it, and print two raw-ADU statistics per measurement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="*", type=Path,
                        help="Saved measurement HDF5 file(s), or one folder; omit to open a file picker.")
    parser.add_argument("--window", type=Path, default=None, help="Saved rectangle JSON; default is per-user persistent storage.")
    parser.add_argument("--reference", type=Path,
                        help="Select a new window using frame zero of this saved triggered reference file.")
    parser.add_argument("--select-only", action="store_true",
                        help="Define the window from a single saved reference image, without calculating statistics yet.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--select", action="store_true", help="Choose and save a replacement window.")
    selection.add_argument("--bounds", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                           help="Set a rectangle explicitly, with zero-based half-open pixel edges.")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=Path, help="Optional separate JSON report with statistics and provenance; refuses overwrite.")
    args = parser.parse_args(argv)
    if args.select_only and args.output:
        parser.error("--output is for a statistics report; omit it with --select-only.")
    try:
        if args.select_only and not args.input and args.reference is not None:
            args.input = [args.reference]
        if not args.input:
            from PyQt6.QtWidgets import QApplication, QFileDialog
            app = QApplication.instance() or QApplication([])
            chosen, _ = QFileDialog.getOpenFileNames(None, "Select saved images from one measurement", "",
                                                     "Camera measurement (*.h5 *.hdf5)")
            if not chosen:
                return 0
            args.input = [Path(path) for path in chosen]
        files = measurement_files(args.input)
        window_path = args.window or default_intensity_window_path()
        selecting = (args.select or args.select_only or args.reference is not None
                     or args.bounds is not None or not window_path.exists())
        if selecting:
            reference = args.reference or files[0]
            bounds = args.bounds
            if bounds is None:
                from hhg_control.gui.camera.intensity_window import choose_intensity_bounds
                image, _ = reference_frame(reference)
                bounds = choose_intensity_bounds(image)
                if bounds is None:
                    return 0
            window = make_intensity_window(bounds, reference)
        else:
            with window_path.open(encoding="utf-8") as handle:
                window = json.load(handle)
        if args.select_only:
            save_intensity_window(window_path, window)
            print(f"Saved window: {window_path}", file=sys.stderr)
            return 0
        result = compute_harmonic_intensity(files, window, batch_size=args.batch_size)
        # Only persist a newly chosen window after it fits this measurement.
        if selecting:
            save_intensity_window(window_path, window)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, allow_nan=False)
                handle.write("\n")
        print(f"Average: {result['average_adu']:.12g} ADU")
        print(f"STDEV: {result['stdev_adu']:.12g} ADU")
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
