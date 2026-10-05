"""Compare mock synthesis with prepared frames through the real scan writer.

This is a software timing experiment only. It cannot measure camera transfer,
sensor timing, trigger response, or real-hardware FIFO behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hhg_control.drivers.base_camera import ReadoutMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from scripts.stress_camera_storage import ReplayCamera


ROI = (612, 1016, 2352, 1144)  # 1740 x 128 pixels, symmetric about sensor Y center
FRAMES_PER_STEP = 1000
STEPS = 2
EXPOSURE_S = 0.001
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "test_data"


class PausableReplay(ReplayCamera):
    """Prepared frames can wait for the HDF5 writer; a physical camera cannot."""

    @property
    def can_pause_acquisition(self) -> bool:
        """Permit writer backpressure in this source-only synthetic benchmark."""
        return True


class TimedMock(MockPcoCamera):
    """Measure time spent inside synthetic acquisition, with seconds as units."""

    def __init__(self) -> None:
        super().__init__()
        self.acquire_wall_s = 0.0
        self.acquire_cpu_s = 0.0

    def acquire_frames(self, num_frames):
        """Time frame synthesis and the mandatory mock exposure wait in seconds."""
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        try:
            return super().acquire_frames(num_frames)
        finally:
            self.acquire_wall_s += time.perf_counter() - wall_start
            self.acquire_cpu_s += time.process_time() - cpu_start


def _measure_capture_only(camera) -> dict:
    """Measure source generation for two equal acquisition counts, no saving."""
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    for _ in range(STEPS):
        count = sum(len(images) for images, _ in camera.iter_frames(FRAMES_PER_STEP, batch_size=4))
        assert count == FRAMES_PER_STEP
    return {
        "wall_s": time.perf_counter() - wall_start,
        "cpu_s": time.process_time() - cpu_start,
    }


def _measure_saved(camera, case_name: str, run_id: str, output_dir: Path) -> dict:
    """Time two complete uncompressed HDF5 steps, retaining labelled benchmark files."""
    manager = CameraScanManager(camera, output_dir)
    saved_paths = []
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    for step in range(STEPS):
        path, _ = manager.acquire_and_save_step(
            f"BENCHMARK_ONLY_simulation_overhead_{case_name}", step, "synthetic_step", float(step),
            FRAMES_PER_STEP, run_id=run_id, batch_size=4,
        )
        saved_paths.append(path)
    elapsed = time.perf_counter() - wall_start
    cpu_s = time.process_time() - cpu_start
    assert len(saved_paths) == STEPS
    total_bytes = sum(path.stat().st_size for path in saved_paths)
    return {"wall_s": elapsed, "cpu_s": cpu_s, "hdf5_bytes": total_bytes}


def main() -> None:
    """Run matched software timings at one ROI and 1 ms simulated exposure."""
    from hhg_control.safe_io import benchmark_directory
    output_dir = benchmark_directory(OUT_DIR, "simulation_overhead")
    free = shutil.disk_usage(OUT_DIR).free
    expected = FRAMES_PER_STEP * STEPS * (ROI[2] - ROI[0]) * (ROI[3] - ROI[1]) * 2
    if free < expected * 3:
        raise RuntimeError(f"Need at least {expected * 3 / 1e9:.2f} GB free; have {free / 1e9:.2f} GB.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    report = {
        "run_id": run_id,
        "roi": ROI,
        "frames_per_step": FRAMES_PER_STEP,
        "steps": STEPS,
        "exposure_s": EXPOSURE_S,
        "note": "Prepared replay has no exposure, camera SDK, or USB cost; OS write cache may affect HDF5 time.",
        "cases": {},
    }
    for name in ("mock_realistic", "prepared_replay"):
        if name == "prepared_replay":
            camera = PausableReplay(ROI[2] - ROI[0], ROI[3] - ROI[1], 4, "noise")
        else:
            camera = TimedMock()
        camera.connect()
        try:
            camera.set_exposure_time(EXPOSURE_S)
            if isinstance(camera, TimedMock):
                camera.set_readout_mode(ReadoutMode.GLOBAL_SHUTTER)
                camera.set_roi(ROI)
            capture = _measure_capture_only(camera)
            if isinstance(camera, TimedMock):
                camera.acquire_wall_s = camera.acquire_cpu_s = 0.0
            else:
                camera.acquire_s = 0.0
            saved = _measure_saved(camera, name, run_id, output_dir)
            if isinstance(camera, TimedMock):
                saved["source_acquire_wall_s"] = camera.acquire_wall_s
                saved["source_acquire_cpu_s"] = camera.acquire_cpu_s
            else:
                saved["source_acquire_wall_s"] = camera.acquire_s
            report["cases"][name] = {"capture_only": capture, "saved_scan": saved}
            print(f"{name}: capture {capture['wall_s']:.3f}s, saved {saved['wall_s']:.3f}s", flush=True)
        finally:
            camera.close()
    report_path = output_dir / f"BENCHMARK_ONLY_simulation_overhead_{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {report_path}", flush=True)


if __name__ == "__main__":
    main()
