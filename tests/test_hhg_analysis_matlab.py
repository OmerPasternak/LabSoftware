"""Optional real-MATLAB offline test using the current camera file schema."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import h5py
import numpy as np
import pytest


def write_current_file(path, frames, *, complete=True, count=None, step=0, value=26.7, camera="synthetic"):
    """Write the acquisition schema, including one metadata record per frame."""
    with h5py.File(path, "w") as f:
        dset = f.create_dataset("images", data=frames, chunks=(1, *frames.shape[1:]))
        dset.attrs["dimension_order"] = "[frame, y, x]"
        dset.attrs["data_dimension_ordering"] = "frame index, sensor y, sensor x"
        records = [json.dumps({"frame_id": n, "timestamp_source": "synthetic"}) for n in range(len(frames))]
        f.create_dataset("frame_metadata/json", data=records, dtype=h5py.string_dtype("utf-8"))
        f.attrs.update(schema_version="2.0", complete=complete,
                       frames_written=len(frames) if count is None else count,
                       scan_step_index=step, scan_parameter_name="TC position (mm)",
                       scan_parameter_value=value, run_id="synthetic-run", experiment_name="offline-test",
                       exposure_time_s=0.01, roi_bounds=[0, 0, frames.shape[2], frames.shape[1]],
                       readout_mode="ROLLING_SHUTTER", camera_serial=camera)


@pytest.mark.skipif(shutil.which("matlab") is None, reason="MATLAB is not installed")
def test_current_hdf5_matlab_analysis_preserves_sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # Cross both raw-read and 256-spectrum matrix-update boundaries.
    frames = np.random.default_rng(186).integers(90, 200, size=(263, 6, 10), dtype=np.uint16)
    frames[:, :, 0] = 100  # Constant-channel Pearson must stay undefined.
    write_current_file(tmp_path / "synthetic.h5", frames)
    write_current_file(tmp_path / "incomplete.h5", frames, complete=False)
    write_current_file(tmp_path / "count_mismatch.h5", frames, count=262)
    write_current_file(tmp_path / "single.h5", frames[:1])
    write_current_file(tmp_path / "step0.h5", frames[:2], step=0)
    write_current_file(tmp_path / "step1.h5", frames[2:5], step=1, value=26.701)
    write_current_file(tmp_path / "wrong_camera.h5", frames[5:], step=2, value=26.702, camera="other")
    (tmp_path / "harmonics.msk").write_text("13 2 2 3 2\n14.5 6 3 2 2\n", encoding="utf-8")
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.glob("*.h5")}
    literal = lambda path: str(path).replace("'", "''")
    command = (f"addpath('{literal(root / 'tests/matlab')}'); "
               f"test_hhg_analysis('{literal(root)}','{literal(tmp_path)}')")
    result = subprocess.run([shutil.which("matlab"), "-batch", command], capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HHG_ANALYSIS_SYNTHETIC_CHECKS_PASSED" in result.stdout
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    assert (tmp_path / "matlab_analysis_gui.png").is_file()
