"""
Hardware-independent mock emulator for pco.edge 5.5 sCMOS camera.
Simulates dark counts, read noise, and a Gaussian beam profile.
"""

import time
from datetime import datetime
from typing import Tuple, List, Dict, Any
import numpy as np
from .base_camera import BaseCamera, ReadoutMode


class MockPcoCamera(BaseCamera):
    """Mock emulator matching the pco.edge 5.5 USB camera."""

    WIDTH: int  = 2560
    HEIGHT: int = 2160

    def __init__(self, fast_simulation: bool = False) -> None:
        super().__init__()
        self.fast_simulation = fast_simulation
        self._model  = "pco.edge 5.5 USB (EMULATOR)"
        self._serial = "MOCK-EDGE-5501"
        self._rng = np.random.default_rng(42)

        # Keep construction cheap so the GUI can appear before GO is pressed.
        # Simulation arrays are prepared by the connection worker, not the UI thread.
        self._gaussian_profile: np.ndarray | None = None
        self._dark_bank: np.ndarray | None = None
        self._dark_idx = 0
        self._frame_count = 0

    def _prepare_simulation_data(self) -> None:
        """Build synthetic full-sensor patterns when the mock connects."""
        if self._gaussian_profile is not None and self._dark_bank is not None:
            return

        # Precompute static 2D spatial Gaussian beam profile (sigma = 120 px, centred at (1280, 1080))
        y, x = np.ogrid[:self.HEIGHT, :self.WIDTH]
        center_y, center_x = self.HEIGHT // 2, self.WIDTH // 2
        sigma_x, sigma_y = 120.0, 120.0
        self._gaussian_profile = np.exp(
            -(((x - center_x) ** 2) / (2 * sigma_x ** 2) + ((y - center_y) ** 2) / (2 * sigma_y ** 2))
        ).astype(np.float32)

        # Region bounding box for shot noise (±4σ; outside this, signal ≈ 0)
        self._roi_y1 = max(0, int(center_y - 4 * sigma_y))
        self._roi_y2 = min(self.HEIGHT, int(center_y + 4 * sigma_y))
        self._roi_x1 = max(0, int(center_x - 4 * sigma_x))
        self._roi_x2 = min(self.WIDTH, int(center_x + 4 * sigma_x))

        # Pre-generate bank of realistic dark noise patterns directly in uint16 for speed
        self._dark_bank = np.clip(
            self._rng.normal(loc=100.0, scale=3.0, size=(4, self.HEIGHT, self.WIDTH)),
            0, 65535
        ).astype(np.uint16)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Prepare synthetic data and mark the mock connected; no hardware is used."""
        self._prepare_simulation_data()
        self._is_connected = True

    def close(self) -> None:
        self.stop_live()
        self._is_connected = False

    def start_live(self, buffer_size: int = 4) -> None:
        """Start simulated live acquisition; buffer size is accepted for API parity."""
        del buffer_size
        if not self._is_connected:
            raise RuntimeError("Cannot start live acquisition: Mock camera is not connected.")
        self._live_active = True

    def acquire_live_frame(self, timeout_s: float | None = None) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Acquire the next synthetic live frame within the configured exposure time."""
        del timeout_s
        if not getattr(self, "_live_active", False):
            raise RuntimeError("Live acquisition is not active.")
        frames, metadata = self.acquire_frames(1)
        return frames[0], metadata[0]

    # ------------------------------------------------------------------
    # Exposure
    # ------------------------------------------------------------------

    def set_exposure_time(self, exposure_s: float) -> None:
        self.validate_exposure_time(exposure_s)
        self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        return self._exposure_time_s

    # ------------------------------------------------------------------
    # Sensor information & ROI
    # ------------------------------------------------------------------

    def get_sensor_info(self) -> Dict[str, Any]:
        return {
            "model":          self._model,
            "serial_number":  self._serial,
            "width":          self.WIDTH,
            "height":         self.HEIGHT,
            "bit_depth":      16,
            "pixel_size_um":  6.5,
            "interface":      "USB 3.0 (Emulated)",
            "readout_mode":   self._readout_mode.name,
        }

    def get_roi_limits(self) -> Dict[str, Any]:
        """Hardware ROI limits matching pco.edge 5.5: 4-pixel X steps, Y symmetric around 1080."""
        return {"steps": (4, 1), "minimum": (64, 16), "symmetric": (False, True)}

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Simulate hardware sensor ROI in unbinned sensor pixels."""
        self.validate_roi(roi)
        self._roi = tuple(roi)

    # ------------------------------------------------------------------
    # Readout mode
    # ------------------------------------------------------------------

    def get_readout_mode(self) -> ReadoutMode:
        """Return the current simulated readout mode."""
        return self._readout_mode

    def set_readout_mode(self, mode: ReadoutMode) -> None:
        """Switch the simulated shutter mode without a hardware reboot."""
        if not isinstance(mode, ReadoutMode):
            raise ValueError("mode must be a ReadoutMode value.")
        self._readout_mode = mode

    # ------------------------------------------------------------------
    # Frame acquisition
    # ------------------------------------------------------------------

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Acquire ``num_frames`` synthetic frames directly in requested ROI bounds."""
        if not self._is_connected:
            raise RuntimeError("Cannot acquire frames: Mock camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        if self._gaussian_profile is None or self._dark_bank is None:
            raise RuntimeError("Mock camera simulation data was not initialized.")

        if not self.fast_simulation:
            time.sleep(self._exposure_time_s * num_frames)

        x0, y0, x1, y1 = self.get_roi()
        roi_h = y1 - y0
        roi_w = x1 - x0

        images = np.zeros((num_frames, roi_h, roi_w), dtype=np.uint16)
        metadata = []

        # Exposure-scaled peak signal with ±40 % sinusoidal modulation at 2 Hz
        base_peak = np.float32(min(50000.0, 5000.0 + (self._exposure_time_s / 0.010) * 8000.0))
        t_start   = time.time()
        modulation  = np.float32(1.0 + 0.40 * np.sin(2 * np.pi * 2.0 * t_start))
        peak_counts = np.float32(min(55000.0, float(base_peak * modulation)))

        # Compute intersection between beam footprint and requested ROI for fast localized noise
        by1 = max(self._roi_y1, y0)
        by2 = min(self._roi_y2, y1)
        bx1 = max(self._roi_x1, x0)
        bx2 = min(self._roi_x2, x1)
        has_beam = (by2 > by1 and bx2 > bx1)

        if has_beam:
            beam_sub = self._gaussian_profile[by1:by2, bx1:bx2] * peak_counts
            shot_std = np.sqrt(np.maximum(beam_sub, 1.0, dtype=np.float32))
            # Region within the output frame
            fy1, fy2 = by1 - y0, by2 - y0
            fx1, fx2 = bx1 - x0, bx2 - x0

        for i in range(num_frames):
            dark_frame = self._dark_bank[self._dark_idx % len(self._dark_bank), y0:y1, x0:x1].copy()
            self._dark_idx += 1

            if has_beam:
                shot_noise = (
                    self._rng.standard_normal(size=(by2 - by1, bx2 - bx1), dtype=np.float32) * shot_std
                )
                sig = np.clip(beam_sub + shot_noise, 0, 65535).astype(np.uint16)
                dark_frame[fy1:fy2, fx1:fx2] = np.clip(
                    dark_frame[fy1:fy2, fx1:fx2].astype(np.int32) + sig, 0, 65535
                ).astype(np.uint16)

            images[i] = dark_frame
            self._frame_count += 1

            t_now = time.time()
            metadata.append({
                "frame_id":          i,
                "timestamp":         t_now,
                "camera_timestamp":  t_now,
                "camera_time_str":   datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "timestamp_source":  "simulated_host_clock",
                "exposure_s":        self._exposure_time_s,
                "roi":               (x0, y0, x1, y1),
                "readout_mode":      self._readout_mode.name,
                "data_type":         "Synthetic 2D Gaussian + Poisson shot noise + dark pedestal",
                "simulated":         True,
            })

        return images, metadata
