"""
Experiment Sequencer & Data Storage.
Handles step acquisitions and records multi-frame HDF5 datasets compatible with MATLAB.
"""

from datetime import datetime, timezone
from pathlib import Path
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

    def acquire_and_save_step(
        self,
        experiment_name: str,
        step_index: int,
        param_name: str,
        param_value: float,
        num_frames: int
    ) -> Path:
        """
        Acquire N frames at a given scan step and persist them to an HDF5 file.
        Output format: <storage_dir>/<experiment_name>_step_<step_index:04d>.h5
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
            dset.attrs["units"] = "counts"
            dset.attrs["dimension_order"] = "[frame, y, x]"

            h5f.attrs["experiment_name"] = clean_exp
            h5f.attrs["step_index"] = int(step_index)
            h5f.attrs["scan_parameter_name"] = str(param_name)
            h5f.attrs["scan_parameter_value"] = float(param_value)
            h5f.attrs["exposure_time_s"] = float(exposure_s)
            h5f.attrs["num_frames"] = int(num_frames)
            h5f.attrs["timestamp_utc"] = timestamp_str
            h5f.attrs["camera_model"] = str(sensor_info.get("model", "pco.edge"))
            h5f.attrs["camera_serial"] = str(sensor_info.get("serial_number", "UNKNOWN"))
            h5f.attrs["sensor_width"] = int(sensor_info.get("width", images.shape[2]))
            h5f.attrs["sensor_height"] = int(sensor_info.get("height", images.shape[1]))

        return filepath

