"""Compatibility alias; implementation: hhg_control.camera.drivers.pco_edge."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("hhg_control.camera.drivers.pco_edge")
