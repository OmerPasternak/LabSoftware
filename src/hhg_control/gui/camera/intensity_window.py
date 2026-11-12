"""Interactive rectangle selection from one saved camera frame."""

from __future__ import annotations

import numpy as np


def choose_intensity_bounds(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """Let the user drag one harmonic rectangle; return half-open pixel edges or cancel."""
    import matplotlib
    matplotlib.use("QtAgg")
    import matplotlib.pyplot as plt
    from matplotlib.widgets import RectangleSelector
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QPushButton, QToolBar
    from hhg_control.camera.analysis.plot_windows import show_analysis_figures

    height, width = image.shape
    figure, axis = plt.subplots(figsize=(11, 6), constrained_layout=True)
    shown = axis.imshow(image, cmap="gray", origin="upper", aspect="auto",
                        extent=(0, width, height, 0), interpolation="nearest")
    figure.colorbar(shown, ax=axis, label="Raw counts (ADU)")
    axis.set(xlabel="x pixel edge", ylabel="y pixel edge",
             title="Drag a rectangle around the full harmonic, then click Use this window")
    window = figure.canvas.manager.window
    window.setWindowTitle("Harmonic intensity window — first saved frame")
    toolbar = QToolBar("Window selection", window)
    toolbar.setMovable(False)
    window.addToolBar(Qt.ToolBarArea.BottomToolBarArea, toolbar)
    button = QPushButton("Use this window")
    button.setEnabled(False)
    toolbar.addWidget(button)
    selected = None
    accepted = False

    def select(first, last):
        nonlocal selected
        # Image edges are integer coordinates; include every touched pixel.
        x0 = max(0, int(np.floor(min(first.xdata, last.xdata))))
        x1 = min(width, int(np.ceil(max(first.xdata, last.xdata))))
        y0 = max(0, int(np.floor(min(first.ydata, last.ydata))))
        y1 = min(height, int(np.ceil(max(first.ydata, last.ydata))))
        selected = (x0, y0, x1, y1)
        button.setEnabled(x1 > x0 and y1 > y0)
        window.statusBar().showMessage(f"x [{x0}, {x1}), y [{y0}, {y1})")

    def accept(*_):
        nonlocal accepted
        if selected is not None and button.isEnabled():
            accepted = True
            plt.close(figure)

    selector = RectangleSelector(axis, select, button=[1], useblit=True,
                                 interactive=True, minspanx=1, minspany=1, spancoords="data")
    button.clicked.connect(accept)
    figure.canvas.mpl_connect("key_press_event", lambda event: accept() if event.key == "enter" else None)
    figure._intensity_selector = selector
    show_analysis_figures()
    return selected if accepted else None
