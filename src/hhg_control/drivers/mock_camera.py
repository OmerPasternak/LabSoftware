"""
Hardware-independent mock emulator for pco.edge 5.5 sCMOS camera.
Simulates dark counts, read noise, and a Gaussian beam profile.
"""

import time
from typing import Tuple, List, Dict, Any
import numpy as np
from .base_camera import BaseCamera


class MockPcoCamera(BaseCamera):
    """Mock emulator matching the pco.edge 5.5 USB camera."""

    WIDTH: int = 2560
    HEIGHT: int = 2160

    def __init__(self, fast_simulation: bool = False) -> None:
        super().__init__()
        self.fast_simulation = fast_simulation
        self._model = "pco.edge 5.5 USB (EMULATOR)"
        self._serial = "MOCK-EDGE-5501"

    def connect(self) -> None:
        self._is_connected = True

    def close(self) -> None:
        self._is_connected = False

    def set_exposure_time(self, exposure_s: float) -> None:
        self.validate_exposure_time(exposure_s)
        self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        return self._exposure_time_s

    def get_sensor_info(self) -> Dict[str, Any]:
        return {
            "model": self._model,
            "serial_number": self._serial,
            "width": self.WIDTH,
            "height": self.HEIGHT,
            "bit_depth": 16,
            "pixel_size_um": 6.5,
            "interface": "USB 3.0 (Emulated)"
        }

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        if not self._is_connected:
            raise RuntimeError("Cannot acquire frames: Mock camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        if not self.fast_simulation:
            time.sleep(self._exposure_time_s * num_frames)

        images = np.zeros((num_frames, self.HEIGHT, self.WIDTH), dtype=np.uint16)
        metadata = []

        # Synthetic beam: 2D Gaussian spot near the center of the sensor
        y, x = np.ogrid[:self.HEIGHT, :self.WIDTH]
        center_y, center_x = self.HEIGHT // 2, self.WIDTH // 2
        sigma_x, sigma_y = 120.0, 120.0
        gaussian_profile = np.exp(-(((x - center_x) ** 2) / (2 * sigma_x ** 2) + 
                                    ((y - center_y) ** 2) / (2 * sigma_y ** 2)))

        peak_counts = min(55000.0, 5000.0 + (self._exposure_time_s / 0.010) * 8000.0)

        for i in range(num_frames):
            dark_noise = np.random.normal(loc=100.0, scale=3.0, size=(self.HEIGHT, self.WIDTH))
            shot_noise = np.random.normal(loc=0.0, scale=np.sqrt(np.maximum(gaussian_profile * peak_counts, 1.0)))
            frame = dark_noise + (gaussian_profile * peak_counts) + shot_noise
            images[i] = np.clip(frame, 0, 65535).astype(np.uint16)
            
            metadata.append({
                "frame_id": i,
                "timestamp": time.time(),
                "exposure_s": self._exposure_time_s,
                "simulated": True
            })

        return images, metadata

