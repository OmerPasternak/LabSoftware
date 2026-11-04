"""Dedicated, offline analysis GUI, separate from camera acquisition controls."""

from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSettings, QStandardPaths
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hhg_control.camera.analysis.spectral import default_harmonic_map_path
from hhg_control.project_paths import project_root
from .matlab_bridge import companion_directory, matlab_arguments, matlab_candidates


HDF5_FILTER = "HDF5 camera scans (*.h5 *.hdf5)"


class FundamentalAnalysisWindow(QMainWindow):
    """Launch read-only analysis workflows for completed camera HDF5 files.

    The window deliberately owns only UI orchestration. Numerical methods stay
    in the existing analysis modules and scripts so future H1 analysis can be
    added without involving the live camera or scan sequencer.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fundamental Data Analysis")
        self.resize(820, 660)
        self._analysis_processes: list[QProcess] = []

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        heading = QLabel("Fundamental Data Analysis")
        heading.setStyleSheet("font-size: 19px; font-weight: 600;")
        root.addWidget(heading)

        intro = QLabel(
            "Analyze completed camera files without connecting to or changing the camera. "
            "Acquisition HDF5 files are read-only inputs; results are written separately."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #5b6570;")
        root.addWidget(intro)

        core_group = QGroupBox("Existing saved-data analysis")
        core_layout = QGridLayout(core_group)
        core_layout.setHorizontalSpacing(10)
        core_layout.setVerticalSpacing(8)

        self.btn_x_pearson = QPushButton("X Pearson")
        self.btn_x_pearson.setToolTip(
            "Use the supplied MATLAB spatial-mean Pearson workflow on a completed camera file."
        )
        self.btn_x_pearson.clicked.connect(self._choose_x_pearson_input)
        core_layout.addWidget(self.btn_x_pearson, 0, 0)

        self.btn_mark_harmonics = QPushButton("Mark Harmonics")
        self.btn_mark_harmonics.setToolTip(
            "Review harmonic windows from a saved frame and update the persistent map."
        )
        self.btn_mark_harmonics.clicked.connect(self._choose_harmonic_marker_input)
        core_layout.addWidget(self.btn_mark_harmonics, 0, 1)

        self.btn_harmonic_g2 = QPushButton("Harmonic g²")
        self.btn_harmonic_g2.setToolTip(
            "Calculate SI MCP-corrected g² from matched saved ON, OFF, and background files."
        )
        self.btn_harmonic_g2.clicked.connect(self._choose_harmonic_g2_inputs)
        core_layout.addWidget(self.btn_harmonic_g2, 0, 2)

        core_note = QLabel("Pearson uses MATLAB. Harmonic marking and corrected g² retain their existing workflows.")
        core_note.setWordWrap(True)
        core_layout.addWidget(core_note, 1, 0, 1, 3)
        root.addWidget(core_group)

        fundamental_group = QGroupBox("Fundamental / H1 analysis")
        fundamental_layout = QVBoxLayout(fundamental_group)
        self.lbl_fundamental_extension = QLabel(
            "H1 / fundamental and HHG workflows share the saved-data workspace below. "
            "Choose windows and calibration appropriate to the recorded signal."
        )
        self.lbl_fundamental_extension.setWordWrap(True)
        fundamental_layout.addWidget(self.lbl_fundamental_extension)
        root.addWidget(fundamental_group)

        matlab_group = QGroupBox("Lab MATLAB analysis — ShowScanGUI reference")
        matlab_layout = QVBoxLayout(matlab_group)
        description = QLabel(
            "Open the original scan analyzer with its existing controls, colors and orientation. "
            "The HHG Analysis menu adds HDF5 import and parameterized covariance, Pearson, "
            "variance, PCA and two-color views in the same MATLAB session."
        )
        description.setWordWrap(True)
        matlab_layout.addWidget(description)
        executable_row = QHBoxLayout()
        executable_row.addWidget(QLabel("MATLAB:"))
        self.txt_matlab_executable = QLineEdit()
        candidates = matlab_candidates()
        settings = QSettings("HHGControl", "Analysis")
        saved_matlab = str(settings.value("matlab_executable", ""))
        try:
            saved_matlab_exists = Path(saved_matlab).is_file()
        except OSError:
            saved_matlab_exists = False
        if not saved_matlab_exists:
            saved_matlab = str(candidates[0]) if candidates else ""
        self.txt_matlab_executable.setText(saved_matlab)
        self.txt_matlab_executable.setPlaceholderText("Choose matlab.exe to enable the companion")
        executable_row.addWidget(self.txt_matlab_executable, 1)
        self.btn_browse_matlab = QPushButton("Browse…")
        self.btn_browse_matlab.clicked.connect(self._choose_matlab_executable)
        executable_row.addWidget(self.btn_browse_matlab)
        matlab_layout.addLayout(executable_row)
        action_row = QHBoxLayout()
        self.btn_matlab_analysis = QPushButton("Open MATLAB Scan Analyzer + Workflows")
        self.btn_matlab_analysis.clicked.connect(self._launch_matlab_companion)
        action_row.addWidget(self.btn_matlab_analysis)
        self.btn_feature_guide = QPushButton("Feature guide / limitations")
        self.btn_feature_guide.clicked.connect(self._open_feature_guide)
        action_row.addWidget(self.btn_feature_guide)
        matlab_layout.addLayout(action_row)
        limitations = QLabel(
            "Integrated Fourier aggregation and the original helper-based calibration route are inactive. "
            "The guide lists required toolboxes and all remaining limitations. "
            "The companion remains open independently of this window."
        )
        limitations.setWordWrap(True)
        limitations.setStyleSheet("color: #805b16;")
        matlab_layout.addWidget(limitations)
        root.addWidget(matlab_group)

        self.txt_activity_log = QTextEdit()
        self.txt_activity_log.setReadOnly(True)
        self.txt_activity_log.setPlaceholderText("Analysis process messages will appear here.")
        self.txt_activity_log.setMaximumHeight(130)
        self.txt_activity_log.document().setMaximumBlockCount(2000)
        root.addWidget(self.txt_activity_log, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        close_row.addWidget(self.btn_close)
        root.addLayout(close_row)

        self.setCentralWidget(central)

    def _choose_matlab_executable(self) -> None:
        """Choose a MATLAB release without changing the lab Python environment."""
        filepath, _ = QFileDialog.getOpenFileName(self, "Choose MATLAB executable", "", "MATLAB (matlab.exe);;All files (*)")
        if filepath:
            self.txt_matlab_executable.setText(filepath)

    def _launch_matlab_companion(self, _checked=False, *, input_path: Path | None = None, action: str | None = None) -> None:
        """Start a separate offline MATLAB desktop; closing Qt cannot terminate it."""
        executable = Path(self.txt_matlab_executable.text().strip())
        try:
            executable_exists = executable.is_file()
        except OSError:
            executable_exists = False
        if not executable_exists:
            QMessageBox.warning(self, "MATLAB Missing", "Choose an installed MATLAB executable first.")
            return
        settings = QSettings("HHGControl", "Analysis")
        settings.setValue("matlab_executable", str(executable))
        log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "analysis_logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"matlab_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
            started, _pid = QProcess.startDetached(str(executable), matlab_arguments(log_path, input_path, action), str(companion_directory()))
        except OSError as exc:
            QMessageBox.warning(self, "MATLAB Launch Failed", str(exc))
            return
        if not started:
            QMessageBox.warning(self, "MATLAB Launch Failed", f"Could not start {executable}.")
            return
        self._append_log(f"MATLAB companion starting. Startup/runtime log: {log_path}")

    def _open_feature_guide(self) -> None:
        """Open the packaged feature and scientific-method limitations guide."""
        path = Path(__file__).resolve().parent / "docs" / "feature_guide.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _default_directory(self) -> str:
        """Return an existing project data directory for file pickers."""
        try:
            root = project_root()
        except RuntimeError:
            return str(Path.home())
        data_dir = root / "data"
        return str(data_dir if data_dir.is_dir() else root)

    def _choose_x_pearson_input(self) -> None:
        """Choose a completed scan-step file and launch X Pearson analysis."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Choose a completed scan-step HDF5 file", self._default_directory(), HDF5_FILTER
        )
        if filepath:
            self._launch_matlab_companion(input_path=Path(filepath), action="pearson")

    def _choose_harmonic_marker_input(self) -> None:
        """Choose a completed scan-step file for harmonic-window review."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Choose a completed scan-step HDF5 file", self._default_directory(), HDF5_FILTER
        )
        if filepath:
            self._launch_analysis_script("mark_harmonic_limits.py", Path(filepath))

    def _choose_harmonic_g2_inputs(self) -> None:
        """Choose matched ON/OFF/background files and launch SI harmonic g²."""
        map_path = default_harmonic_map_path()
        if not map_path.is_file():
            QMessageBox.warning(
                self,
                "Harmonic Map Missing",
                "Run Mark Harmonics and save a validated map before calculating harmonic g².",
            )
            return

        selected: list[str] = []
        for label in ("BSV ON", "BSV OFF (coherent only)", "BACKGROUND (no XUV)"):
            filepath, _ = QFileDialog.getOpenFileName(
                self,
                f"Choose {label} saved HDF5 file",
                self._default_directory(),
                HDF5_FILTER,
            )
            if not filepath:
                return
            selected.append(filepath)

        self._launch_analysis_script(
            "analyze_harmonic_g2.py",
            Path(selected[0]),
            extra_args=["--bsv-off", selected[1], "--background", selected[2]],
        )

    def _launch_analysis_script(
        self, script_name: str, input_path: Path, extra_args: list[str] | None = None
    ) -> None:
        """Run a saved-data analysis script asynchronously and capture its output."""
        script_path = Path(__file__).resolve().parent / script_name
        if not script_path.is_file():
            try:
                script_path = project_root() / "scripts" / script_name
            except RuntimeError:
                pass
        if not script_path.is_file():
            QMessageBox.warning(self, "Analysis Tool Missing", f"Could not find analysis script:\n{script_path}")
            return

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments([str(script_path), str(input_path), *(extra_args or [])])
        process.setWorkingDirectory(str(script_path.parent))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        try:
            environment.insert("PYTHONPATH", str(project_root() / "src"))
        except RuntimeError:
            pass  # Installed package imports normally; resources are bundled.
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda proc=process: self._read_analysis_output(proc))
        process.finished.connect(
            lambda exit_code, _status, proc=process, name=script_name:
            self._on_analysis_finished(proc, name, exit_code)
        )
        process.errorOccurred.connect(
            lambda error, proc=process, name=script_name:
            self._on_analysis_process_error(proc, name, error)
        )
        self._analysis_processes.append(process)
        self._append_log(f"Starting {script_name} for {input_path}.")
        process.start()

    def _read_analysis_output(self, process: QProcess) -> None:
        """Append one analysis process's output to the window log."""
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if output:
            for line in output.splitlines():
                self._append_log(line)

    def _on_analysis_finished(self, process: QProcess, script_name: str, exit_code: int) -> None:
        """Record completion and release the process wrapper."""
        self._read_analysis_output(process)
        status = "completed" if exit_code == 0 else f"failed (exit code {exit_code})"
        self._append_log(f"{script_name} {status}.")
        if process in self._analysis_processes:
            self._analysis_processes.remove(process)
        process.deleteLater()

    def _on_analysis_process_error(self, process: QProcess, script_name: str, error) -> None:
        """Report process launch errors and release a process that never started."""
        self._append_log(f"{script_name} error: {process.errorString()} ({error}).")
        if error == QProcess.ProcessError.FailedToStart:
            if process in self._analysis_processes:
                self._analysis_processes.remove(process)
            process.deleteLater()

    def _append_log(self, message: str) -> None:
        """Append a timestamp-free child-process message to the activity log."""
        self.txt_activity_log.insertPlainText(message + "\n")

    def closeEvent(self, event) -> None:
        """Keep active analysis processes alive until their windows have finished."""
        if self._analysis_processes:
            self._append_log("Analysis is still open. Close its plot windows before closing this launcher.")
            event.ignore()
            return
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    """Launch the standalone analysis application."""
    del argv  # Reserved for future UI options; scripts receive their own arguments.
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv[:1])
    window = FundamentalAnalysisWindow()
    window.show()
    if not owns_app:
        return 0
    return app.exec()
