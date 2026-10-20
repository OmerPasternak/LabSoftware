"""Compatibility alias; implementation: hhg_control.camera.analysis.plot_windows."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("hhg_control.camera.analysis.plot_windows")
