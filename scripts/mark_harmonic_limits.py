"""Detect and review harmonic windows in saved data; never connects to hardware."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector, Slider, Button, TextBox
import numpy as np

from hhg_control.analysis.spectral import (
    default_harmonic_map_path, detect_harmonic_regions, load_mean_snapshot,
    save_harmonic_regions, validate_harmonic_regions,
)
from hhg_control.safe_io import unique_output


def main(argv: list[str] | None = None) -> int:
    """Review automatic windows, refine if necessary, then replace the default map."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Saved HDF5 /images[frame,y,x] file.")
    parser.add_argument("--output", type=Path, help="Override persistent map path (replaced only on Save).")
    args = parser.parse_args(argv)
    try:
        print("Loading saved frames and detecting peaks...", flush=True)
        image, metadata = load_mean_snapshot(args.input)
        regions, detection = detect_harmonic_regions(image)
    except Exception as exc:
        print(f"[ERROR] Could not detect harmonics: {exc}", file=sys.stderr)
        return 1
    spectrum = image.sum(axis=0)
    width = image.shape[1]
    output = args.output or default_harmonic_map_path()
    figure, axis = plt.subplots(figsize=(12, 6))
    figure.subplots_adjust(bottom=0.27, top=0.84)
    status = figure.text(0.08, 0.91, "", fontsize=10)
    sensitivity_axis = figure.add_axes((0.18, 0.12, 0.45, 0.035))
    sensitivity = Slider(sensitivity_axis, "Peak sigma", 2.0, 12.0, valinit=5.0, valstep=0.5)
    save_axis = figure.add_axes((0.73, 0.07, 0.19, 0.065))
    save_button = Button(save_axis, "Confirm and save")
    order_axis = figure.add_axes((0.18, 0.045, 0.45, 0.04))
    orders = TextBox(order_axis, "Orders left to right ", initial="")
    save_error = False
    saved = False
    light_line, = axis.plot([], [], color="0.8", linewidth=1, label="Outside windows")
    dark_line, = axis.plot([], [], color="#102d50", linewidth=1.3, label="Selected harmonic signal")
    annotations = []

    def redraw(message="", preview=None):
        """Display raw y-integrated spectrum, with dark windows and pale gaps."""
        for annotation in annotations:
            annotation.remove()
        annotations.clear()
        shown = regions if preview is None else preview
        selected = np.zeros(width, dtype=bool)
        for region in shown:
            selected[region["x_start"]:region["x_stop"]] = True
        x = np.arange(width)
        light_line.set_data(x, spectrum)
        dark_line.set_data(x, np.where(selected, spectrum, np.nan))
        for region in shown:
            a, b = region["x_start"], region["x_stop"]
            annotations.append(axis.axvspan(a - 0.5, b - 0.5, color="#102d50", alpha=0.10))
            annotations.append(axis.text((a+b-1)/2, spectrum[a:b].max(), region["label"], ha="center", fontsize=9))
        axis.set(xlabel="Detector x pixel (P labels are peaks, not harmonic orders)",
                 ylabel="Mean image summed along y (ADU)", xlim=(-0.5, width-0.5))
        axis.legend(loc="upper right")
        axis.relim()
        axis.autoscale_view(scalex=False)
        status.set_text(f"{len(regions)} windows. {message}\n"
                        "Review weak peaks/tails. Drag to add/replace overlaps; right-click removes a window.\n"
                        "Slider re-detects all windows. Enter saves; Escape cancels and keeps the previous map.")
        figure.canvas.draw_idle()

    def redetect(value):
        nonlocal regions, detection
        regions, detection = detect_harmonic_regions(image, threshold_sigma=float(value))
        redraw("Automatic proposal; visual confirmation required.")

    def select(a, b):
        nonlocal regions
        start = max(0, min(width-1, int(np.floor(min(a,b)+0.5))))
        stop = max(start+1, min(width, int(np.floor(max(a,b)+0.5))+1))
        regions = [r for r in regions if r["x_stop"] <= start or r["x_start"] >= stop]
        regions.append({"label": "new", "x_start": start, "x_stop": stop})
        regions.sort(key=lambda r: r["x_start"])
        for index, region in enumerate(regions, 1):
            region["label"] = f"P{index}"
        detection["manually_edited"] = True
        redraw("Manual refinement applied.")

    def remove(event):
        nonlocal regions
        if event.inaxes == axis and event.button == 3 and event.xdata is not None:
            regions = [r for r in regions if not r["x_start"]-0.5 <= event.xdata < r["x_stop"]-0.5]
            for index, region in enumerate(regions, 1):
                region["label"] = f"P{index}"
            detection["manually_edited"] = True
            redraw("Window removed.")

    def save(event=None):
        nonlocal save_error, saved
        snapshot = None
        try:
            proposed = [dict(region) for region in regions]
            validate_harmonic_regions(proposed, width)
            entered = orders.text.strip()
            if entered:
                order_values = [float(value.strip()) for value in entered.split(",")]
                if len(order_values) != len(regions) or len(set(order_values)) != len(order_values):
                    raise ValueError("Enter one unique harmonic order per window, separated by commas.")
                for region, order in zip(proposed, order_values):
                    region["order"] = order
                    region["label"] = f"H{order:g}"
                validate_harmonic_regions(proposed, width)
            else:
                for region in proposed:
                    region.pop("order", None)
            redraw("Confirmed reference windows.", proposed)
            output.parent.mkdir(parents=True, exist_ok=True)
            snapshot = unique_output(output.with_name("harmonics_review.png"))
            figure.savefig(snapshot, dpi=160)
            save_harmonic_regions(output, proposed, args.input, image.shape,
                                  roi_bounds=metadata.get("roi_bounds"),
                                  annotated_snapshot_path=snapshot, overwrite=True,
                                  detection={**detection, "visually_confirmed": True})
            saved = True
            save_error = False
            print(f"Saved {len(regions)} windows as the persistent default: {output}", flush=True)
            plt.close(figure)
        except Exception as exc:
            save_error = True
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
            redraw(f"Cannot save: {exc}")

    def key(event):
        if event.inaxes == order_axis and event.key not in {"enter", "escape"}:
            return
        if event.key in {"enter", "s"}:
            save()
        elif event.key in {"escape", "q"}:
            plt.close(figure)

    selector = SpanSelector(axis, select, "horizontal", useblit=True, button=1)
    sensitivity.on_changed(redetect)
    save_button.on_clicked(save)
    figure.canvas.mpl_connect("button_press_event", remove)
    figure.canvas.mpl_connect("key_press_event", key)
    redraw("Automatic proposal; visual confirmation required.")
    plt.show()
    if not saved:
        print("No replacement saved; previous harmonic map retained.", flush=True)
    return 1 if save_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
