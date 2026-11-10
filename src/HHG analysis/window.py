"""Launch the sole ShowScanGUI interface; no Python analysis window."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import QProcess, QSettings, QStandardPaths
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from .matlab_bridge import matlab_candidates, matlab_arguments


def main(argv=None):
    """Open ShowScanGUI directly; MATLAB stays minimized until workspace requested."""
    app = QApplication.instance() or QApplication([])
    settings = QSettings("HHGControl", "Analysis")
    saved = str(settings.value("matlab_executable", ""))
    try:
        executable = Path(saved) if saved and Path(saved).is_file() else None
    except OSError:
        executable = None
    if executable is None:
        candidates = matlab_candidates()
        executable = candidates[0] if candidates else None
    if executable is None:
        selected, _ = QFileDialog.getOpenFileName(None, "Locate MATLAB", "", "MATLAB (matlab.exe)")
        if not selected:
            return 1
        executable = Path(selected)
    settings.setValue("matlab_executable", str(executable))
    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "analysis_logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"analysis_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        started, _pid = QProcess.startDetached(str(executable), matlab_arguments(log_path))
        if not started:
            raise OSError("Could not start the analysis interface.")
    except OSError as exc:
        QMessageBox.critical(None, "Analysis startup", str(exc))
        return 1
    return 0


class FundamentalAnalysisWindow:
    """Nonvisual compatibility entry point; show launches the sole analyzer."""
    def show(self):
        return main()
