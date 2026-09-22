"""
Hardware-independent mock emulator for pco.edge 5.5 sCMOS camera.
Simulates dark counts, read noise, and a Gaussian beam profile.

Readout modes:
  - ROLLING_SHUTTER: Simulates the rolling-shutter light-sheet artefact seen
      with pulsed laser sources.  Only the rows that are 'open' when the
      simulated laser pulse fires receive the beam signal.  The active band
      drifts slowly frame-to-frame to mimic a free-running (unsynchronised)
      acquisition.  This is intentionally dramatic so the artefact is obvious
      in the UI when evaluating modes.
  - GLOBAL_RESET:    All rows reset simultaneously; the full Gaussian is
      visible in every frame.  This is the recommended mode for HHG.
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

    # Rolling-shutter simulation parameters
    # The active readout 'band' spans this fraction of the sensor height
    # (represents the fraction of rows open during a single laser pulse).
    _ROLLING_BAND_FRACTION: float = 0.10   # 10% → 216 rows out of 2160
    # How fast (rows/frame) the rolling band drifts in free-run
    _ROLLING_DRIFT_ROWS_PER_FRAME: int = 43  # ~2× per 100 frames ≈ visible drift

    def __init__(self, fast_simulation: bool = False) -> None:
        super().__init__()
        self.fast_simulation = fast_simulation
        self._model  = "pco.edge 5.5 USB (EMULATOR)"
        self._serial = "MOCK-EDGE-5501"
        self._rng = np.random.default_rng(42)

        # Precompute static 2D spatial Gaussian beam profile (sigma = 120 px, centred at (1280, 1080))
        y, x = np.ogrid[:self.HEIGHT, :self.WIDTH]
        center_y, center_x = self.HEIGHT // 2, self.WIDTH // 2
        sigma_x, sigma_y = 120.0, 120.0
        self._gaussian_profile = np.exp(
            -(((x - center_x) ** 2) / (2 * sigma_x ** 2) + ((y - center_y) ** 2) / (2 * sigma_y ** 2))
        ).astype(np.float32)

        # 1-D row envelope for the rolling-shutter light-sheet artefact
        # Shape (HEIGHT, 1) so it broadcasts against (HEIGHT, WIDTH) frames
        half_band = int(self.HEIGHT * self._ROLLING_BAND_FRACTION / 2)
        rows = np.arange(self.HEIGHT, dtype=np.float32)
        # The band envelope is a narrow Gaussian; its centre shifts each frame
        sigma_band = float(half_band)
        self._band_sigma = sigma_band
        # band_centre is updated per frame; initialise at sensor centre
        self._rolling_band_centre: float = float(center_y)

        # Region bounding box for shot noise (±4σ; outside this, signal ≈ 0)
        self._roi_y1 = max(0, int(center_y - 4 * sigma_y))
        self._roi_y2 = min(self.HEIGHT, int(center_y + 4 * sigma_y))
        self._roi_x1 = max(0, int(center_x - 4 * sigma_x))
        self._roi_x2 = min(self.WIDTH, int(center_x + 4 * sigma_x))

        # Pre-generate a bank of realistic dark noise patterns (mean = 100 ADU, std = 3 ADU)
        self._dark_bank = self._rng.normal(
            loc=100.0, scale=3.0, size=(4, self.HEIGHT, self.WIDTH)
        ).astype(np.float32)
        self._dark_idx = 0
        self._frame_count = 0   # used to advance rolling band position

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._is_connected = True

    def close(self) -> None:
        self._is_connected = False

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
        """Switch the simulated readout mode immediately (no reboot required in the mock).

        Args:
            mode: ReadoutMode.ROLLING_SHUTTER or ReadoutMode.GLOBAL_RESET.
                  ReadoutMode.GLOBAL_SHUTTER is not supported by the pco.edge 5.5
                  sCMOS sensor and will raise ValueError.

        Notes:
            In rolling-shutter mode the mock synthesises a horizontal light-sheet
            artefact (only the rows inside the simulated readout window receive the
            optical beam signal).  The band drifts across the sensor each frame,
            mimicking a free-running camera that is not synchronised to the laser.
        """
        if mode == ReadoutMode.GLOBAL_SHUTTER:
            raise ValueError(
                "ReadoutMode.GLOBAL_SHUTTER is not supported by the pco.edge 5.5 sCMOS sensor. "
                "Use ROLLING_SHUTTER or GLOBAL_RESET."
            )
        self._readout_mode = mode

    # ------------------------------------------------------------------
    # Frame acquisition
    # ------------------------------------------------------------------

    def _build_rolling_band_envelope(self) -> np.ndarray:
        """Return a (HEIGHT, 1) float32 row-weighting array for the rolling-shutter simulation.

        The active band is a narrow Gaussian centred at ``_rolling_band_centre``
        that drifts downward each frame, wrapping at the sensor boundary.
        """
        rows = np.arange(self.HEIGHT, dtype=np.float32)
        centre = self._rolling_band_centre
        envelope = np.exp(
            -0.5 * ((rows - centre) / self._band_sigma) ** 2
        ).astype(np.float32)
        return envelope[:, np.newaxis]   # shape (HEIGHT, 1)

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Acquire ``num_frames`` synthetic frames.

        Synthesis pipeline (same for both modes):
            1. Dark pedestal: cycled from a pre-generated Gaussian bank (100 ± 3 ADU).
            2. Optical signal: 2-D Gaussian beam scaled by exposure and a 2-Hz modulation.
            3. Shot noise:  σ = √signal (Poisson) applied inside the beam footprint.
            4. Mode-specific row weighting:
               - GLOBAL_RESET:    weight = 1.0 everywhere (all rows see the beam).
               - ROLLING_SHUTTER: weight = narrow Gaussian band that drifts per frame.
            5. Clip to [0, 65535] and cast to uint16.
            6. Crop to the configured hardware ROI.

        Returns:
            images:   uint16 array of shape (num_frames, roi_height, roi_width).
            metadata: list of per-frame dicts with timestamp and acquisition info.
        """
        if not self._is_connected:
            raise RuntimeError("Cannot acquire frames: Mock camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        if not self.fast_simulation:
            time.sleep(self._exposure_time_s * num_frames)

        images = np.zeros((num_frames, self.HEIGHT, self.WIDTH), dtype=np.uint16)
        metadata = []

        # Exposure-scaled peak signal with ±40 % sinusoidal modulation at 2 Hz
        base_peak = np.float32(min(55000.0, 5000.0 + (self._exposure_time_s / 0.010) * 8000.0))
        t_start   = time.time()
        modulation  = np.float32(1.0 + 0.40 * np.sin(2 * np.pi * 2.0 * t_start))
        peak_counts = base_peak * modulation

        roi_signal   = (
            self._gaussian_profile[self._roi_y1:self._roi_y2, self._roi_x1:self._roi_x2]
            * peak_counts
        )
        roi_shot_std = np.sqrt(np.maximum(roi_signal, 1.0, dtype=np.float32))
        roi_h = self._roi_y2 - self._roi_y1
        roi_w = self._roi_x2 - self._roi_x1

        x0, y0, x1, y1 = self.get_roi()
        is_rolling = (self._readout_mode == ReadoutMode.ROLLING_SHUTTER)

        for i in range(num_frames):
            dark_frame = self._dark_bank[self._dark_idx % len(self._dark_bank)].copy()
            self._dark_idx += 1

            shot_noise = (
                self._rng.standard_normal(size=(roi_h, roi_w), dtype=np.float32) * roi_shot_std
            )

            if is_rolling and not self.fast_simulation:
                # Apply the row-dependent envelope: rows outside the active band
                # receive only dark noise (as on the real sensor in rolling mode).
                band = self._build_rolling_band_envelope()   # (HEIGHT, 1)
                band_roi = band[self._roi_y1:self._roi_y2, :]  # (roi_h, 1)
                signal_with_band = (roi_signal + shot_noise) * band_roi
                dark_frame[self._roi_y1:self._roi_y2, self._roi_x1:self._roi_x2] += signal_with_band
                # Advance the band centre for the next frame
                self._rolling_band_centre = (
                    (self._rolling_band_centre + self._ROLLING_DRIFT_ROWS_PER_FRAME)
                    % self.HEIGHT
                )
            else:
                # Global reset: every row receives the full beam signal simultaneously
                dark_frame[self._roi_y1:self._roi_y2, self._roi_x1:self._roi_x2] += roi_signal + shot_noise

            images[i] = np.clip(dark_frame, 0, 65535).astype(np.uint16)
            self._frame_count += 1

            t_now = time.time()
            metadata.append({
                "frame_id":          i,
                "timestamp":         t_now,
                "camera_timestamp":  t_now,
                "camera_time_str":   datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "exposure_s":        self._exposure_time_s,
                "roi":               (x0, y0, x1, y1),
                "readout_mode":      self._readout_mode.name,
                "data_type":         "Synthetic 2D Gaussian + Poisson shot noise + dark pedestal",
                "simulated":         True,
            })

        # Crop to the configured hardware ROI
        return np.ascontiguousarray(images[:, y0:y1, x0:x1]), metadata
