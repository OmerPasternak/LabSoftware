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


def test_preserved_matlab_package_matches_original_manifest():
    directory = matlab_bridge.companion_directory()
    manifest = json.loads((directory / "source_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest.items():
        content = (directory / "reference" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], name
    assert not manifest["ShowScanGUI_App.mlapp"].get("contains_executable_source", False)
    assert (directory.parent / "INACTIVE_FEATURES.txt").is_file()
