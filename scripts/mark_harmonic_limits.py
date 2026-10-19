"""Show a mean-frame snapshot and save draggable harmonic x-pixel ranges."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector
import numpy as np

from hhg_control.analysis import load_mean_snapshot, save_harmonic_regions
from hhg_control.safe_io import unique_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open a completed camera step's mean-frame snapshot and mark harmonic x ranges."
    )
    parser.add_argument("input", type=Path, help="Completed HDF5 scan-step file containing /images.")
    parser.add_argument(
        "--output", type=Path,
        help="JSON sidecar path. Defaults to <input>_harmonics.json; existing paths are preserved.",
    )
    args = parser.parse_args(argv)

    try:
        image, metadata = load_mean_snapshot(args.input)
    except Exception as exc:
        print(f"[ERROR] Could not load snapshot: {exc}", file=sys.stderr)
        return 1

    height, width = image.shape
    roi = metadata.get("roi_bounds")
    regions: list[dict[str, int | str]] = []
    patches = []
    texts = []
    status = None
    figure, axis = plt.subplots(figsize=(11, 6), constrained_layout=True)
    displayed = axis.imshow(image, origin="upper", aspect="auto", cmap="gray")
    axis.set_xlim(-0.5, width - 0.5)
    axis.set_xlabel("x pixel (frequency / wavelength dispersion; no calibration applied)")
    axis.set_ylabel("y pixel")
    axis.set_title(
        f"{args.input.name}: mean of {metadata['snapshot_frame_count']} frames\n"
        "Drag horizontally to mark a harmonic range. U = undo, S = save, Q = close."
    )
    figure.colorbar(displayed, ax=axis, label="Mean intensity (ADU)")
    status = axis.text(
        0.01, 0.99, "Selected ranges: 0", transform=axis.transAxes,
        color="yellow", va="top", ha="left",
        bbox={"facecolor": "black", "alpha": 0.55, "edgecolor": "none"},
    )

    def onselect(xmin: float, xmax: float) -> None:
        start = max(0, min(width - 1, int(np.floor(min(xmin, xmax) + 0.5))))
        inclusive_stop = max(0, min(width - 1, int(np.floor(max(xmin, xmax) + 0.5))))
        stop = inclusive_stop + 1
        if stop <= start:
            return
        label = f"H{len(regions) + 1}"
        regions.append({"label": label, "x_start": start, "x_stop": stop})
        patch = axis.axvspan(start - 0.5, stop - 0.5, color="cyan", alpha=0.18, linewidth=1.5)
        text = axis.text(
            (start + stop - 1) / 2, 0.03 * height, label,
            color="yellow", ha="center", va="bottom", weight="bold",
            bbox={"facecolor": "black", "alpha": 0.55, "edgecolor": "none"},
        )
        patches.append(patch)
        texts.append(text)
        status.set_text(f"Selected ranges: {len(regions)} — last {label} [{start}, {stop})")
        figure.canvas.draw_idle()

    selector = SpanSelector(
        axis,
        onselect,
        "horizontal",
        useblit=True,
    )

    def on_key(event) -> None:
        if event.key in {"u", "backspace"}:
            if regions:
                regions.pop()
                patches.pop().remove()
                texts.pop().remove()
                status.set_text(f"Selected ranges: {len(regions)} — last range removed")
                figure.canvas.draw_idle()
            return
        if event.key in {"q", "escape"}:
            plt.close(figure)
            return
        if event.key not in {"s", "enter"}:
            return
        try:
            default_output = args.input.with_name(f"{args.input.stem}_harmonics.json")
            output_path = args.output if args.output is not None else unique_output(default_output)
            if output_path.exists():
                raise FileExistsError(f"Refusing to overwrite existing harmonic map: {output_path}")
            snapshot_png = unique_output(output_path.with_suffix(".png"))
            created_snapshot = False
            try:
                with snapshot_png.open("xb") as handle:
                    created_snapshot = True
                    figure.savefig(handle, format="png", dpi=180)
                saved = save_harmonic_regions(
                    output_path,
                    regions,
                    args.input,
                    (height, width),
                    roi_bounds=roi,
                    annotated_snapshot_path=snapshot_png,
                )
            except Exception:
                if created_snapshot:
                    snapshot_png.unlink(missing_ok=True)
                raise
            print(f"Saved {len(regions)} harmonic ranges: {saved.resolve()}")
            print(f"Saved annotated snapshot: {snapshot_png.resolve()}")
            plt.close(figure)
        except Exception as exc:
            status.set_text(f"Cannot save: {exc}")
            figure.canvas.draw_idle()

    figure.canvas.mpl_connect("key_press_event", on_key)
    # Keep the selector referenced for the lifetime of the interactive figure.
    figure._harmonic_span_selector = selector
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
