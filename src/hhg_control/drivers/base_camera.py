"""
Abstract Base Class defining the universal interface for camera instruments in the lab.
All camera drivers (real hardware or mocks) must conform strictly to this contract.
"""

from abc import ABC, abstractmethod
from typing import Tuple, List, Dict, Any
import numpy as np


class CameraSafetyError(ValueError):
    """Raised when an acquisition parameter violates safe operating limits."""
    pass


class BaseCamera(ABC):
    """Universal Camera Interface."""

    MIN_EXPOSURE_S: float = 0.0005  # 500 microseconds
    MAX_EXPOSURE_S: float = 10.0    # 10 seconds safety ceiling

    def __init__(self) -> None:
        self._is_connected: bool = False
        self._exposure_time_s: float = 0.010  # default 10 ms

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

    @abstractmethod
    def get_sensor_info(self) -> Dict[str, Any]:
        """Return sensor resolution, pixel size, model, and serial number."""
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

