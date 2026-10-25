"""Save and pop up two Python plots of completed camera-delay characterization."""

import argparse
import json
import os
from pathlib import Path

from hhg_control.safe_io import filesystem_path


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


def delete_gathered_data(report_path: Path, report: dict) -> list[Path]:
    """Delete only this completed diagnostic's raw files and sidecars after its PNG is saved.

    Keep the PNG chart. Validate every target and its run-specific name before
    deleting anything; remove the report last so it remains if cleanup fails.
    """
    report_path = Path(report_path)
    if report.get("kind") != "camera_delay_characterization" or not report.get("complete"):
        raise ValueError("Refusing cleanup for an incomplete or unrelated report.")
    resolved_report = Path(os.path.abspath(report_path))
    if (resolved_report.name != report_path.name or resolved_report.suffix != ".json"
            or not resolved_report.stem.startswith("camera_delay_")):
        raise ValueError("Report path does not identify a camera-delay run.")
    if not filesystem_path(resolved_report).is_file() or filesystem_path(resolved_report).is_symlink():
        raise ValueError("Report file is missing or is a symbolic link.")
    run_prefix = f"Camera_delay_{resolved_report.stem.removeprefix('camera_delay_')}_"
    directory = resolved_report.parent
    candidates = [resolved_report, directory / f"{resolved_report.stem}_window.json"]
    reference = report.get("window", {}).get("reference_file")
    if not reference:
        raise ValueError("Report does not identify its saved reference frame.")
    candidates.append(Path(reference))
    for point in report.get("points", []):
        candidates.extend(Path(source) for source in point.get("source_files", []))
    gathered, seen = [], set()
    for candidate in candidates:
        resolved = Path(os.path.abspath(candidate))
        if (resolved.parent != directory or resolved in seen
                or resolved == resolved_report.with_suffix(".png")):
            raise ValueError(f"Refusing cleanup outside this run's output files: {candidate}")
        if filesystem_path(resolved).is_symlink() or not filesystem_path(resolved).is_file():
            raise ValueError(f"Gathered-data file is missing or is a symbolic link: {resolved}")
        if resolved == resolved_report:
            pass
        elif resolved.name.endswith("_window.json"):
            if resolved.name != f"{resolved_report.stem}_window.json":
                raise ValueError(f"Unexpected selection file for this run: {resolved}")
        elif resolved.suffix == ".h5" and resolved.name.startswith(run_prefix):
            pass
        else:
            raise ValueError(f"Unexpected gathered-data file for this run: {resolved}")
        seen.add(resolved)
        gathered.append(resolved)
    raw_files = [path for path in gathered if path.suffix == ".h5"]
    if len(raw_files) != 16 or len(gathered) != 18:
        raise ValueError("Expected one reference, 15 delay files, a window, and a report before cleanup.")
    for path in raw_files:
        filesystem_path(path).unlink()
    selection = directory / f"{resolved_report.stem}_window.json"
    filesystem_path(selection).unlink()
    filesystem_path(resolved_report).unlink()
    return raw_files + [selection, resolved_report]


def main(argv=None):
    """Save the Python graph, clean up a completed run's gathered data, and show the plot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    import matplotlib
    matplotlib.use("QtAgg")
    import matplotlib.pyplot as plt
    from hhg_control.camera.analysis.plot_windows import show_analysis_figures
    with filesystem_path(args.report).open(encoding="utf-8") as handle:
        report = json.load(handle)
    figure = make_figure(report)
    # Register the prepared Figure with a standard interactive Qt manager.
    from matplotlib.backends.backend_qtagg import new_figure_manager_given_figure
    from matplotlib._pylab_helpers import Gcf
    manager = new_figure_manager_given_figure(1, figure)
    Gcf._set_new_active_manager(manager)
    target = args.report.with_suffix(".png")
    with filesystem_path(target).open("xb") as handle:
        figure.savefig(handle, format="png", dpi=150)
    print(f"Plot saved: {target}", flush=True)
    try:
        removed = delete_gathered_data(args.report, report)
        print(f"Removed {len(removed)} gathered-data files after saving the chart.", flush=True)
    except Exception as exc:
        print(f"[CLEANUP ERROR] Plot saved, but gathered-data cleanup failed: {exc}", flush=True)
    show_analysis_figures()
    plt.close(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
