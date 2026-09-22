"""
Experiment Sequencer & Data Storage.
Handles step acquisitions and records multi-frame HDF5 datasets compatible with MATLAB.
"""

from collections.abc import Iterator
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import h5py
import numpy as np
from ..drivers.base_camera import BaseCamera


class CameraScanManager:
    """Manages scan step execution and writes HDF5 files."""

    def __init__(self, camera: BaseCamera, storage_dir: Path | str) -> None:
        self.camera = camera
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def set_storage_dir(self, new_dir: Path | str) -> None:
        self.storage_dir = Path(new_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Set hardware ROI on the underlying camera."""
        self.camera.set_roi(roi)

    def get_roi(self) -> tuple[int, int, int, int]:
        """Get current hardware ROI bounds (x0, y0, x1, y1)."""
        return self.camera.get_roi()

    def acquire_preview(self) -> tuple[np.ndarray, dict]:
        """
        Acquire a single frame without saving to disk for live viewing.
        Returns:
            frame: 2D numpy array (uint16)
            metadata: dict with frame metadata including camera timestamp
        """
        if not self.camera.is_connected:
            raise RuntimeError("Cannot acquire preview: Camera is disconnected.")
        frames, metas = self.camera.acquire_frames(num_frames=1)
        meta = metas[0] if metas else {}
        return frames[0], meta

    def acquire_and_save_step(
        self,
        experiment_name: str,
        step_index: int,
        param_name: str,
        param_value: float,
        num_frames: int
    ) -> tuple[Path, np.ndarray]:
        """
        Acquire N frames at a given scan step and persist them to an HDF5 file.
        Output format: <storage_dir>/<experiment_name>_step_<step_index:04d>.h5
        
        Returns:
            filepath: Path to the generated HDF5 file.
            latest_frame: 2D numpy array (uint16) of the last acquired frame for live display.
        """
        if not self.camera.is_connected:
            raise RuntimeError("Cannot execute scan step: Camera is disconnected.")

        images, _ = self.camera.acquire_frames(num_frames=num_frames)
        sensor_info = self.camera.get_sensor_info()
        exposure_s = self.camera.get_exposure_time()

        timestamp_str = datetime.now(timezone.utc).isoformat()
        clean_exp = experiment_name.strip().replace(" ", "_")
        filename = f"{clean_exp}_step_{step_index:04d}.h5"
        filepath = self.storage_dir / filename

        with h5py.File(filepath, "w") as h5f:
            dset = h5f.create_dataset(
                "images",
                data=images,
                dtype="uint16",
                chunks=(1, images.shape[1], images.shape[2]),
                compression="gzip",
                compression_opts=1
            )
            # Dataset descriptive metadata
            dset.attrs["units"] = "16-bit digital counts (ADU)"
            dset.attrs["physical_units"] = "16-bit digital counts (ADU)"
            dset.attrs["dimension_order"] = "[frame, y, x]"
            dset.attrs["data_dimension_ordering"] = "[frame_index, sensor_height_y, sensor_width_x]"
            dset.attrs["description"] = "Raw 16-bit sCMOS image stack recorded at this scan step"

            # File-level descriptive metadata (includes legacy keys for MATLAB pipeline compatibility)
            h5f.attrs["experiment_name"] = clean_exp
            h5f.attrs["experiment_identifier"] = clean_exp
            h5f.attrs["step_index"] = int(step_index)
            h5f.attrs["scan_step_index"] = int(step_index)
            h5f.attrs["scan_parameter_name"] = str(param_name)
            h5f.attrs["scan_parameter_display_name"] = str(param_name)
            h5f.attrs["scan_parameter_value"] = float(param_value)
            h5f.attrs["scan_parameter_setpoint_value"] = float(param_value)
            h5f.attrs["exposure_time_s"] = float(exposure_s)
            h5f.attrs["exposure_duration_seconds"] = float(exposure_s)
            h5f.attrs["num_frames"] = int(num_frames)
            h5f.attrs["frame_accumulation_count"] = int(num_frames)
            h5f.attrs["timestamp_utc"] = timestamp_str
            h5f.attrs["acquisition_timestamp_utc_iso8601"] = timestamp_str
            h5f.attrs["camera_model"] = str(sensor_info.get("model", "pco.edge"))
            h5f.attrs["camera_manufacturer_and_model"] = str(sensor_info.get("model", "pco.edge"))
            h5f.attrs["camera_serial"] = str(sensor_info.get("serial_number", "UNKNOWN"))
            h5f.attrs["camera_hardware_serial_number"] = str(sensor_info.get("serial_number", "UNKNOWN"))
            h5f.attrs["sensor_width"] = int(sensor_info.get("width", images.shape[2]))
            h5f.attrs["sensor_pixel_width"] = int(sensor_info.get("width", images.shape[2]))
            h5f.attrs["sensor_height"] = int(sensor_info.get("height", images.shape[1]))
            h5f.attrs["sensor_pixel_height"] = int(sensor_info.get("height", images.shape[1]))
            h5f.attrs["roi_bounds"] = self.camera.get_roi()
            if hasattr(self.camera, "get_readout_mode"):
                h5f.attrs["readout_mode"] = self.camera.get_readout_mode().name
                h5f.attrs["readout_mode_value"] = int(self.camera.get_readout_mode().value)

        return filepath, images[-1]

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
    ) -> Iterator[tuple[int, float, Path, np.ndarray]]:
        """
        Execute a multi-step scan sequence from step 0 to num_steps - 1.
        Execute a multi-step scan sequence from start_step to num_steps - 1.

        Yields:
            tuple[int, float, Path, np.ndarray]:
                (step_index, param_setpoint_value, h5_filepath, latest_frame)
                at each completed step.
        """
        if not self.camera.is_connected:
            raise RuntimeError("Cannot execute scan: Camera is disconnected.")
        if num_steps < 1:
            raise ValueError(f"Number of steps must be at least 1, got {num_steps}.")
        if start_step < 0 or start_step >= num_steps:
            raise ValueError(f"start_step must be between 0 and {num_steps - 1}, got {start_step}.")

        for step in range(start_step, num_steps):
            if abort_check is not None and abort_check():
                break

            val = start_value + step * step_size
            if on_step_start is not None:
                on_step_start(step, val)

            filepath, latest_frame = self.acquire_and_save_step(
                experiment_name=experiment_name,
                step_index=step,
                param_name=param_name,
                param_value=val,
                num_frames=num_frames
            )
            yield step, val, filepath, latest_frame



