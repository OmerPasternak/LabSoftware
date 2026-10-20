"""Compatibility alias; implementation: hhg_control.gui.camera.camera_panel."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("hhg_control.gui.camera.camera_panel")
