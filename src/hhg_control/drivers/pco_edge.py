"""
Concrete driver for the Excelitas pco.edge 5.5 USB camera.
Wraps the official `pco` Python SDK (pco.Camera).
"""

from typing import Tuple, List, Dict, Any
import numpy as np
import time
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
        """Open USB camera, check sensor geometry, and apply validated exposure in seconds."""
        if self._is_connected:
            return
        try:
            self._cam = pco.Camera(interface=self._interface_name)
            description = getattr(self._cam, "description", {})
            self._info = {
                "model": getattr(self._cam, "camera_name", "pco.edge"),
                "serial_number": str(getattr(self._cam, "camera_serial", "UNKNOWN")),
                "interface": self._interface_name,
                "width": description["max width"],
                "height": description["max height"],
                "bit_depth": 16
            }
            if (self._info["width"], self._info["height"], description["bit resolution"]) != (2560, 2160, 16):
                raise ValueError("Expected a 2560 x 2160, 16-bit pco.edge 5.5 camera.")
            if tuple(self._cam.configuration["binning"][:2]) != (1, 1):
                raise ValueError("Camera must use unbinned sensor pixels.")
            self.set_exposure_time(self._exposure_time_s)
            self._is_connected = True
        except Exception as exc:
            if self._cam is not None:
                self._cam.close()
            self._cam = None
            self._is_connected = False
            raise ConnectionError(f"Failed to connect to PCO camera: {exc}") from exc

    def close(self) -> None:
        """Stop recording and release the camera handle; call only after workers finish."""
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
        """Apply seconds within both approved lab limits and current hardware limits."""
        self.validate_exposure_time(exposure_s)
        if self._cam is not None:
            description = self._cam.description
            if not description["min exposure time"] <= exposure_s <= description["max exposure time"]:
                raise ValueError("Exposure exceeds the camera limits for its current mode.")
            self._cam.exposure_time = exposure_s
        self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        """Return the camera exposure duration in seconds."""
        if self._cam is not None and hasattr(self._cam, "exposure_time"):
            return float(self._cam.exposure_time)
        return self._exposure_time_s

    def get_sensor_info(self) -> Dict[str, Any]:
        """Return full sensor dimensions in pixels and identity metadata."""
        return dict(self._info)

    def get_roi_limits(self) -> Dict[str, Any]:
        """Read current hardware ROI steps, minimum dimensions, and symmetry."""
        if self._cam is None:
            raise RuntimeError("Camera is not connected.")
        desc = self._cam.description
        return {"steps": desc["roi steps"],
                "minimum": (desc["min width"], desc["min height"]),
                "symmetric": (desc["roi is horz symmetric"], desc["roi is vert symmetric"])}

    def get_roi(self) -> tuple[int, int, int, int]:
        """Translate SDK one-based inclusive ROI to zero-based exclusive bounds."""
        if self._cam is None:
            raise RuntimeError("Camera is not connected.")
        x0, y0, x1, y1 = self._cam.configuration["roi"]
        return (x0 - 1, y0 - 1, x1, y1)

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Set hardware readout ROI while idle and verify the camera accepted it."""
        self.validate_roi(roi)
        if self._cam.is_recording:
            raise RuntimeError("Stop acquisition before changing ROI.")
        x0, y0, x1, y1 = roi
        self._cam.configuration = {"roi": (x0 + 1, y0 + 1, x1, y1)}
        if self.get_roi() != tuple(roi):
            raise RuntimeError(f"Camera applied a different ROI: {self.get_roi()}")

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Acquire uint16 ROI frames with a bounded wait; always stop recording on exit."""
        if not self._is_connected or self._cam is None:
            raise RuntimeError("Camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        try:
            self._cam.record(number_of_images=num_frames, mode="sequence non blocking")
            deadline = time.monotonic() + num_frames * (self.get_exposure_time() + 1.0) + 5.0
            while self._cam.recorded_image_count < num_frames:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Camera acquisition timed out waiting for frames.")
                time.sleep(0.01)
            raw_images, metadata_list = self._cam.images()
        finally:
            self._cam.stop()
        received_at = time.time()
        images_array = np.ascontiguousarray(np.stack(raw_images, axis=0), dtype=np.uint16)
        
        x0, y0, x1, y1 = self.get_roi()
        if images_array.shape != (num_frames, y1 - y0, x1 - x0) or len(metadata_list) != num_frames:
            raise RuntimeError("Camera returned unexpected frame dimensions or metadata count.")
        metas = []
        for i, meta in enumerate(metadata_list):
            metas.append({
                "frame_id": i,
                "timestamp": received_at,
                "timestamp_source": "host batch receipt",
                "roi": self.get_roi(),
                "raw_meta": meta,
                "exposure_s": self._exposure_time_s
            })

        return images_array, metas

