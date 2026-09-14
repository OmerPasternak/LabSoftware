"""
User Interface (UI) Layer for HHG Instrument Control.
Provides per-instrument panels and orchestration windows.
"""

from .camera import CameraMainWindow, CameraPanel

__all__ = ["CameraMainWindow", "CameraPanel"]
