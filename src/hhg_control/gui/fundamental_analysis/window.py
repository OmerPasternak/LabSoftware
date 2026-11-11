"""Compatibility alias for the relocated HHG analysis window."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("hhg_control.hhg_analysis.window")
