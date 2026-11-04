"""Dedicated, offline analysis GUI, separate from camera acquisition controls."""

from __future__ import annotations

from pathlib import Path
import sys

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hhg_control.camera.analysis.spectral import default_harmonic_map_path
from hhg_control.project_paths import project_root


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
        self.resize(760, 440)
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
            "Calculate x-pixel Pearson correlations from one completed scan file."
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

        core_layout.addWidget(
            QLabel("These actions reuse the existing analysis scripts and saved-data contracts."),
            1,
            0,
            1,
            3,
        )
        root.addWidget(core_group)

        fundamental_group = QGroupBox("Fundamental / H1 analysis")
        fundamental_layout = QVBoxLayout(fundamental_group)
        self.lbl_fundamental_extension = QLabel(
            "Dedicated H1 analysis tools can be added here without changing camera acquisition."
        )
        self.lbl_fundamental_extension.setWordWrap(True)
        fundamental_layout.addWidget(self.lbl_fundamental_extension)
        root.addWidget(fundamental_group)

        self.txt_activity_log = QTextEdit()
        self.txt_activity_log.setReadOnly(True)
        self.txt_activity_log.setPlaceholderText("Analysis process messages will appear here.")
        self.txt_activity_log.setMaximumHeight(130)
        root.addWidget(self.txt_activity_log, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        close_row.addWidget(self.btn_close)
        root.addLayout(close_row)

        self.setCentralWidget(central)

    def _default_directory(self) -> str:
        """Return an existing project data directory for file pickers."""
        data_dir = project_root() / "data"
        return str(data_dir if data_dir.is_dir() else project_root())

    def _choose_x_pearson_input(self) -> None:
        """Choose a completed scan-step file and launch X Pearson analysis."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Choose a completed scan-step HDF5 file", self._default_directory(), HDF5_FILTER
        )
        if filepath:
            self._launch_analysis_script("analyze_spectral_covariance.py", Path(filepath))

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
        script_path = project_root() / "scripts" / script_name
        if not script_path.is_file():
            QMessageBox.warning(self, "Analysis Tool Missing", f"Could not find analysis script:\n{script_path}")
            return

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments([str(script_path), str(input_path), *(extra_args or [])])
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
        self.txt_activity_log.append(message)


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
