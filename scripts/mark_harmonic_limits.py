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
from PyQt6.QtWidgets import QApplication, QToolBar, QLabel, QDoubleSpinBox, QLineEdit, QPushButton
import numpy as np

from hhg_control.analysis.spectral import (
    default_harmonic_map_path, detect_harmonic_regions, load_mean_snapshot,
    save_harmonic_regions, validate_harmonic_regions,
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
    controls.addWidget(QLabel("  Threshold "))
    sensitivity = QDoubleSpinBox()
    sensitivity.setRange(2.0, 12.0)
    sensitivity.setSingleStep(0.5)
    sensitivity.setValue(5.0)
    sensitivity.setToolTip("Lower to detect weaker peaks. Replaces manual edits.")
    controls.addWidget(sensitivity)
    controls.addSeparator()
    controls.addWidget(QLabel(" Orders "))
    orders = QLineEdit()
    orders.setPlaceholderText("Left to right: 11, 11.5, 12, ...")
    orders.setMinimumWidth(260)
    controls.addWidget(orders)
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
        axis.set(xlabel="Pixel", ylabel="Signal (ADU)", xlim=(-0.5, width-0.5),
                 title=f"{len(shown)} peaks")
        axis.relim()
        axis.autoscale_view(scalex=False)
        status.showMessage(message or "Drag to add · Right-click to remove")
        figure.canvas.draw_idle()

    def redetect(value):
        nonlocal regions, detection
        regions, detection = detect_harmonic_regions(image, threshold_sigma=float(value))
        redraw()

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
            entered = orders.text().strip()
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
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        if event.key in {"enter", "s"}:
            save()
        elif event.key in {"escape", "q"}:
            plt.close(figure)

    selector = SpanSelector(axis, select, "horizontal", useblit=True, button=1)
    sensitivity.valueChanged.connect(redetect)
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
