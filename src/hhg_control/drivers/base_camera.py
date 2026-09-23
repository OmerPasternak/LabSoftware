"""
Abstract Base Class defining the universal interface for camera instruments in the lab.
All camera drivers (real hardware or mocks) must conform strictly to this contract.
"""

from abc import ABC, abstractmethod
from enum import IntEnum
from collections.abc import Iterator
from typing import Tuple, List, Dict, Any
import numpy as np


class CameraSafetyError(ValueError):
    """Raised when an acquisition parameter violates safe operating limits."""
    pass


class ReadoutMode(IntEnum):
    """Sensor readout modes for the pco.edge 5.5 sCMOS camera.

    Values match the PCO SDK ``set_camera_setup`` shutter-mode parameter
    (C constant SCCMOS_FORMAT_*).

    Attributes:
        ROLLING_SHUTTER: Standard sCMOS rolling readout.  Each row is exposed
            sequentially; different rows capture the scene at slightly
            different times.  Gives the highest frame rate but produces a
            **light-sheet artefact** with pulsed laser sources (HHG) where
            only the rows open during the laser pulse receive signal.
        GLOBAL_SHUTTER: All pixels start and stop exposure together. Available
            on the pco.edge 5.5 USB, at a lower maximum rate than rolling mode.
        GLOBAL_RESET:    All rows reset (start of exposure) simultaneously so
            every pixel integrates the same laser pulse.  Readout is still
            sequential (rolling), but there is no light-sheet artefact.
            This mode remains available for existing scripts, but differs from
            true global shutter because exposure ends row by row.
    """
    ROLLING_SHUTTER = 1
    GLOBAL_SHUTTER  = 2
    GLOBAL_RESET    = 4


class BaseCamera(ABC):
    """Universal Camera Interface."""

    MIN_EXPOSURE_S: float = 0.0005  # 500 microseconds
    MAX_EXPOSURE_S: float = 10.0    # 10 seconds safety ceiling

    def __init__(self) -> None:
        self._is_connected: bool = False
        self._exposure_time_s: float = 0.010  # default 10 ms
        self._readout_mode: ReadoutMode = ReadoutMode.ROLLING_SHUTTER

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @abstractmethod
    def connect(self) -> None:
        """Establish communication and configure initial camera state."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Safely release the camera handle and reset acquisition hardware."""
        pass

    def validate_exposure_time(self, exposure_s: float) -> None:
        """Check whether exposure time is within safe physical limits."""
        if not (self.MIN_EXPOSURE_S <= exposure_s <= self.MAX_EXPOSURE_S):
            raise CameraSafetyError(
                f"Requested exposure time {exposure_s * 1e3:.2f} ms is outside "
                f"safe limits [{self.MIN_EXPOSURE_S * 1e3:.2f} ms, "
                f"{self.MAX_EXPOSURE_S * 1e3:.2f} ms]."
            )

    @abstractmethod
    def set_exposure_time(self, exposure_s: float) -> None:
        """Set exposure time in seconds."""
        pass

    @abstractmethod
    def get_exposure_time(self) -> float:
        """Get exposure time in seconds."""
        pass

    @abstractmethod
    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """
        Acquire a sequence of frames.
        Returns:
            images: 3D numpy array of shape (num_frames, height, width), uint16.
            metadata: List of dicts containing timestamps and hardware flags.
        """
        pass

    def iter_frames(
        self,
        num_frames: int,
        batch_size: int = 4,
    ) -> Iterator[Tuple[np.ndarray, List[Dict[str, Any]]]]:
        """Yield bounded frame batches for memory-safe storage.

        Args:
            num_frames: Total number of frames to acquire.
            batch_size: Maximum frames returned per batch. Units are frames.
        """
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1.")
        remaining = num_frames
        while remaining:
            count = min(batch_size, remaining)
            yield self.acquire_frames(count)
            remaining -= count

    def start_live(self, buffer_size: int = 4) -> None:
        """Start continuous preview acquisition with a bounded frame buffer."""
        if not self.is_connected:
            raise RuntimeError("Camera is not connected.")
        self._live_active = True

    def acquire_live_frame(self, timeout_s: float | None = None) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Return the newest preview frame and metadata."""
        if not getattr(self, "_live_active", False):
            raise RuntimeError("Live acquisition is not active.")
        frames, metadata = self.acquire_frames(1)
        return frames[0], metadata[0] if metadata else {}

    def stop_live(self) -> None:
        """Stop continuous preview acquisition and release its buffers."""
        self._live_active = False

    @abstractmethod
    def get_sensor_info(self) -> Dict[str, Any]:
        """Return sensor resolution, pixel size, model, and serial number."""
        pass

    def get_roi(self) -> tuple[int, int, int, int]:
        """Return zero-based (x0, y0, x1, y1) pixel bounds (upper bounds excluded)."""
        info = self.get_sensor_info()
        return getattr(self, "_roi", (0, 0, info.get("width", 2560), info.get("height", 2160)))

    def get_roi_limits(self) -> Dict[str, Any]:
        """Return hardware pixel steps, minimum size, and symmetry requirements."""
        return {
            "steps": (1, 1),
            "minimum": (1, 1),
            "symmetric": (False, False)
        }

    def validate_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Validate ROI parameters against sensor geometry and hardware constraints."""
        if len(roi) != 4 or any(not isinstance(v, (int, np.integer)) for v in roi):
            raise ValueError("ROI requires four integer pixel bounds (x0, y0, x1, y1).")
        x0, y0, x1, y1 = [int(v) for v in roi]
        info = self.get_sensor_info()
        w, h = info.get("width", 2560), info.get("height", 2160)
        limits = self.get_roi_limits()
        step_x, step_y = limits["steps"]
        min_w, min_h = limits["minimum"]
        sym_x, sym_y = limits["symmetric"]

        if not (0 <= x0 < x1 <= w) or not (0 <= y0 < y1 <= h):
            raise ValueError(f"ROI ({x0}, {y0}, {x1}, {y1}) is outside sensor bounds [0, 0, {w}, {h}].")

        if (x1 - x0) < min_w or (y1 - y0) < min_h:
            raise ValueError(f"ROI span ({x1 - x0} x {y1 - y0}) is smaller than minimum ({min_w} x {min_h}).")

        if (x0 % step_x != 0) or (x1 % step_x != 0):
            raise ValueError(f"Horizontal ROI bounds must be multiples of {step_x} pixels.")
        if (y0 % step_y != 0) or (y1 % step_y != 0):
            raise ValueError(f"Vertical ROI bounds must be multiples of {step_y} pixels.")

        if sym_x and (x0 + x1 != w):
            raise ValueError(f"Horizontal ROI must be symmetric about sensor center (x0 + x1 = {w}).")
        if sym_y and (y0 + y1 != h):
            raise ValueError(f"Vertical ROI must be symmetric about sensor center (y0 + y1 = {h}).")

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Configure hardware readout bounds."""
        raise NotImplementedError("Hardware ROI is not supported by this camera driver.")

    def get_readout_mode(self) -> ReadoutMode:
        """Return the current sensor readout mode.

        Returns:
            ReadoutMode enum value reflecting the mode currently programmed
            into the camera hardware (or the most recently requested mode
            for drivers that cache the setting).
        """
        return self._readout_mode

    def set_readout_mode(self, mode: ReadoutMode) -> None:
        """Configure the sensor readout mode.

        Switching modes on a physical pco.edge requires a full camera
        reboot (~3–5 s).  Drivers that implement this must:
          1. Stop any active recording.
          2. Call the SDK shutter-mode setter (PCO ``set_camera_setup``).
          3. Reboot/restart the camera and reconnect.
          4. Update ``self._readout_mode``.

        Raises:
            NotImplementedError: If this driver does not support readout
                mode selection (default behaviour).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support readout mode selection."
        )

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


