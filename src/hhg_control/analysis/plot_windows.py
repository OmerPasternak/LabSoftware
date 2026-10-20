"""Display completed analysis figures in ordinary Python/Qt plot windows."""

import matplotlib.pyplot as plt


def show_analysis_figures() -> None:
    """Raise all completed result windows and keep the child process alive.

    Called only by offline analysis scripts after saving results. Acquisition
    stays in the main application and is not blocked by these plot windows.
    """
    for number in plt.get_fignums():
        manager = plt.figure(number).canvas.manager
        window = getattr(manager, "window", None)
        if window is not None:
            window.show()
            window.raise_()
            window.activateWindow()
    plt.show(block=True)
