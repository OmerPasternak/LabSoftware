"""
Concrete driver for the Excelitas pco.edge 5.5 USB camera.
Wraps the official `pco` Python SDK (pco.Camera).
"""

import time
from datetime import datetime
from typing import Tuple, List, Dict, Any
import numpy as np
from .base_camera import BaseCamera

try:
    import pco
    PCO_AVAILABLE = True
except (ImportError, RuntimeError) as err:
    PCO_AVAILABLE = False
    PCO_IMPORT_ERROR = str(err)


class PcoEdgeCamera(BaseCamera):
    """Driver for pco.edge 5.5 sCMOS using the official Excelitas `pco` SDK."""

    def __init__(self, interface: str = "USB 3.0") -> None:
        super().__init__()
        if not PCO_AVAILABLE:
            raise ImportError(
                f"The official 'pco' package or its required C runtime DLLs are missing: {PCO_IMPORT_ERROR}. "
                "Ensure 'pip install pco' is executed and PCO CamWare / USB drivers are installed."
            )
        self._interface_name = interface
        self._cam: Any = None
        self._info: Dict[str, Any] = {}

    def connect(self) -> None:
        if self._is_connected:
            return
        try:
            self._cam = pco.Camera(interface=self._interface_name)
            description = getattr(self._cam, "description", {})
            self._info = {
                "model": getattr(self._cam, "camera_name", "pco.edge"),
                "serial_number": str(getattr(self._cam, "camera_serial", "UNKNOWN")),
                "interface": self._interface_name,
                "width": description.get("max_width", 2560),
                "height": description.get("max_height", 2160),
                "bit_depth": 16
            }
            self.set_exposure_time(self._exposure_time_s)
            self._is_connected = True
        except Exception as exc:
            self._cam = None
            self._is_connected = False
            raise ConnectionError(f"Failed to connect to PCO camera: {exc}") from exc

    def close(self) -> None:
        if self._cam is not None:
            try:
                if getattr(self._cam, "is_recording", False):
                    self._cam.stop()
                self._cam.close()
            except Exception:
                pass
            finally:
                self._cam = None
                self._is_connected = False

    def set_exposure_time(self, exposure_s: float) -> None:
        self.validate_exposure_time(exposure_s)
        if self._cam is not None:
            self._cam.set_exposure_time(exposure_s)
        self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        if self._cam is not None and hasattr(self._cam, "exposure_time"):
            return float(self._cam.exposure_time)
        return self._exposure_time_s

    def get_sensor_info(self) -> Dict[str, Any]:
        return self._info

    def get_roi_limits(self) -> Dict[str, Any]:
        """Read hardware ROI steps, minimum dimensions, and symmetry from pco SDK."""
        if self._cam is not None and hasattr(self._cam, "description"):
            desc = getattr(self._cam, "description", {})
            return {
                "steps": desc.get("roi steps", (4, 1)),
                "minimum": (desc.get("min width", 64), desc.get("min height", 16)),
                "symmetric": (desc.get("roi is horz symmetric", False), desc.get("roi is vert symmetric", True))
            }
        return {"steps": (4, 1), "minimum": (64, 16), "symmetric": (False, True)}

    def get_roi(self) -> tuple[int, int, int, int]:
        """Translate SDK one-based inclusive ROI (x0, y0, x1, y1) to zero-based exclusive bounds."""
        if self._cam is not None and hasattr(self._cam, "configuration"):
            cfg = self._cam.configuration
            if "roi" in cfg and cfg["roi"]:
                x0, y0, x1, y1 = cfg["roi"]
                return (x0 - 1, y0 - 1, x1, y1)
        return getattr(self, "_roi", (0, 0, 2560, 2160))

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Set hardware readout ROI on camera sensor."""
        self.validate_roi(roi)
        if self._cam is not None:
            if getattr(self._cam, "is_recording", False):
                self._cam.stop()
            x0, y0, x1, y1 = roi
            # PCO SDK expects 1-based (x0+1, y0+1, x1, y1)
            self._cam.configuration = {"roi": (x0 + 1, y0 + 1, x1, y1)}
        self._roi = tuple(roi)

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        if not self._is_connected or self._cam is None:
            raise RuntimeError("Camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        self._cam.record(number_of_images=num_frames, mode="sequence")
        raw_images, metadata_list = self._cam.images()
        images_array = np.ascontiguousarray(np.stack(raw_images, axis=0), dtype=np.uint16)
        
        metas = []
        for i, meta in enumerate(metadata_list):
            cam_time = None
            if isinstance(meta, dict):
                cam_time = meta.get("timestamp")
            if not cam_time:
                cam_time = time.time()
            metas.append({
                "frame_id": i,
                "camera_timestamp": cam_time,
                "camera_time_str": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "raw_meta": str(meta),
                "exposure_s": self._exposure_time_s,
                "roi": self.get_roi()
            })

        return images_array, metas


