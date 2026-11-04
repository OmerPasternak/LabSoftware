"""Layout and launcher tests for the standalone saved-data analysis GUI."""

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

pytest.importorskip("pytestqt")

from hhg_control.gui.fundamental_analysis import FundamentalAnalysisWindow
import hhg_control.gui.fundamental_analysis.window as analysis_window


def test_fundamental_analysis_window_has_separate_analysis_actions(qtbot):
    window = FundamentalAnalysisWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "Fundamental Data Analysis"
    assert window.btn_x_pearson.text() == "X Pearson"
    assert window.btn_mark_harmonics.text() == "Mark Harmonics"
    assert window.btn_harmonic_g2.text() == "Harmonic g²"
    assert "H1" in window.lbl_fundamental_extension.text()
    assert not hasattr(window, "camera")
    window.close()


def test_x_pearson_button_uses_authoritative_matlab_workflow(qtbot, monkeypatch):
    window = FundamentalAnalysisWindow()
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(
        analysis_window.QFileDialog,
        "getOpenFileName",
        lambda *args: ("capture.h5", "HDF5 camera scans (*.h5 *.hdf5)"),
    )
    monkeypatch.setattr(window, "_launch_matlab_companion", lambda *args, **kwargs: calls.append((args, kwargs)))

    window.btn_x_pearson.click()

    assert calls == [((), {"input_path": Path("capture.h5"), "action": "pearson"})]
    window.close()


def test_harmonic_g2_uses_persistent_map_and_matched_reference_files(qtbot, monkeypatch, tmp_path):
    window = FundamentalAnalysisWindow()
    qtbot.addWidget(window)
    map_path = tmp_path / "harmonics.json"
    map_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(analysis_window, "default_harmonic_map_path", lambda: map_path)
    choices = iter(("on.h5", "off.h5", "background.h5"))
    titles = []

    def choose(_parent, title, *_args):
        titles.append(title)
        return next(choices), ""

    monkeypatch.setattr(analysis_window.QFileDialog, "getOpenFileName", choose)
    calls = []
    monkeypatch.setattr(window, "_launch_analysis_script", lambda *args, **kwargs: calls.append((args, kwargs)))

    window.btn_harmonic_g2.click()

    assert len(titles) == 3
    assert calls[0][0] == ("analyze_harmonic_g2.py", Path("on.h5"))
    assert calls[0][1]["extra_args"] == ["--bsv-off", "off.h5", "--background", "background.h5"]
    window.close()


def test_harmonic_g2_requires_the_persistent_map(qtbot, monkeypatch, tmp_path):
    window = FundamentalAnalysisWindow()
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(analysis_window, "default_harmonic_map_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    window.btn_harmonic_g2.click()

    assert warnings and "Mark Harmonics" in warnings[0]
    window.close()
