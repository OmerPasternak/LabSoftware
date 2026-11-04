"""Relocation, offline startup and MATLAB boundary regression checks."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

from PyQt6.QtCore import QProcess

from hhg_control.hhg_analysis import matlab_bridge
from hhg_control.hhg_analysis import window as analysis


def test_relocated_gui_preserves_old_module_and_class_identity():
    old = importlib.import_module("hhg_control.gui.fundamental_analysis.window")
    assert old is analysis
    assert "HHG analysis" in str(Path(analysis.__file__))


def test_standalone_module_starts_without_loading_camera_driver(tmp_path):
    code = """
import runpy, sys
from PyQt6.QtCore import QTimer
from hhg_control.hhg_analysis import window
original = window.QApplication
class AutoCloseApp(original):
    def __init__(self, *args):
        super().__init__(*args)
        QTimer.singleShot(150, self.quit)
window.QApplication = AutoCloseApp
assert not any('drivers.pco_edge' in name for name in sys.modules)
runpy.run_module('hhg_control.hhg_analysis', run_name='__main__')
"""
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    source = Path(__file__).resolve().parents[1] / "src"
    environment["PYTHONPATH"] = str(source)
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=environment,
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_matlab_detaches_and_quotes_source_and_input_paths(qtbot, monkeypatch, tmp_path):
    class MemorySettings:
        def __init__(self, *_):
            pass

        def value(self, _key, default):
            return default

        def setValue(self, *_):
            pass

    monkeypatch.setattr(analysis, "QSettings", MemorySettings)
    window = analysis.FundamentalAnalysisWindow()
    qtbot.addWidget(window)
    executable = tmp_path / "matlab.exe"
    executable.touch()
    window.txt_matlab_executable.setText(str(executable))
    monkeypatch.setattr(analysis.QStandardPaths, "writableLocation", lambda *_: str(tmp_path))
    monkeypatch.setattr(matlab_bridge, "companion_directory", lambda: tmp_path / "O'Brien resources")
    calls = []
    monkeypatch.setattr(QProcess, "startDetached", lambda *args: (calls.append(args) or (True, 123)))
    window._launch_matlab_companion(input_path=tmp_path / "O'Brien scan.h5", action="pearson")
    assert len(calls) == 1
    arguments = calls[0][1]
    assert arguments[0] == "-desktop" and arguments[1] == "-logfile"
    assert "O''Brien resources" in arguments[-1]
    assert "O''Brien scan.h5" in arguments[-1]
    assert "'pearson'" in arguments[-1]
    window._append_log("<b>literal process output</b>")
    assert "<b>literal process output</b>" in window.txt_activity_log.toPlainText()
    window.close()


def test_close_keeps_active_analysis_alive(qtbot):
    window = analysis.FundamentalAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    process = QProcess(window)
    window._analysis_processes.append(process)
    assert not window.close()
    assert window.isVisible()
    window._analysis_processes.remove(process)
    assert window.close()


def test_preserved_matlab_package_matches_original_manifest():
    directory = matlab_bridge.companion_directory()
    manifest = json.loads((directory / "source_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest.items():
        content = (directory / "reference" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], name
    assert not manifest["ShowScanGUI_App.mlapp"].get("contains_executable_source", False)
    assert (directory.parent / "INACTIVE_FEATURES.txt").is_file()
