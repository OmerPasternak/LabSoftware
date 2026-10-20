"""Compatibility alias; implementation: hhg_control.camera.analysis.hdf5_io."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module("hhg_control.camera.analysis.hdf5_io")
