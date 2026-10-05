"""Camera sequencing and crash-resistant HDF5 storage."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import threading
import time
from typing import Any, Optional

import numpy as np

from ..drivers.base_camera import BaseCamera, TriggerMode
from ..safe_io import check_disk_space


HDF5_SCHEMA_VERSION = "2.0"
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class AcquisitionAborted(RuntimeError):
    """Raised after a requested abort leaves an intentionally incomplete step."""


class AcquisitionBackpressure(RuntimeError):
    """Raised when storage cannot accept frames within the bounded queue."""


def _frame_chunk_shape(height: int, width: int) -> tuple[int, int, int]:
    """Tile a uint16 frame with chunks at most 1 MiB and no edge padding."""
    max_pixels = (1024 * 1024) // np.dtype("uint16").itemsize
    rows = [size for size in range(1, height + 1) if height % size == 0]
    cols = [size for size in range(1, width + 1) if width % size == 0]
    best_rows, best_cols = 1, 1
    for row in rows:
        for col in cols:
            if row * col <= max_pixels and row * col > best_rows * best_cols:
                best_rows, best_cols = row, col
    return 1, best_rows, best_cols


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

    def __init__(
        self, camera: BaseCamera, storage_dir: Path | str,
        *, compression: str | None = None,
    ) -> None:
        """Configure scan storage; use no compression for fast acquisition.

        ``compression='gzip'`` keeps the older compact format for slow scans.
        Both modes use the same HDF5 schema and raw 16-bit ADU units.
        """
        if compression not in (None, "gzip"):
            raise ValueError("compression must be None or 'gzip'.")
        self.camera = camera
        self.compression = compression
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
        with self._operation("CONFIGURING"), self._camera_lock:
            self.camera.set_roi(roi)

    def set_readout_mode(self, mode) -> None:
        """Configure and verify shutter mode while acquisition is idle."""
        with self._operation("CONFIGURING"), self._camera_lock:
            self.camera.set_readout_mode(mode)
            if self.camera.get_readout_mode() != mode:
                raise RuntimeError("Shutter readback differs from request.")

    def set_exposure_time(self, exposure_s: float) -> float:
        """Configure exposure seconds while idle, returning verified readback."""
        with self._operation("CONFIGURING"), self._camera_lock:
            self.camera.set_exposure_time(exposure_s)
            actual = self.camera.get_exposure_time()
            if not math.isclose(actual, exposure_s, rel_tol=1e-5, abs_tol=1e-9):
                raise RuntimeError("Exposure readback differs from request.")
            return actual

    def disconnect(self) -> None:
        """Release the camera under the same lock used by acquisition."""
        with self._operation("DISCONNECTING"), self._camera_lock:
            self.camera.close()

    def get_roi(self) -> tuple[int, int, int, int]:
        """Get zero-based, upper-exclusive hardware ROI bounds in pixels."""
        with self._camera_lock:
            return self.camera.get_roi()

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Change camera triggering only while live and scan acquisition are idle."""
        with self._operation("CONFIGURING"), self._camera_lock:
            self.camera.set_trigger_mode(mode)

    def get_trigger_mode(self) -> TriggerMode:
        """Read the active camera trigger mode under the camera access lock."""
        with self._camera_lock:
            return self.camera.get_trigger_mode()

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
            try:
                self.camera.stop_live()
            except Exception:
                self.camera.state_unknown = True
                raise
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
        queue_batches: int = 4,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> tuple[Path, np.ndarray]:
        """Acquire and atomically save one scan step in bounded frame batches.

        Units are raw 16-bit ADU, seconds for exposure, and sensor pixels for ROI.
        The bounded queue overlaps camera capture with HDF5 writing. A mock
        waits for storage; a physical camera fails the step on backpressure
        instead of silently losing frames.
        """
        with self._operation("SCANNING"):
            return self._acquire_and_save_step(
                experiment_name, step_index, param_name, param_value, num_frames,
                run_id=run_id, batch_size=batch_size, queue_batches=queue_batches,
                abort_check=abort_check,
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
        queue_batches: int,
        abort_check: Optional[Callable[[], bool]],
    ) -> tuple[Path, np.ndarray]:
        if not self.camera.is_connected:
            raise RuntimeError("Cannot execute scan step: Camera is disconnected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")
        if batch_size < 1 or queue_batches < 1:
            raise ValueError("batch_size and queue_batches must be >= 1.")

        # HDF5 is only needed for a saved scan, not for opening the GUI or live view.
        import h5py

        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        filepath = self._unique_filepath(experiment_name, run_id, step_index)
        partial_path = filepath.with_suffix(".h5.partial")
        sensor_info = self.camera.get_sensor_info()
        exposure_s = self.camera.get_exposure_time()
        timestamp_str = datetime.now(timezone.utc).isoformat()
        clean_exp = sanitize_filename_component(experiment_name)
        clean_param = str(param_name).strip() or "Setpoint"
        roi = self.camera.get_roi()
        check_disk_space(self.storage_dir, int(num_frames * (roi[2] - roi[0]) * (roi[3] - roi[1]) * 2 * 1.1))

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
                "frames_written": 0,
                "timestamp_utc": timestamp_str,
                "acquisition_timestamp_utc_iso8601": timestamp_str,
                "camera_model": str(sensor_info.get("model", "pco.edge")),
                "camera_manufacturer_and_model": str(sensor_info.get("model", "pco.edge")),
                "camera_serial": str(sensor_info.get("serial_number", "UNKNOWN")),
                "camera_hardware_serial_number": str(sensor_info.get("serial_number", "UNKNOWN")),
                "sensor_width": int(sensor_info.get("width", roi[2] - roi[0])),
                "sensor_pixel_width": int(sensor_info.get("width", roi[2] - roi[0])),
                "sensor_height": int(sensor_info.get("height", roi[3] - roi[1])),
                "sensor_pixel_height": int(sensor_info.get("height", roi[3] - roi[1])),
                "roi_bounds": roi,
                "storage_compression": self.compression or "none",
                "trigger_mode": self.camera.get_trigger_mode().value,
        }
        mode = self.camera.get_readout_mode()
        attrs["readout_mode"] = mode.name
        attrs["readout_mode_value"] = int(mode.value)
        pending: Queue[tuple[np.ndarray, list[dict[str, Any]]]] = Queue(maxsize=queue_batches)
        producer_done = threading.Event()
        writer_errors: list[BaseException] = []
        latest_frame: np.ndarray | None = None
        captured = 0
        written = 0
        producer_error: BaseException | None = None
        producer_complete = False

        def write_batches() -> None:
            """Own the HDF5 handle and drain copied frame batches in order."""
            nonlocal written
            try:
                with h5py.File(partial_path, "x") as h5f:
                    h5f.attrs["schema_version"] = HDF5_SCHEMA_VERSION
                    h5f.attrs["complete"] = False
                    for key, value in attrs.items():
                        h5f.attrs[key] = value
                    metadata_group = h5f.create_group("frame_metadata")
                    metadata_group.attrs["description"] = (
                        "One JSON object per acquired frame; timestamps may be camera or host supplied."
                    )
                    metadata_dset = metadata_group.create_dataset(
                        "json", shape=(num_frames,),
                        dtype=h5py.string_dtype(encoding="utf-8"),
                        chunks=(min(256, num_frames),),
                    )
                    image_dset = None
                    batches_written = 0
                    next_space_check = 0.0
                    while True:
                        try:
                            images, metadata = pending.get(timeout=0.05)
                        except Empty:
                            if producer_done.is_set():
                                break
                            continue
                        if image_dset is None:
                            height, width = images.shape[1:]
                            options = (
                                {"compression": "gzip", "compression_opts": 1, "shuffle": True}
                                if self.compression == "gzip" else {}
                            )
                            image_dset = h5f.create_dataset(
                                "images", shape=(num_frames, height, width), dtype="uint16",
                                chunks=_frame_chunk_shape(height, width), **options,
                            )
                            image_dset.attrs["units"] = "16-bit digital counts (ADU)"
                            image_dset.attrs["physical_units"] = "16-bit digital counts (ADU)"
                            image_dset.attrs["dimension_order"] = "[frame, y, x]"
                            image_dset.attrs["data_dimension_ordering"] = (
                                "[frame_index, sensor_height_y, sensor_width_x]"
                            )
                            image_dset.attrs["description"] = (
                                "Raw 16-bit sCMOS image stack recorded at this scan step"
                            )
                        end = written + len(images)
                        if time.monotonic() >= next_space_check:
                            check_disk_space(self.storage_dir, images.nbytes)
                            next_space_check = time.monotonic() + 1.0
                        self._write_batch(image_dset, metadata_dset, written, images, metadata)
                        written = end
                        batches_written += 1
                        if batches_written % 32 == 0:
                            h5f.attrs["frames_written"] = written
                            h5f.flush()
                    h5f.attrs["frames_written"] = written
                    if producer_complete and written == num_frames and image_dset is not None:
                        h5f.attrs["complete"] = True
                    h5f.flush()
            except BaseException as exc:
                writer_errors.append(exc)

        with self._camera_lock:
            writer = threading.Thread(target=write_batches, name="camera-hdf5-writer")
            writer.start()
            iterator = None
            try:
                iterator = self.camera.iter_frames(
                    num_frames, batch_size=batch_size,
                    stop_check=lambda: bool(writer_errors) or (
                        abort_check is not None and abort_check()
                    ),
                )
                for images, metadata in iterator:
                    if abort_check is not None and abort_check():
                        raise AcquisitionAborted(
                            f"Acquisition stopped after {captured} of {num_frames} frames."
                        )
                    if writer_errors:
                        raise RuntimeError("HDF5 writer failed during acquisition.") from writer_errors[0]
                    if images.ndim != 3 or images.dtype != np.uint16:
                        raise ValueError("Camera batches must have shape [frame, y, x] and dtype uint16.")
                    if len(images) < 1 or captured + len(images) > num_frames:
                        raise ValueError("Camera returned an empty or oversized frame batch.")
                    if len(metadata) != len(images):
                        raise ValueError("Camera metadata count does not match frame count.")
                    copied_batch = (images.copy(), list(metadata))
                    while True:
                        try:
                            pending.put(copied_batch, timeout=0.05)
                            break
                        except Full as exc:
                            if writer_errors:
                                raise RuntimeError("HDF5 writer failed during acquisition.") from writer_errors[0]
                            if abort_check is not None and abort_check():
                                raise AcquisitionAborted(
                                    f"Acquisition stopped after {captured} of {num_frames} frames."
                                ) from exc
                            if not self.camera.can_pause_acquisition:
                                raise AcquisitionBackpressure(
                                    f"HDF5 writer queue filled after {captured} frames "
                                    f"at {images.shape[2]}x{images.shape[1]} pixels; "
                                    "acquisition stopped to avoid frame loss. "
                                    "Use a smaller applied ROI or faster storage."
                                ) from exc
                    captured += len(images)
                    latest_frame = images[-1].copy()
                if abort_check is not None and abort_check() and captured != num_frames:
                    raise AcquisitionAborted(
                        f"Acquisition stopped after {captured} of {num_frames} frames."
                    )
                if captured != num_frames:
                    raise RuntimeError(f"Camera returned {captured} frames; expected {num_frames}.")
                producer_complete = True
            except BaseException as exc:
                producer_error = exc
            finally:
                try:
                    close_iterator = getattr(iterator, "close", None)
                    if close_iterator is not None:
                        close_iterator()
                except BaseException as exc:
                    if producer_error is None:
                        producer_error = exc
                        producer_complete = False
                producer_done.set()
                writer.join()

        if writer_errors:
            raise RuntimeError("HDF5 writer failed; partial file retained.") from writer_errors[0]
        if producer_error is not None:
            raise producer_error
        if not producer_complete or written != num_frames or latest_frame is None:
            raise RuntimeError("Acquisition incomplete; partial file retained.")
        # Windows rename fails if the destination exists. Other hosts use an
        # exclusive hard link to publish, so a concurrent writer cannot be replaced.
        if os.name == "nt":
            os.rename(partial_path, filepath)
        else:
            os.link(partial_path, filepath)
            partial_path.unlink()
        return filepath, latest_frame

    def _write_batch(
        self, image_dset: Any, metadata_dset: Any, start: int,
        images: np.ndarray, metadata: list[dict[str, Any]],
    ) -> None:
        """Write one ordered batch of raw ADU frames and per-frame JSON."""
        end = start + len(images)
        image_dset[start:end] = images
        metadata_dset[start:end] = [
            json.dumps(item, default=_json_safe, sort_keys=True) for item in metadata
        ]

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
                        run_id=run_id, batch_size=4, queue_batches=4,
                        abort_check=abort_check,
                    )
                except AcquisitionAborted:
                    break
                yield step, value, filepath, latest_frame
