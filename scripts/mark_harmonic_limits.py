"""Detect and review harmonic windows in saved data; never connects to hardware."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QToolBar, QPushButton, QInputDialog
import numpy as np

from hhg_control.analysis.spectral import (
    default_harmonic_map_path, detect_harmonic_regions, load_mean_snapshot,
    save_harmonic_regions, validate_harmonic_regions,
    calibrate_harmonic_orders,
)
from hhg_control.safe_io import unique_output
from hhg_control.analysis.plot_windows import show_analysis_figures


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
    figure.subplots_adjust(bottom=0.13, top=0.90)
    window = figure.canvas.manager.window
    window.setWindowTitle("Harmonics")
    controls = QToolBar("Harmonic windows", window)
    controls.setMovable(False)
    window.addToolBar(Qt.ToolBarArea.BottomToolBarArea, controls)
    save_button = QPushButton("Save")
    controls.addWidget(save_button)
    status = window.statusBar()
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
        shown = sorted(regions if preview is None else preview, key=lambda r: r['x_start'])
        selected = np.zeros(width, dtype=bool)
        for region in shown:
            selected[region["x_start"]:region["x_stop"]] = True
        x = np.arange(width)
        light_line.set_data(x, np.where(~selected, spectrum, np.nan))
        dark_line.set_data(x, np.where(selected, spectrum, np.nan))
        for region in shown:
            a, b = region["x_start"], region["x_stop"]
            # Peak index increases from right to left; never invent physical orders.
            label = f"P{len(shown)-shown.index(region)}"
            annotations.append(axis.text((a+b-1)/2, spectrum[a:b].max(), label, ha="center", fontsize=9))
        axis.set(xlabel="Pixel", ylabel="Signal (ADU)", xlim=(-0.5, width-0.5),
                 title=f"{len(shown)} peaks")
        axis.relim()
        axis.autoscale_view(scalex=False)
        status.showMessage(message or "Drag to add · Right-click to remove")
        figure.canvas.draw_idle()

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
        redraw()

    def remove(event):
        nonlocal regions
        if event.inaxes == axis and event.button == 3 and event.xdata is not None:
            regions = [r for r in regions if not r["x_start"]-0.5 <= event.xdata < r["x_stop"]-0.5]
            for index, region in enumerate(regions, 1):
                region["label"] = f"P{index}"
            detection["manually_edited"] = True
            redraw()

    def save(event=None):
        nonlocal save_error, saved
        snapshot = None
        try:
            proposed = [dict(region) for region in regions]
            validate_harmonic_regions(proposed, width)
            reference_label, accepted = QInputDialog.getItem(
                window, "Odd harmonic reference", "Choose one known odd harmonic peak:",
                [f"P{i}" for i in range(1, len(proposed)+1)], 0, False,
            )
            if not accepted:
                return
            odd_order, accepted = QInputDialog.getInt(
                window, "Odd harmonic reference", "Its odd harmonic order:", 11, 1, 999, 2,
            )
            if not accepted:
                return
            reference_index = int(reference_label[1:])
            proposed = calibrate_harmonic_orders(proposed, reference_index, odd_order)
            redraw("Confirmed reference windows.", proposed)
            output.parent.mkdir(parents=True, exist_ok=True)
            snapshot = unique_output(output.with_name("harmonics_review.png"))
            figure.savefig(snapshot, dpi=160)
            save_harmonic_regions(output, proposed, args.input, image.shape,
                                  roi_bounds=metadata.get("roi_bounds"),
                                  annotated_snapshot_path=snapshot, overwrite=True,
                                  detection={**detection, "visually_confirmed": True,
                                             "order_reference_peak_index": reference_index,
                                             "order_reference_odd_order": odd_order,
                                             "assumed_order_spacing_omega": .5,
                                             "assumes_complete_peak_sequence": True})
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
        if event.key in {"enter", "s"}:
            save()
        elif event.key in {"escape", "q"}:
            plt.close(figure)

    selector = SpanSelector(axis, select, "horizontal", useblit=True, button=1)
    save_button.clicked.connect(save)
    figure.canvas.mpl_connect("button_press_event", remove)
    figure.canvas.mpl_connect("key_press_event", key)
    redraw()
    show_analysis_figures()
    if not saved:
        print("No replacement saved; previous harmonic map retained.", flush=True)
    return 1 if save_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
