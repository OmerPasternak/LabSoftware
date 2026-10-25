"""Save and pop up two Python plots of completed camera-delay characterization."""

import argparse
import json
from pathlib import Path


def make_figure(report):
    """Plot actual camera delays in ns against raw-window mean ADU ± sample STDEV."""
    from matplotlib.figure import Figure
    if not report.get("complete") or len(report["points"]) != 15:
        raise ValueError("A completed 15-point characterization is required.")
    figure = Figure(figsize=(11, 5), constrained_layout=True)
    axes = figure.subplots(1, 2, sharey=True)
    for axis, points, limits in zip(axes, (report["points"][:5], report["points"][5:]),
                                   ((0, 100), (1900, 2100))):
        axis.errorbar([p["actual_delay_ns"] for p in points],
                      [p["average_adu"] for p in points],
                      yerr=[p["stdev_adu"] for p in points], fmt="o-", capsize=4)
        axis.set(xlabel="Camera delay (ns)", title=f"{limits[0]}–{limits[1]} ns")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Average integrated intensity (ADU/image)")
    simulated = "MOCK" in str(report.get("camera", {}).get("serial_number", ""))
    figure.suptitle(f"{'SIMULATED — ' if simulated else ''}Camera delay characterization\n"
                   f"{report['frames_per_point']} frames/delay; error bars: sample STDEV")
    return figure


def main(argv=None):
    """Read the separate JSON report, save PNG, and raise a normal Qt plot window."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    import matplotlib
    matplotlib.use("QtAgg")
    import matplotlib.pyplot as plt
    from hhg_control.camera.analysis.plot_windows import show_analysis_figures
    with args.report.open(encoding="utf-8") as handle:
        report = json.load(handle)
    figure = make_figure(report)
    # Register the prepared Figure with a standard interactive Qt manager.
    from matplotlib.backends.backend_qtagg import new_figure_manager_given_figure
    from matplotlib._pylab_helpers import Gcf
    manager = new_figure_manager_given_figure(1, figure)
    Gcf._set_new_active_manager(manager)
    target = args.report.with_suffix(".png")
    with target.open("xb") as handle:
        figure.savefig(handle, format="png", dpi=150)
    print(f"Plot saved: {target}", flush=True)
    show_analysis_figures()
    plt.close(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
