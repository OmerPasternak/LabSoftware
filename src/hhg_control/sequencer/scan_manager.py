"""Camera sequencing and crash-resistant HDF5 storage."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Optional

import h5py
import numpy as np

from ..drivers.base_camera import BaseCamera


HDF5_SCHEMA_VERSION = "2.0"
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class AcquisitionAborted(RuntimeError):
    """Raised after a requested abort leaves an intentionally incomplete step."""


def sanitize_filename_component(value: str, fallback: str = "HHG_Scan") -> str:
    """Return a cross-platform-safe filename component without path traversal."""
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:96]


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (datetime, Path)):
        return str(value)
    return value


class CameraScanManager:
    """Serialize camera access and write versioned, MATLAB-readable HDF5 files."""

    def __init__(self, camera: BaseCamera, storage_dir: Path | str) -> None:
        self.camera = camera
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._camera_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._state = "IDLE"

    @property
    def state(self) -> str:
        """Return the current serialized acquisition state."""
        with self._state_lock:
            return self._state

    @contextmanager
    def _operation(self, state: str):
        with self._state_lock:
            if self._state != "IDLE":
                raise RuntimeError(f"Camera is busy ({self._state}); cannot start {state}.")
            self._state = state
        try:
            yield
        finally:
            with self._state_lock:
                self._state = "IDLE"

    def set_storage_dir(self, new_dir: Path | str) -> None:
        """Set and create the scan output directory."""
        self.storage_dir = Path(new_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Set hardware ROI bounds in unbinned sensor pixels."""
        with self._camera_lock:
            self.camera.set_roi(roi)

    def get_roi(self) -> tuple[int, int, int, int]:
        """Get zero-based, upper-exclusive hardware ROI bounds in pixels."""
        with self._camera_lock:
            return self.camera.get_roi()

    def start_live(self, buffer_size: int = 4) -> None:
        """Enter LIVE state and start the camera's persistent preview buffer."""
        with self._state_lock:
            if self._state != "IDLE":
                raise RuntimeError(f"Camera is busy ({self._state}); cannot start LIVE.")
            self._state = "LIVE"
        try:
            with self._camera_lock:
                self.camera.start_live(buffer_size=buffer_size)
        except Exception:
            with self._state_lock:
                self._state = "IDLE"
            raise

    def acquire_live_frame(self, timeout_s: float | None = None) -> tuple[np.ndarray, dict]:
        """Read the newest frame from an active live acquisition."""
        if self.state != "LIVE":
            raise RuntimeError("Live acquisition is not active.")
        with self._camera_lock:
            return self.camera.acquire_live_frame(timeout_s=timeout_s)

    def stop_live(self) -> None:
        """Stop preview acquisition and return to IDLE after the active read completes."""
        if self.state != "LIVE":
            return
        with self._camera_lock:
            self.camera.stop_live()
        with self._state_lock:
            self._state = "IDLE"

    def acquire_preview(self) -> tuple[np.ndarray, dict]:
        """Acquire one unsaved preview frame while the camera is idle."""
        if not self.camera.is_connected:
            raise RuntimeError("Cannot acquire preview: Camera is disconnected.")
        with self._operation("PREVIEW"), self._camera_lock:
            frames, metas = self.camera.acquire_frames(num_frames=1)
        return frames[0], metas[0] if metas else {}

    def _unique_filepath(self, experiment_name: str, run_id: str, step_index: int) -> Path:
        clean_exp = sanitize_filename_component(experiment_name)
        clean_run = sanitize_filename_component(run_id, fallback="run")
        base = self.storage_dir / f"{clean_exp}_{clean_run}_step_{step_index:04d}.h5"
        if not base.exists() and not base.with_suffix(".h5.partial").exists():
            return base
        suffix = datetime.now(timezone.utc).strftime("%H%M%S_%f")
        return self.storage_dir / f"{clean_exp}_{clean_run}_{suffix}_step_{step_index:04d}.h5"

    def acquire_and_save_step(
        self,
        experiment_name: str,
        step_index: int,
        param_name: str,
        param_value: float,
        num_frames: int,
        *,
        run_id: str | None = None,
        batch_size: int = 4,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> tuple[Path, np.ndarray]:
        """Acquire and atomically save one scan step in bounded frame batches.

        Units are raw 16-bit ADU, seconds for exposure, and sensor pixels for ROI.
        """
        with self._operation("SCANNING"):
            return self._acquire_and_save_step(
                experiment_name, step_index, param_name, param_value, num_frames,
                run_id=run_id, batch_size=batch_size, abort_check=abort_check,
            )

    def _acquire_and_save_step(
        self,
        experiment_name: str,
        step_index: int,
        param_name: str,
        param_value: float,
        num_frames: int,
        *,
        run_id: str | None,
        batch_size: int,
        abort_check: Optional[Callable[[], bool]],
    ) -> tuple[Path, np.ndarray]:
        if not self.camera.is_connected:
            raise RuntimeError("Cannot execute scan step: Camera is disconnected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")

        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        filepath = self._unique_filepath(experiment_name, run_id, step_index)
        partial_path = filepath.with_suffix(".h5.partial")
        sensor_info = self.camera.get_sensor_info()
        exposure_s = self.camera.get_exposure_time()
        timestamp_str = datetime.now(timezone.utc).isoformat()
        clean_exp = sanitize_filename_component(experiment_name)
        clean_param = str(param_name).strip() or "Setpoint"
        roi = self.camera.get_roi()

        latest_frame: np.ndarray | None = None
        frame_metadata: list[dict[str, Any]] = []
        written = 0

        with self._camera_lock, h5py.File(partial_path, "w") as h5f:
            h5f.attrs["schema_version"] = HDF5_SCHEMA_VERSION
            h5f.attrs["complete"] = False
            dset = None
            for images, metadata in self.camera.iter_frames(num_frames, batch_size=batch_size):
                if abort_check is not None and abort_check():
                    h5f.attrs["frames_written"] = written
                    h5f.flush()
                    raise AcquisitionAborted(
                        f"Acquisition stopped after {written} of {num_frames} frames; partial file retained."
                    )
                if images.ndim != 3 or images.dtype != np.uint16:
                    raise ValueError("Camera batches must have shape [frame, y, x] and dtype uint16.")
                if dset is None:
                    height, width = images.shape[1:]
                    dset = h5f.create_dataset(
                        "images",
                        shape=(num_frames, height, width),
                        dtype="uint16",
                        chunks=(1, min(512, height), min(512, width)),
                        compression="gzip",
                        compression_opts=1,
                        shuffle=True,
                    )
                    dset.attrs["units"] = "16-bit digital counts (ADU)"
                    dset.attrs["physical_units"] = "16-bit digital counts (ADU)"
                    dset.attrs["dimension_order"] = "[frame, y, x]"
                    dset.attrs["data_dimension_ordering"] = "[frame_index, sensor_height_y, sensor_width_x]"
                    dset.attrs["description"] = "Raw 16-bit sCMOS image stack recorded at this scan step"
                end = written + images.shape[0]
                if end > num_frames:
                    raise ValueError("Camera returned more frames than requested.")
                dset[written:end, :, :] = images
                latest_frame = images[-1].copy()
                frame_metadata.extend(metadata)
                written = end

            if written != num_frames or dset is None or latest_frame is None:
                raise RuntimeError(f"Camera returned {written} frames; expected {num_frames}.")

            meta_group = h5f.create_group("frame_metadata")
            string_dtype = h5py.string_dtype(encoding="utf-8")
            metadata_json = [json.dumps(item, default=_json_safe, sort_keys=True) for item in frame_metadata]
            meta_group.create_dataset("json", data=np.asarray(metadata_json, dtype=object), dtype=string_dtype)
            meta_group.attrs["description"] = "One JSON object per acquired frame; timestamps may be camera or host supplied."

            attrs = {
                "experiment_name": clean_exp,
                "experiment_identifier": clean_exp,
                "run_id": run_id,
                "step_index": int(step_index),
                "scan_step_index": int(step_index),
                "scan_parameter_name": clean_param,
                "scan_parameter_display_name": clean_param,
                "scan_parameter_value": float(param_value),
                "scan_parameter_setpoint_value": float(param_value),
                "exposure_time_s": float(exposure_s),
                "exposure_duration_seconds": float(exposure_s),
                "num_frames": int(num_frames),
                "frame_accumulation_count": int(num_frames),
                "frames_written": int(written),
                "timestamp_utc": timestamp_str,
                "acquisition_timestamp_utc_iso8601": timestamp_str,
                "camera_model": str(sensor_info.get("model", "pco.edge")),
                "camera_manufacturer_and_model": str(sensor_info.get("model", "pco.edge")),
                "camera_serial": str(sensor_info.get("serial_number", "UNKNOWN")),
                "camera_hardware_serial_number": str(sensor_info.get("serial_number", "UNKNOWN")),
                "sensor_width": int(sensor_info.get("width", latest_frame.shape[1])),
                "sensor_pixel_width": int(sensor_info.get("width", latest_frame.shape[1])),
                "sensor_height": int(sensor_info.get("height", latest_frame.shape[0])),
                "sensor_pixel_height": int(sensor_info.get("height", latest_frame.shape[0])),
                "roi_bounds": roi,
            }
            for key, value in attrs.items():
                h5f.attrs[key] = value
            if hasattr(self.camera, "get_readout_mode"):
                mode = self.camera.get_readout_mode()
                h5f.attrs["readout_mode"] = mode.name
                h5f.attrs["readout_mode_value"] = int(mode.value)
            h5f.attrs["complete"] = True
            h5f.flush()

        os.replace(partial_path, filepath)
        return filepath, latest_frame

    def execute_scan(
        self,
        experiment_name: str,
        param_name: str,
        start_value: float,
        step_size: float,
        num_steps: int,
        num_frames: int,
        start_step: int = 0,
        on_step_start: Optional[Callable[[int, float], None]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
        run_id: str | None = None,
    ) -> Iterator[tuple[int, float, Path, np.ndarray]]:
        """Execute a metadata setpoint scan without commanding an actuator."""
        if not self.camera.is_connected:
            raise RuntimeError("Cannot execute scan: Camera is disconnected.")
        if num_steps < 1:
            raise ValueError(f"Number of steps must be at least 1, got {num_steps}.")
        if start_step < 0 or start_step >= num_steps:
            raise ValueError(f"start_step must be between 0 and {num_steps - 1}, got {start_step}.")
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")

        with self._operation("SCANNING"):
            for step in range(start_step, num_steps):
                if abort_check is not None and abort_check():
                    break
                value = start_value + step * step_size
                if on_step_start is not None:
                    on_step_start(step, value)
                try:
                    filepath, latest_frame = self._acquire_and_save_step(
                        experiment_name, step, param_name, value, num_frames,
                        run_id=run_id, batch_size=4, abort_check=abort_check,
                    )
                except AcquisitionAborted:
                    break
                yield step, value, filepath, latest_frame
