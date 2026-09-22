"""
Hardware-independent mock emulator for pco.edge 5.5 sCMOS camera.
Simulates dark counts, read noise, and a Gaussian beam profile.
"""

import time
from datetime import datetime
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
        self._rng = np.random.default_rng(42)

        # Precompute static 2D spatial Gaussian beam profile (sigma = 120 px, centered at (1280, 1080))
        y, x = np.ogrid[:self.HEIGHT, :self.WIDTH]
        center_y, center_x = self.HEIGHT // 2, self.WIDTH // 2
        sigma_x, sigma_y = 120.0, 120.0
        self._gaussian_profile = np.exp(
            -(((x - center_x) ** 2) / (2 * sigma_x ** 2) + ((y - center_y) ** 2) / (2 * sigma_y ** 2))
        ).astype(np.float32)

        # Region of interest (ROI) bounding box for shot noise (+/- 4 sigma = 480 px)
        # Outside 4 sigma the optical signal is zero, so Poisson shot noise variance is 0.
        self._roi_y1 = max(0, int(center_y - 4 * sigma_y))
        self._roi_y2 = min(self.HEIGHT, int(center_y + 4 * sigma_y))
        self._roi_x1 = max(0, int(center_x - 4 * sigma_x))
        self._roi_x2 = min(self.WIDTH, int(center_x + 4 * sigma_x))

        # Pre-generate a bank of realistic dark noise patterns (mean = 100 ADU, std = 3 ADU)
        # Eliminates generating 5.5 million float64 random numbers per frame on the CPU.
        self._dark_bank = self._rng.normal(
            loc=100.0, scale=3.0, size=(4, self.HEIGHT, self.WIDTH)
        ).astype(np.float32)
        self._dark_idx = 0

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

    def get_roi_limits(self) -> Dict[str, Any]:
        """Hardware ROI limits matching pco.edge 5.5: 4-pixel X steps, vertically symmetric around y=1080."""
        return {"steps": (4, 1), "minimum": (64, 16), "symmetric": (False, True)}

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Simulate hardware sensor ROI in unbinned sensor pixels."""
        self.validate_roi(roi)
        self._roi = tuple(roi)

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        if not self._is_connected:
            raise RuntimeError("Cannot acquire frames: Mock camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        if not self.fast_simulation:
            time.sleep(self._exposure_time_s * num_frames)

        # =========================================================================
        # FAST VECTORIZED SCMOS DATA SYNTHESIS:
        # 1. Full 2560 x 2160 uint16 sensor array allocation.
        # 2. Dark Noise: Cycled from precomputed 16-bit Gaussian pedestal (100 +/- 3 ADU).
        # 3. Optical Signal: 2D Gaussian beam scaled by exposure duration.
        # 4. Poisson Shot Noise: Computed within beam ROI where signal > 0.
        # =========================================================================
        images = np.zeros((num_frames, self.HEIGHT, self.WIDTH), dtype=np.uint16)
        metadata = []

        base_peak = np.float32(min(55000.0, 5000.0 + (self._exposure_time_s / 0.010) * 8000.0))
        # Sinusoidal modulation: ±40% amplitude at 2 Hz so update rate is clearly visible by eye
        t_now = time.time()
        modulation = 1.0 + 0.40 * np.sin(2 * np.pi * 2.0 * t_now)
        peak_counts = np.float32(base_peak * modulation)
        roi_signal = (
            self._gaussian_profile[self._roi_y1:self._roi_y2, self._roi_x1:self._roi_x2] * peak_counts
        )
        roi_shot_std = np.sqrt(np.maximum(roi_signal, 1.0, dtype=np.float32))
        roi_h = self._roi_y2 - self._roi_y1
        roi_w = self._roi_x2 - self._roi_x1

        x0, y0, x1, y1 = self.get_roi()

        for i in range(num_frames):
            dark_frame = self._dark_bank[self._dark_idx % len(self._dark_bank)].copy()
            self._dark_idx += 1

            shot_noise = self._rng.standard_normal(size=(roi_h, roi_w), dtype=np.float32) * roi_shot_std
            dark_frame[self._roi_y1:self._roi_y2, self._roi_x1:self._roi_x2] += roi_signal + shot_noise

            images[i] = np.clip(dark_frame, 0, 65535).astype(np.uint16)

            t_now = time.time()
            metadata.append({
                "frame_id": i,
                "timestamp": t_now,
                "camera_timestamp": t_now,
                "camera_time_str": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "exposure_s": self._exposure_time_s,
                "roi": (x0, y0, x1, y1),
                "data_type": "Synthetic 2D Gaussian beam + Poisson shot noise + Dark pedestal",
                "simulated": True
            })

        # Return strictly cropped ROI frames; pixels outside are shut off by hardware
        cropped_images = np.ascontiguousarray(images[:, y0:y1, x0:x1])
        return cropped_images, metadata

