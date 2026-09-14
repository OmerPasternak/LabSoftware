"""
Backward-compatibility runner for Camera GUI.
Redirects to the modular hhg_control.ui.camera package.
"""

from hhg_control.ui.camera.camera_panel import (
    CameraMainWindow,
    PreviewTask,
    ScanSequenceTask,
    main,
)

__all__ = ["CameraMainWindow", "PreviewTask", "ScanSequenceTask", "main"]

if __name__ == "__main__":
    main()
