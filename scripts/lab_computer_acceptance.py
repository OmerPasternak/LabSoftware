# %% SETUP — helpers/configuration for the companion notebook and CLI
"""Section-by-section lab-PC acceptance checks using the existing camera stack.

Notebook: open application tests/lab_computer_acceptance.ipynb, select this checkout's .venv
interpreter, run setup, edit CONFIG, then run individual numbered cells.
IPython/Spyder console: import this module, edit CONFIG, call execute(3, CONFIG).
Each section opens/closes its own camera.
Terminal: python scripts/lab_computer_acceptance.py --section 3 --source mock
          python scripts/lab_computer_acceptance.py --section 3 --source physical
          python scripts/lab_computer_acceptance.py --section 4 --output-dir D:/CameraTests
Running the whole file without --section prints help and performs no tests.
There is deliberately no 'run all' action. Importing this module runs no tests.

Close CamWare and the camera GUI before any camera section. Review CONFIG before
selecting physical: opening the existing driver sets exposure; configuring it
sets ROI/shutter/trigger, and shutter changes may reboot the camera. No actuator,
shutter interlock, high-voltage supply, or gas controller is commanded.
Sections 1–7 use AUTO_SEQUENCE. Optional external-trigger work is section 8.
All rates use decimal MB/s; exposure is seconds; ROI bounds are zero-based,
upper-exclusive sensor pixels. Reports and acquired HDF5 files are retained.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Callable

import h5py
import numpy as np

# __file__ can be absent in an IDE cell. Start the interactive console in the
# repository root (or scripts/) so this search resolves the intended checkout.
def find_root() -> Path:
    """Locate this checkout from the file path or interactive working directory."""
    start = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / "src/hhg_control").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("Start the interactive console in the LabSoftware - Codex folder.")


ROOT = find_root()
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from hhg_control.drivers.base_camera import ReadoutMode, TriggerMode
from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import AcquisitionAborted, CameraScanManager
from scripts.benchmark_capture_pipeline import run_benchmark
from scripts.stress_camera_storage import ReplayCamera, run_case


@dataclass
class Settings:
    """Reviewable acquisition settings and provisional acceptance targets."""

    source: str = "mock"  # Change explicitly to "physical" on the lab PC.
    output_dir: Path = ROOT / "data/test_data/lab_acceptance"
    expected_serial: str | None = None  # Fill in the physical camera serial.
    usb_port_note: str = "FILL IN: controller, physical port, cable"
    roi: tuple[int, int, int, int] = (0, 1016, 2560, 1144)  # 2560 x 128, centered vertically
    exposure_s: float = 0.001
    readout: ReadoutMode = ReadoutMode.GLOBAL_SHUTTER
    target_fps: float = 455.0  # Storage load/reference only; replace with measured rate.
    camware_fps: float | None = None  # Measured with IDENTICAL settings; never invent.
    speed_fraction: float = 0.90  # Proposed minimum vs matched physical baseline.
    storage_margin: float = 1.30  # Proposed unpaced writer throughput / target load.
    batch_size: int = 4
    short_frames: tuple[int, ...] = (20, 1000)
    speed_frames: int = 10000
    repeats: int = 3
    sustained_seconds: float = 300.0  # ~61 GB at the default ROI/rate.
    max_section_raw_gb: float = 120.0  # Per-section budget, not permission to fill disk.
    run_offline_tests: bool = False
    # Section 7: empty uses the latest short scan; one path checks it; two paths
    # compare an original and its byte-for-byte copy.
    data_files: tuple[Path, ...] = ()
    # Only fill these after the manual bench checks described in section 8.
    trigger_bench_ready: bool = False
    trigger_frames: int = 100
    observed_trigger_pulses: int | None = None


CONFIG = Settings()

# Camera timing is quantized by the hardware/SDK. Accept readbacks within one
# microsecond while still reporting and saving the exact applied value.
EXPOSURE_READBACK_ABS_TOL_S = 1e-6


def require(condition: bool, message: str) -> None:
    """Stop a check with an explicit failure reason."""
    if not condition:
        raise AssertionError(message)


def json_default(value):
    """Serialize settings and reports without dropping NumPy or path values."""
    if isinstance(value, (Path, ReadoutMode, TriggerMode)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def sha256_file(path: Path) -> str:
    """Hash an entire file in bounded reads to verify an archived copy."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(32 * 2**20), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(cfg: Settings, frames: int = 0) -> Path:
    """Check settings, per-section raw-byte budget and free space before saving."""
    require(cfg.source in ("mock", "physical"), "source must be mock or physical")
    require(cfg.target_fps > 0 and cfg.batch_size > 0, "Rate and batch size must be positive")
    require(cfg.repeats > 0 and cfg.speed_frames > 0 and cfg.sustained_seconds > 0,
            "Run sizes and duration must be positive")
    require(0 < cfg.speed_fraction <= 1 and cfg.storage_margin >= 1,
            "Speed fraction must be in (0, 1]; storage margin must be >= 1")
    require(len(cfg.roi) == 4 and all(isinstance(v, int) for v in cfg.roi),
            "ROI must contain four integer sensor-pixel bounds")
    require(cfg.roi[2] > cfg.roi[0] and cfg.roi[3] > cfg.roi[1], "ROI spans must be positive")
    destination = Path(cfg.output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    raw = frames * (cfg.roi[2] - cfg.roi[0]) * (cfg.roi[3] - cfg.roi[1]) * 2
    require(raw <= cfg.max_section_raw_gb * 1e9,
            f"Section needs {raw / 1e9:.2f} GB raw; increase max_section_raw_gb deliberately")
    require(shutil.disk_usage(destination).free >= raw * 1.5 + 100_000_000,
            f"Need at least {raw * 1.5 / 1e9 + 0.1:.2f} GB free on {destination}")
    print(f"Source: {cfg.source}; destination: {destination}; planned raw data: {raw / 1e9:.2f} GB")
    return destination


def snapshot(camera) -> dict:
    """Read identity, applied pixel bounds, exposure seconds and acquisition modes."""
    return {"sensor": camera.get_sensor_info(), "roi": list(camera.get_roi()),
            "exposure_s": camera.get_exposure_time(),
            "readout": camera.get_readout_mode().name,
            "trigger": camera.get_trigger_mode().value,
            "roi_limits": camera.get_roi_limits()}


@contextmanager
def configured_camera(cfg: Settings, trigger=TriggerMode.AUTO_SEQUENCE):
    """Open the requested source, apply reviewed settings, verify them, then close."""
    if cfg.source == "physical":
        from hhg_control.drivers.pco_edge import PcoEdgeCamera
        require(cfg.expected_serial is not None, "Set expected_serial from the camera label before connecting")
        camera = PcoEdgeCamera(serial=cfg.expected_serial)
    else:
        camera = MockPcoCamera()
    try:
        camera.connect()
        identity = camera.get_sensor_info()
        if cfg.source == "physical":
            model = str(identity.get("model", "")).lower()
            serial = str(identity.get("serial_number", ""))
            require(model and "emulator" not in model and "mock" not in model,
                    "Physical source reported a missing or simulated identity")
            require(serial.upper() not in ("", "UNKNOWN", "NONE"), "Physical serial is unknown")
            if cfg.expected_serial is not None:
                require(serial == cfg.expected_serial, "Connected camera serial differs from expected_serial")
        camera.set_exposure_time(cfg.exposure_s)
        camera.set_readout_mode(cfg.readout)
        camera.set_roi(cfg.roi)
        camera.set_trigger_mode(trigger)
        actual = snapshot(camera)
        require(actual["roi"] == list(cfg.roi), f"Applied ROI differs: {actual['roi']}")
        require(
            math.isclose(
                actual["exposure_s"], cfg.exposure_s,
                rel_tol=1e-5, abs_tol=EXPOSURE_READBACK_ABS_TOL_S,
            ),
            f"Exposure readback {actual['exposure_s']!r} s differs from requested "
            f"{cfg.exposure_s!r} s",
        )
        require(actual["readout"] == cfg.readout.name, "Shutter readback differs")
        require(actual["trigger"] == trigger.value, "Trigger readback differs")
        print(json.dumps(actual, indent=2, default=json_default))
        yield camera
    finally:
        camera.close()


def inspect_file(path: Path, cfg: Settings | None = None, full_read: bool = False) -> dict:
    """Check all metadata in bounded batches; optionally hash/read every raw pixel.

    Hardware IDs must increment modulo 2**32. Host intervals describe software
    receipt, never exposure timing. File hashes establish a copy-verification
    reference; they cannot independently establish the camera's original pixels.
    """
    path = Path(path)
    require(path.suffix == ".h5", "Incomplete .partial files are not accepted as research inputs")
    intervals, sources, previous_id, previous_time = [], {}, None, None
    valid_host_times, count = True, 0
    digest = hashlib.sha256()
    metadata_digest = hashlib.sha256()
    with h5py.File(path, "r") as h5:
        attrs = h5.attrs
        require(bool(attrs["complete"]), f"Incomplete file: {path}")
        count = int(attrs["num_frames"])
        require(count > 0 and int(attrs["frames_written"]) == count, "Requested/written count differs")
        images, metadata = h5["images"], h5["frame_metadata/json"]
        require(images.dtype == np.dtype("uint16") and images.ndim == 3, "Unexpected pixel dtype/axes")
        require(images.shape[0] == count and metadata.shape == (count,), "Image/metadata count differs")
        roi = [int(v) for v in attrs["roi_bounds"]]
        require(images.shape[1:] == (roi[3] - roi[1], roi[2] - roi[0]), "ROI and array dimensions differ")
        if cfg is not None:
            require(roi == list(cfg.roi), "Saved ROI differs from settings")
            require(str(attrs["readout_mode"]) == cfg.readout.name, "Saved shutter mode differs")
            require(math.isclose(
                        float(attrs["exposure_time_s"]), cfg.exposure_s,
                        rel_tol=1e-5, abs_tol=EXPOSURE_READBACK_ABS_TOL_S,
                    ),
                    "Saved exposure differs")
            if cfg.source == "physical":
                require("emulator" not in str(attrs["camera_model"]).lower(), "File is simulated")
                if cfg.expected_serial:
                    require(str(attrs["camera_serial"]) == cfg.expected_serial, "Saved serial differs")
        for start in range(0, count, 256):
            for raw in metadata[start:start + 256]:
                encoded = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                metadata_digest.update(len(encoded).to_bytes(8, "little"))
                metadata_digest.update(encoded)
                item = json.loads(raw)
                frame_id = int(item["frame_id"])
                require(previous_id is None or frame_id == (previous_id + 1) % 2**32,
                        f"Missing, duplicate or reordered frame ID near {frame_id}")
                previous_id = frame_id
                source = item.get("timestamp_source", "unknown")
                sources[source] = sources.get(source, 0) + 1
                timestamp = item.get("host_frame_read_monotonic_ns")
                if timestamp is None:
                    valid_host_times = False
                elif previous_time is not None:
                    delta = int(timestamp) - previous_time
                    require(delta > 0, "Host read times are not increasing")
                    intervals.append(delta / 1e6)
                previous_time = int(timestamp) if timestamp is not None else None
        sample_stats = []
        for index in sorted({0, count // 2, count - 1}):
            frame = images[index]
            sample_stats.append({"frame": index, "min_adu": int(frame.min()),
                                 "max_adu": int(frame.max()), "mean_adu": float(frame.mean()),
                                 "saturated_fraction": float(np.mean(frame == 65535))})
        if full_read:
            # Bound reads to ~32 MiB even for a full-sensor ROI.
            block = max(1, (32 * 2**20) // (images.shape[1] * images.shape[2] * 2))
            for start in range(0, count, block):
                digest.update(images[start:start + block].tobytes(order="C"))
        result = {"file": str(path.resolve()), "frames": count, "complete": True,
                  "consecutive_ids": True, "shape_frame_y_x": list(images.shape),
                  "camera_model": str(attrs["camera_model"]), "camera_serial": str(attrs["camera_serial"]),
                  "roi_bounds": roi, "exposure_s": float(attrs["exposure_time_s"]),
                  "readout_mode": str(attrs["readout_mode"]),
                  "schema_version": str(attrs["schema_version"]),
                  "compression": images.compression or "none",
                  "trigger_mode": str(attrs["trigger_mode"]), "timestamp_sources": sources,
                  "writer_queue_batches": (int(attrs["writer_queue_batches"])
                                             if "writer_queue_batches" in attrs else None),
                  "writer_queue_capacity_frames": (int(attrs["writer_queue_capacity_frames"])
                                                     if "writer_queue_capacity_frames" in attrs else None),
                  "writer_queue_capacity_MiB": (int(attrs["writer_queue_capacity_bytes"]) / 2**20
                                                 if "writer_queue_capacity_bytes" in attrs else None),
                  "writer_queue_max_batches_used": (int(attrs["writer_queue_max_batches_used"])
                                                     if "writer_queue_max_batches_used" in attrs else None),
                  "sample_statistics": sample_stats, "full_pixel_read": full_read,
                  "pixel_sha256": digest.hexdigest() if full_read else None,
                  "metadata_sha256": metadata_digest.hexdigest()}
    if valid_host_times and intervals:
        result.update(host_read_fps=1000 * len(intervals) / sum(intervals),
                      host_interval_median_ms=float(np.median(intervals)),
                      host_interval_p99_ms=float(np.percentile(intervals, 99)))
    else:
        result["host_read_fps"] = None
    return result


def saved_run(camera, cfg: Settings, label: str, frames: int,
              manager_class=CameraScanManager, abort_check=None) -> dict:
    """Time production capture/file close, then fsync, and verify the saved step."""
    manager = manager_class(camera, cfg.output_dir)
    start, cpu_start = time.perf_counter(), time.process_time()
    path, _ = manager.acquire_and_save_step(
        label, 0, "Acceptance setpoint (metadata only)", 0.0, frames,
        batch_size=cfg.batch_size, abort_check=abort_check,
    )
    close_s = time.perf_counter() - start
    cpu_s = time.process_time() - cpu_start
    fsync_started = time.perf_counter()
    with path.open("r+b", buffering=0) as handle:
        os.fsync(handle.fileno())
    fsync_s = time.perf_counter() - fsync_started
    synced_s = time.perf_counter() - start
    result = inspect_file(path, cfg)
    result.update(total_close_s=close_s, synced_s=synced_s,
                  final_os_fsync_s=fsync_s,
                  pipeline_stage_timing_s=manager.last_step_timing,
                  total_fps=frames / close_s, synced_fps=frames / synced_s,
                  file_MB_per_s=path.stat().st_size / 1e6 / close_s,
                  cpu_s=cpu_s, average_cpu_cores=cpu_s / close_s)
    return result


def capture_only(camera, cfg: Settings, frames: int) -> dict:
    """Read bounded driver batches without saving; check frame IDs and dimensions."""
    count, previous, host_times = 0, None, []
    start = time.perf_counter()
    iterator = camera.iter_frames(frames, batch_size=cfg.batch_size)
    try:
        for images, metadata in iterator:
            require(images.shape[1:] == (cfg.roi[3] - cfg.roi[1], cfg.roi[2] - cfg.roi[0])
                    and images.dtype == np.uint16, "Capture dimensions/dtype differ")
            require(len(images) == len(metadata), "Capture image/metadata count differs")
            for item in metadata:
                frame_id = int(item["frame_id"])
                require(previous is None or frame_id == (previous + 1) % 2**32, "Capture frame-ID gap")
                previous = frame_id
                if item.get("host_frame_read_monotonic_ns") is not None:
                    host_times.append(int(item["host_frame_read_monotonic_ns"]))
            count += len(images)
    finally:
        iterator.close()
    elapsed = time.perf_counter() - start
    require(count == frames, "Capture returned fewer frames than requested")
    host_fps = None
    if len(host_times) == frames and frames > 1:
        delta = np.diff(np.asarray(host_times, dtype=np.int64))
        require(np.all(delta > 0), "Capture host times are not increasing")
        host_fps = (frames - 1) * 1e9 / (host_times[-1] - host_times[0])
    return {"frames": frames, "elapsed_s": elapsed, "total_fps": frames / elapsed,
            "host_read_fps": host_fps, "consecutive_ids": True}


def section_1(cfg: Settings) -> dict:
    """Record the PC/environment, run offline tests and benchmark the chosen drive."""
    destination = prepare(cfg, cfg.speed_frames)
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
        )
        status = subprocess.run(
            ["git", "status", "--short", "--branch"], cwd=ROOT, capture_output=True, text=True,
        )
        revision_text = revision.stdout.strip() or "unavailable"
        status_text = status.stdout
        git_note = None
    except FileNotFoundError:
        # GitHub Desktop can manage the checkout with its bundled Git even when
        # git.exe is not available to the notebook kernel through Windows PATH.
        revision_text = "unavailable: git executable is not on this kernel's PATH"
        status_text = "unavailable"
        git_note = "Git metadata was skipped; storage diagnostics continued."
    versions = {}
    for package in ("numpy", "h5py", "PyQt6", "pco", "pytest"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    result = {"python": sys.executable, "platform": platform.platform(),
              "revision": revision_text, "git_status": status_text,
              "git_note": git_note,
              "packages": versions, "usb_port_note": cfg.usb_port_note,
              "free_GB": shutil.disk_usage(destination).free / 1e9}
    if cfg.run_offline_tests:
        # Separate unique temp directory; pytest may delete its basetemp.
        temp = destination / ("pytest_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f"))
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(temp)],
                             cwd=ROOT, capture_output=True, text=True)
        log_path = destination / (temp.name + ".log")
        log_path.write_text(run.stdout + run.stderr, encoding="utf-8")
        result["offline_tests"] = {
            "passed": run.returncode == 0,
            "exit_code": run.returncode,
            "log": str(log_path),
            "summary": run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip(),
        }
        if run.returncode:
            print(f"Offline tests failed; see {log_path}. Continuing with independent storage checks.")
    else:
        result["offline_tests"] = {"passed": None, "status": "SKIPPED"}
    width, height = cfg.roi[2] - cfg.roi[0], cfg.roi[3] - cfg.roi[1]
    # Replay can wait; unpaced throughput estimates writer capacity rather than
    # modeling a camera FIFO. Benchmark files are labelled and retained.
    class PausableReplay(ReplayCamera):
        @property
        def can_pause_acquisition(self):
            """Allow this synthetic writer-capacity test to wait for storage."""
            return True
    replay = PausableReplay(width, height, cfg.batch_size, "noise")
    replay.connect()
    try:
        result["unpaced_writer"] = run_case("application", replay, destination, cfg.speed_frames, cfg.batch_size)
    finally:
        replay.close()
    result["paced_writer"] = run_benchmark(destination, frames=cfg.speed_frames, width=width,
                                           height=height, fps=cfg.target_fps, batch_size=cfg.batch_size)
    required_mb_s = width * height * 2 * cfg.target_fps / 1e6
    result["required_raw_MB_per_s"] = required_mb_s
    result["provisional_writer_margin_met"] = result["unpaced_writer"]["raw_MB_per_s"] >= required_mb_s * cfg.storage_margin
    result["readiness_passed"] = (
        result["offline_tests"]["passed"] is True
        and result["provisional_writer_margin_met"]
        and result["paced_writer"]["verified_consecutive_frame_ids"]
        and result["paced_writer"]["verified_image_frame_markers"]
    )
    result["storage_note"] = "Synthetic results; OS/device caching and replay waiting limit hardware conclusions."
    return result


def section_2(cfg: Settings) -> dict:
    """Verify settings across two separate connections; no frames are saved."""
    prepare(cfg)
    readbacks = []
    for _ in range(2):
        with configured_camera(cfg) as camera:
            readbacks.append(snapshot(camera))
    require(readbacks[0] == readbacks[1], "Readback/identity changed across reconnect")
    return {"reconnect_readbacks": readbacks}


def section_3(cfg: Settings) -> dict:
    """Acquire short production scans and validate exact frame/metadata counts."""
    require(cfg.short_frames and all(n > 0 for n in cfg.short_frames), "Short-run sizes must be positive")
    prepare(cfg, sum(cfg.short_frames))
    runs = []
    with configured_camera(cfg) as camera:
        for frames in cfg.short_frames:
            runs.append(saved_run(camera, cfg, "acceptance_short", frames))
    return {"runs": runs}


def show_section_3_review_images(report: dict):
    """Display the first, middle and last saved frame from every Section 3 run.

    The three panels in each row share one percentile-based ADU scale so that
    brightness changes remain visible. Only the review frames are read from
    disk; the full acquisitions are not loaded into memory.
    """
    import matplotlib.pyplot as plt

    result = report.get("result", report)
    runs = result.get("runs", [])
    require(runs, "Section 3 produced no completed runs to display")
    figure, axes = plt.subplots(
        len(runs), 3, figsize=(16, max(3.2, 3.2 * len(runs))),
        squeeze=False, constrained_layout=True,
    )
    labels = ("first", "middle", "last")
    for row, run in enumerate(runs):
        path = Path(run["file"])
        with h5py.File(path, "r") as h5:
            images = h5["images"]
            count = int(images.shape[0])
            require(count > 0, f"No images found in {path}")
            indices = (0, count // 2, count - 1)
            review_frames = [images[index] for index in indices]

        combined = np.stack(review_frames)
        low, high = np.percentile(combined, (0.5, 99.5))
        if high <= low:
            low, high = float(combined.min()), float(combined.max())
        if high <= low:
            high = low + 1.0
        last_image = None
        for column, (label, index, frame) in enumerate(zip(labels, indices, review_frames)):
            axis = axes[row, column]
            last_image = axis.imshow(
                frame, cmap="gray", vmin=low, vmax=high, aspect="auto",
                interpolation="nearest",
            )
            saturated = 100.0 * float(np.mean(frame == np.iinfo(np.uint16).max))
            axis.set_title(
                f"{label}: frame {index + 1}/{count}\n"
                f"min {int(frame.min())}, max {int(frame.max())}, "
                f"mean {float(frame.mean()):.1f} ADU, sat {saturated:.3f}%"
            )
            axis.set_xlabel("ROI x pixel")
            axis.set_ylabel("ROI y pixel")
        figure.colorbar(last_image, ax=list(axes[row]), label="ADU (shared scale for this run)")
        print(f"Review row {row + 1}: {path} — frames {[index + 1 for index in indices]}")
    figure.suptitle(
        "Section 3 visual review — first, middle and last saved frames\n"
        "Each row shares an ADU scale; the thin 2560×128 ROI is stretched vertically for inspection."
    )
    plt.show()
    return figure


def section_4(cfg: Settings) -> dict:
    """Compare repeated capture-only and saved scans with a matched manual baseline."""
    prepare(cfg, cfg.speed_frames * cfg.repeats)
    comparisons = []
    with configured_camera(cfg) as camera:
        for _ in range(cfg.repeats):
            capture = capture_only(camera, cfg, cfg.speed_frames)
            saved = saved_run(camera, cfg, "acceptance_speed", cfg.speed_frames)
            comparisons.append({"capture_only": capture, "saved": saved})
    capture_median = float(np.median([item["capture_only"]["total_fps"] for item in comparisons]))
    saved_median = float(np.median([item["saved"]["total_fps"] for item in comparisons]))
    result = {"runs": comparisons, "capture_median_fps": capture_median,
              "saved_median_fps": saved_median, "saved_over_capture": saved_median / capture_median}
    if cfg.source == "physical" and cfg.camware_fps is not None:
        require(cfg.camware_fps > 0, "CamWare baseline must be positive")
        result["camware_fps"] = cfg.camware_fps
        result["provisional_capture_target_met"] = capture_median >= cfg.speed_fraction * cfg.camware_fps
        result["saved_over_camware_capture"] = saved_median / cfg.camware_fps
    else:
        result["provisional_capture_target_met"] = None
        result["pending"] = "Matched physical CamWare baseline needed for hardware speed acceptance."
    return result


def section_5(cfg: Settings) -> dict:
    """Run repeated 10k-style steps and one continuous recording sized by target fps."""
    long_frames = math.ceil(cfg.sustained_seconds * cfg.target_fps)
    prepare(cfg, cfg.speed_frames * cfg.repeats + long_frames)
    runs = []
    with configured_camera(cfg) as camera:
        for _ in range(cfg.repeats):
            runs.append(saved_run(camera, cfg, "acceptance_repeat", cfg.speed_frames))
        continuous = saved_run(camera, cfg, "acceptance_continuous", long_frames)
    # A fixed frame count gives an estimated duration, not a guaranteed one.
    observed = continuous.get("host_read_fps")
    observed_duration = (long_frames - 1) / observed if observed else continuous["total_close_s"]
    return {"repeat_runs": runs, "continuous_run": continuous,
            "requested_duration_s": cfg.sustained_seconds,
            "observed_acquisition_span_s": observed_duration,
            "duration_target_met": observed_duration >= cfg.sustained_seconds * 0.95,
            "pending": "Review memory/disk traces and GUI behavior manually; see cell comments."}


def section_6(cfg: Settings) -> dict:
    """Exercise abort, writer failure and recovery on a mock, irrespective of source."""
    # Fault injection must not alter or slow the physical camera path.
    mock_cfg = replace(cfg, source="mock", expected_serial=None)
    destination = prepare(mock_cfg, 40)
    before = set(destination.glob("*.h5.partial"))
    outcomes = []
    with configured_camera(mock_cfg) as camera:
        manager = CameraScanManager(camera, destination)
        try:
            manager.acquire_and_save_step("acceptance_abort", 0, "mock", 0.0, 20,
                                          abort_check=lambda: True)
        except AcquisitionAborted:
            outcomes.append("abort reported")
        else:
            raise AssertionError("Abort unexpectedly completed")
        require(manager.state == "IDLE", "Manager stayed busy after abort")

        class FailedWriter(CameraScanManager):
            def _write_batch(self, *args, **kwargs):
                """Inject an isolated disk-write error without touching the drive."""
                raise OSError("acceptance test: injected writer error")

        failing = FailedWriter(camera, destination)
        try:
            failing.acquire_and_save_step("acceptance_writer_error", 0, "mock", 0.0, 8)
        except RuntimeError as exc:
            require("writer" in str(exc).lower(), "Unexpected recovery failure")
            outcomes.append("writer failure reported")
        else:
            raise AssertionError("Injected writer error unexpectedly completed")
        require(failing.state == "IDLE", "Manager stayed busy after writer failure")
        recovery = saved_run(camera, mock_cfg, "acceptance_recovery", 8)
    partials = sorted(set(destination.glob("*.h5.partial")) - before)
    require(len(partials) == 2, "Expected two retained incomplete files")
    for path in partials:
        with h5py.File(path, "r") as h5:
            require(not bool(h5.attrs["complete"]), "Failed step marked complete")
        require(not path.with_suffix("").exists(), "Failed step published as .h5")
    return {"fault_source": "mock only", "outcomes": outcomes,
            "retained_partial_files": partials, "recovery": recovery,
            "pending": "GUI Stop, low-space, disconnect, restart and timeout cases require the manual checks."}


def section_7(cfg: Settings) -> dict:
    """Verify one scan in Python and optionally compare its byte-for-byte copy."""
    prepare(cfg)
    if cfg.data_files:
        require(1 <= len(cfg.data_files) <= 2,
                "Set CONFIG.data_files to one scan or (original, copy)")
        paths = [Path(path) for path in cfg.data_files]
    else:
        candidates = list(Path(cfg.output_dir).glob("acceptance_short_*.h5"))
        require(candidates, "Run section 3 first, or set CONFIG.data_files")
        paths = [max(candidates, key=lambda path: path.stat().st_mtime_ns)]
    files = []
    for path in paths:
        result = inspect_file(path, full_read=True)
        result["file_sha256"] = sha256_file(path)
        files.append(result)
    comparison = None
    if len(files) == 2:
        fields = ("file_sha256", "pixel_sha256", "metadata_sha256", "frames",
                  "shape_frame_y_x", "roi_bounds", "camera_model", "camera_serial",
                  "readout_mode", "trigger_mode", "exposure_s", "schema_version", "compression")
        differences = [field for field in fields if files[0][field] != files[1][field]]
        require(not differences, f"Original and copy differ in: {', '.join(differences)}")
        comparison = {"original": str(paths[0]), "copy": str(paths[1]),
                      "byte_for_byte_match": True, "pixel_and_metadata_match": True}
    return {"files": files, "copy_comparison": comparison,
            "note": "Python checks raw ADU data and metadata. MATLAB interpretation and physical calibration are separate checks."}


def section_8(cfg: Settings) -> dict:
    """Optionally acquire triggered frames; count comparison requires bench evidence."""
    if not cfg.trigger_bench_ready:
        return {"status": "DEFERRED", "reason": "Trigger bench not ready; sections 1–7 remain usable."}
    require(cfg.source == "physical", "Trigger accounting requires the physical camera")
    require(cfg.trigger_frames > 0, "trigger_frames must be positive")
    prepare(cfg, cfg.trigger_frames)
    try:
        with configured_camera(cfg, TriggerMode.EXTERNAL_EXPOSURE_START) as camera:
            try:
                result = saved_run(camera, cfg, "acceptance_trigger", cfg.trigger_frames)
            finally:
                camera.set_trigger_mode(TriggerMode.AUTO_SEQUENCE)
    finally:
        print("Trigger run finished/failed. Disable the bench pulse source before further setup.")
    require(result["trigger_mode"] == TriggerMode.EXTERNAL_EXPOSURE_START.value,
            "Saved file does not identify external triggering")
    result["observed_trigger_pulses"] = cfg.observed_trigger_pulses
    result["count_agreement"] = (cfg.observed_trigger_pulses == result["frames"]
                                 if cfg.observed_trigger_pulses is not None else None)
    result["pending"] = "Scope exposure/busy timing and independent pulse counts are required for shot synchronization."
    return result


def _saved_run_metrics(run: dict) -> dict:
    """Select decision-relevant saved-run measurements with explicit units."""
    return {
        "file": run.get("file"),
        "frames": run.get("frames"),
        "complete": run.get("complete"),
        "consecutive_frame_ids": run.get("consecutive_ids"),
        "shape_frame_y_x": run.get("shape_frame_y_x"),
        "total_fps": run.get("total_fps"),
        "host_read_fps": run.get("host_read_fps"),
        "synced_fps": run.get("synced_fps"),
        "file_MB_per_s": run.get("file_MB_per_s"),
        "host_interval_median_ms": run.get("host_interval_median_ms"),
        "host_interval_p99_ms": run.get("host_interval_p99_ms"),
        "total_close_s": run.get("total_close_s"),
        "durable_sync_s": run.get("synced_s"),
        "final_os_fsync_s": run.get("final_os_fsync_s"),
        "pipeline_stage_timing_s": run.get("pipeline_stage_timing_s"),
        "average_cpu_cores": run.get("average_cpu_cores"),
        "writer_queue_batches": run.get("writer_queue_batches"),
        "writer_queue_capacity_frames": run.get("writer_queue_capacity_frames"),
        "writer_queue_capacity_MiB": run.get("writer_queue_capacity_MiB"),
        "writer_queue_max_batches_used": run.get("writer_queue_max_batches_used"),
        "sample_statistics": run.get("sample_statistics"),
    }


def important_metrics(number: int, report: dict, cfg: Settings, report_path: Path) -> dict:
    """Build a concise end-of-section summary without overstating acceptance."""
    width, height = cfg.roi[2] - cfg.roi[0], cfg.roi[3] - cfg.roi[1]
    summary = {
        "section": number,
        "execution_status": report["status"],
        "source": cfg.source,
        "report_file": str(report_path.resolve()),
        "output_directory": str(Path(cfg.output_dir).resolve()),
        "configured_roi": {"bounds": list(cfg.roi), "width_px": width, "height_px": height},
        "configured_exposure_ms": cfg.exposure_s * 1000,
        "configured_target_fps": cfg.target_fps,
        "batch_size_frames": cfg.batch_size,
    }
    if report["status"] == "FAILED":
        summary["decision"] = "FAILED"
        summary["error"] = report.get("error")
        return summary

    result = report["result"]
    if number == 1:
        unpaced = result["unpaced_writer"]
        paced = result["paced_writer"]
        required = result["required_raw_MB_per_s"]
        margin_target = required * cfg.storage_margin
        summary.update({
            "decision": "READINESS_PASS" if result["readiness_passed"] else "NOT_PASSED",
            "offline_tests": result["offline_tests"],
            "free_GB_before_benchmarks": result["free_GB"],
            "required_raw_MB_per_s": required,
            "writer_target_with_margin_MB_per_s": margin_target,
            "unpaced_writer_raw_MB_per_s": unpaced["raw_MB_per_s"],
            "unpaced_writer_headroom_ratio": unpaced["raw_MB_per_s"] / required,
            "writer_margin_met": result["provisional_writer_margin_met"],
            "paced_pipeline_fps": paced["pipeline_fps"],
            "paced_durable_fps": paced["durable_fps"],
            "paced_max_schedule_late_ms": paced["max_schedule_late_s"] * 1000,
            "paced_wait_s": paced["pace_wait_s"],
            "paced_stage_timing_s": paced["stage_timing_s"],
            "paced_writer_queue_usage": {
                "max_batches_used": paced["writer_queue_max_batches_used"],
                "capacity_batches": paced["writer_queue_capacity_batches"],
                "full_wait_events": paced["producer_queue_full_wait_events"],
            },
            "paced_bottleneck_diagnosis": paced["bottleneck_diagnosis"],
            "paced_consecutive_frame_ids": paced["verified_consecutive_frame_ids"],
            "paced_image_markers_verified": paced["verified_image_frame_markers"],
            "limitation": result["storage_note"],
        })
    elif number == 2:
        first, second = result["reconnect_readbacks"]
        summary.update({
            "decision": "AUTOMATED_CHECKS_PASS",
            "model": first["sensor"].get("model"),
            "camera_serial": first["sensor"].get("serial_number"),
            "applied_roi": first["roi"],
            "applied_exposure_ms": first["exposure_s"] * 1000,
            "exposure_difference_us": (first["exposure_s"] - cfg.exposure_s) * 1e6,
            "readout_mode": first["readout"],
            "trigger_mode": first["trigger"],
            "two_reconnect_readbacks_identical": first == second,
            "roi_limits": first["roi_limits"],
        })
    elif number == 3:
        summary.update({
            "decision": "AUTOMATED_CHECKS_PASS",
            "runs": [_saved_run_metrics(run) for run in result["runs"]],
            "manual_review_required": "Inspect first/middle/last images for scene, orientation and saturation.",
            "limitation": "Short-run integrity does not establish sustained target-rate operation.",
        })
    elif number == 4:
        summary.update({
            "decision": (
                "PROVISIONAL_PASS" if result["provisional_capture_target_met"] is True
                else "NOT_PASSED" if result["provisional_capture_target_met"] is False
                else "PENDING_BASELINE"
            ),
            "capture_only_median_fps": result["capture_median_fps"],
            "saved_median_fps": result["saved_median_fps"],
            "saved_over_capture_ratio": result["saved_over_capture"],
            "camware_fps": result.get("camware_fps"),
            "capture_target_fraction": cfg.speed_fraction,
            "capture_target_met": result["provisional_capture_target_met"],
            "runs": [
                {
                    "capture_only": item["capture_only"],
                    "saved": _saved_run_metrics(item["saved"]),
                }
                for item in result["runs"]
            ],
            "pending": result.get("pending"),
        })
    elif number == 5:
        continuous = result["continuous_run"]
        summary.update({
            "decision": "AUTOMATED_CHECKS_PASS" if result["duration_target_met"] else "NOT_PASSED",
            "repeat_runs": [_saved_run_metrics(run) for run in result["repeat_runs"]],
            "continuous_run": _saved_run_metrics(continuous),
            "requested_duration_s": result["requested_duration_s"],
            "observed_acquisition_span_s": result["observed_acquisition_span_s"],
            "duration_target_met": result["duration_target_met"],
            "pending_manual_review": result["pending"],
        })
    elif number == 6:
        summary.update({
            "decision": "AUTOMATED_CHECKS_PASS",
            "fault_source": result["fault_source"],
            "verified_outcomes": result["outcomes"],
            "retained_partial_file_count": len(result["retained_partial_files"]),
            "retained_partial_files": [str(path) for path in result["retained_partial_files"]],
            "recovery_run": _saved_run_metrics(result["recovery"]),
            "pending_manual_review": result["pending"],
        })
    elif number == 7:
        summary.update({
            "decision": "AUTOMATED_CHECKS_PASS",
            "files": [
                {
                    "file": item["file"],
                    "frames": item["frames"],
                    "shape_frame_y_x": item["shape_frame_y_x"],
                    "consecutive_frame_ids": item["consecutive_ids"],
                    "full_pixel_read": item["full_pixel_read"],
                    "file_sha256": item["file_sha256"],
                    "pixel_sha256": item["pixel_sha256"],
                    "metadata_sha256": item["metadata_sha256"],
                }
                for item in result["files"]
            ],
            "copy_comparison": result["copy_comparison"],
            "limitation": result["note"],
        })
    elif number == 8:
        if result.get("status") == "DEFERRED":
            summary.update({"decision": "DEFERRED", "reason": result["reason"]})
        else:
            summary.update({
                "decision": (
                    "COUNT_CHECK_PASS" if result["count_agreement"] is True
                    else "PENDING_OR_NOT_PASSED"
                ),
                "trigger_run": _saved_run_metrics(result),
                "trigger_mode": result["trigger_mode"],
                "observed_trigger_pulses": result["observed_trigger_pulses"],
                "saved_frames": result["frames"],
                "trigger_count_agreement": result["count_agreement"],
                "pending_manual_review": result["pending"],
            })
    return summary


def _print_important_metrics(summary: dict) -> None:
    """Print one recognizable final block for notebook and terminal users."""
    print(f"\n=== IMPORTANT METRICS — SECTION {summary['section']} ===")
    print(json.dumps(summary, indent=2, default=json_default))


def execute(number: int, cfg: Settings = CONFIG) -> dict:
    """Run exactly one section and retain a JSON report even when it fails."""
    destination = prepare(cfg)
    report = {"section": number, "utc": datetime.now(timezone.utc).isoformat(),
              "source": cfg.source, "settings": asdict(cfg),
              "hardware_acceptance": "not automatically certified; review section benchmarks"}
    path = destination / f"section_{number}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}.json"
    try:
        report["result"] = SECTIONS[number](cfg)
        report["status"] = "EXECUTED"  # Pending/provisional checks remain explicit in result.
    except BaseException as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        report["important_metrics"] = important_metrics(number, report, cfg, path)
        path.write_text(json.dumps(report, indent=2, default=json_default), encoding="utf-8")
        _print_important_metrics(report["important_metrics"])
        raise
    report["important_metrics"] = important_metrics(number, report, cfg, path)
    path.write_text(json.dumps(report, indent=2, default=json_default), encoding="utf-8")
    print(json.dumps(report["result"], indent=2, default=json_default))
    _print_important_metrics(report["important_metrics"])
    return report


SECTIONS: dict[int, Callable] = {1: section_1, 2: section_2, 3: section_3, 4: section_4,
                               5: section_5, 6: section_6, 7: section_7, 8: section_8}
_INTERACTIVE = hasattr(sys, "ps1") or "ipykernel" in sys.modules
_SELECTED = None
if __name__ == "__main__" and not _INTERACTIVE:
    # Windows redirected output can use cp1252 even though checkout paths contain
    # Hebrew. Keep the stream's encoding, but never fail a test while printing a path.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--section", type=int, choices=range(1, 9))
    parser.add_argument("--source", choices=("mock", "physical"), default=CONFIG.source)
    parser.add_argument("--output-dir", type=Path, default=CONFIG.output_dir)
    parser.add_argument("--skip-offline-tests", action="store_true", help="Compatibility option; tests are skipped by default")
    parser.add_argument("--run-offline-tests", action="store_true", help="Explicitly launch the offline test suite")
    args = parser.parse_args()
    _SELECTED = args.section
    CONFIG.source, CONFIG.output_dir = args.source, args.output_dir
    CONFIG.run_offline_tests = args.run_offline_tests
    if _SELECTED is None:
        parser.print_help()


def selected(number: int) -> bool:
    """Run a selected terminal section, or the currently executed IDE cell."""
    return __name__ == "__main__" and _SELECTED == number


# %% 1 — Readiness and storage (no physical camera is opened)
# 1. Set output_dir to the FINAL acquisition drive; fill usb_port_note.
# 2. Run this section with the final interpreter and checkout. Review pytest log.
# 3. Repeat writer measurements after warm-up and with normal background apps.
# 4. Watch Task Manager > Performance > Disk during a longer write. Short results
#    can fit in OS/device caches; fsync timings still depend on the storage stack.
# 5. Review paced_stage_timing_s and paced_bottleneck_diagnosis. Timed stages
#    overlap because camera/replay production and HDF5 writing run concurrently.
# Benchmarks: all offline tests pass; sufficient free space; proposed unpaced
# capacity >= 1.30 * (ROI width * height * 2 bytes * required fps). At 2560x128,
# 455 fps means 298.19 MB/s raw and a proposed 387.65 MB/s writer-capacity target.
# A GUI layout test may fail separately; its failure is recorded and storage
# still runs. The storage source is synthetic, so it cannot test camera or USB.
# Paced replay: every ID/image marker must agree, no error; review max lateness.
# Replay waits for scheduled delivery and cannot prove physical FIFO tolerance.
if selected(1):
    REPORT_1 = execute(1, CONFIG)


# %% 2 — Physical identity, settings and reconnect
# 1. Close CamWare/GUI. Review exposure_s, roi, readout; set source="physical".
# 2. Global Shutter is mode value 2. expected_serial is the camera's unique
#    hardware serial, not a shutter value. Fill it from the camera label before connecting.
# 3. In GUI separately check Rolling -> Global -> Rolling -> intended mode.
#    Camera reboot may take seconds. Confirm final ROI/exposure/trigger readback.
# 4. Close/reopen the GUI; repeat once. Do not run GUI and script simultaneously.
# Benchmarks: exact serial/ROI/modes; exposure agrees to SDK precision; two
# script reconnects agree; GUI has no stuck controls/crash and shows final mode.
if selected(2):
    REPORT_2 = execute(2, CONFIG)


# %% 3 — Short acquisition: 20 frames, then 1,000
# 1. Complete section 2 first, then run this cell.
# 2. The notebook displays first/middle/last images from both saved runs.
# 3. Check scene, orientation, ROI coverage, corruption and saturation values.
# Benchmarks: complete files, exact counts, uint16 [frame,y,x], all IDs
# consecutive, no recorder/FIFO/writer errors. Appearance must match the scene;
# dark/light statistics are observations, not a calibrated noise specification.
if selected(3):
    REPORT_3 = execute(3, CONFIG)
    show_section_3_review_images(REPORT_3)


# %% 4 — Camera download vs saving speed (3 x 10,000 by default)
# 1. Close our GUI. In CamWare Camera Properties select Global Shutter, Auto
#    Sequence, 1 ms exposure, and a centered 2560x128 ROI. CamWare's one-based
#    inclusive bounds may read Left 1, Right 2560, Top 1017, Bottom 1144.
# 2. Acquisition > Memory Allocation Dialog: start with 1000 frames (~0.66 GB
#    raw). Use 10000 (~6.55 GB raw) only if available PC RAM permits.
# 3. Use Record Sequence, not Live Preview. Time the actual recording, note its
#    saved-in-memory frame count, and repeat three times. Record fps=count/time.
#    Playback speed and configured frame rate are not measured capture rates.
#    Enter the matched memory-recording result in camware_fps.
# 4. Close CamWare; run this cell. Script compares capture-only and saved scans.
# 5. Close script handles (automatic). In GUI repeat 10,000 frames three times,
#    timing Start -> finished AND inspecting saved files. Record GUI times.
# 6. Repeat with normal lab apps open and the intended disk; compare variability.
# Benchmarks: zero missing IDs/errors. Proposed capture-only rate >=90% of
# CamWare's matched memory-recording rate. Saved rate is reported separately;
# it includes HDF5 writing and file close. Inspect host-read rate too.
# High capture/low save: disk/writer. Low capture and save vs CamWare: SDK/reader.
# Good script/slow GUI: display/orchestration. Mock rates measure synthesis cost.
if selected(4):
    REPORT_4 = execute(4, CONFIG)


# %% 5 — Repeated and sustained reliability
# 1. Review space estimate: default repeated steps + 5-minute run ~109 GB raw;
#    script requires 1.5x headroom. Reduce duration for a preliminary trial only.
# 2. Watch Task Manager memory/CPU/disk while running. Record start/end and peak
#    memory plus any upward trend; repeat at least once after PC warm-up.
# 3. Use observed_acquisition_span_s: fixed N is only an estimated duration.
#    If under 95% of intended time, set target_fps to measured rate and rerun.
# 4. Separately run a representative GUI multi-step experiment for the intended
#    unattended-session duration, subject to available disk space.
# Benchmarks: every file valid, zero gaps/overflow/queue errors; no progressive
# memory growth after warm-up; no degradation >10% vs section 4. Record actual
# maxima rather than inventing a universal RAM or p99 limit. Queue/FIFO failure
# is always a failed acquisition even when average fps looks good.
# Scan setpoints currently label metadata and DO NOT command a physical stage.
if selected(5):
    REPORT_5 = execute(5, CONFIG)


# %% 6 — Recovery and GUI usability (automatic fault tests always use a mock)
# 1. Run this cell: abort + injected writer error retain two incomplete files;
#    a subsequent mock acquisition must complete without process restart.
# 2. GUI: repeat Live -> Stop -> Scan -> Stop -> Live ten times; include rapid
#    double-clicks. During a scan try source/ROI/mode changes; controls must safely
#    block or serialize them. Resize window; edit colour limits; draw one ROI.
# 3. Stop a physical short scan. Expect an explicit abort, an incomplete .partial
#    for the interrupted step, and successful next scan. Preserve finished steps.
# 4. MOCK ONLY: choose an unwritable destination and test Start. For low-space,
#    use an isolated disposable small test volume; do not fill the research drive.
#    Current free-space preflight applies to execute_scan; direct per-step capture
#    is protected here by prepare(). Error must be clear; nothing marked complete.
# 5. With recording STOPPED, disconnect/reconnect camera; verify clear disconnected
#    status and successful reconnect. Close app during mock acquisition and reopen.
# 6. Trigger timeout is deferred to section 8. Do not unplug other lab instruments.
# Benchmarks: no GUI crash/deadlock, no simultaneous camera ownership, failure
# visible, no incomplete .h5, next run succeeds. Provisional normal UI response
# <1 s; Stop finishes within current SDK wait + writer drain. Record seconds;
# shutter reboot is a separately expected delay, not a responsiveness failure.
if selected(6):
    REPORT_6 = execute(6, CONFIG)


# %% 7 — Raw-data integrity and optional copy verification, entirely in Python
# 1. With CONFIG.data_files=(), this checks the latest short scan from section 3.
#    It reads every pixel and metadata record and computes SHA-256 fingerprints.
# 2. To compare an archived copy, set CONFIG.data_files=(Path(original), Path(copy))
#    before running. It checks exact file bytes, raw pixels, metadata, counts,
#    ROI and camera settings. No MATLAB installation is needed for this cell.
# 3. Acquire labelled dark and stable-illumination short files at identical
#    settings using safe existing lab procedures. Review clipping and artifacts.
# Benchmarks: complete uint16 [frame,y,x], exact counts, consecutive camera IDs,
# and byte-for-byte copy equality if supplied. Pixel statistics remain in ADU.
# Saturation/noise acceptance depends on experiment and calibration; document
# limits before using these files for correlation measurements.
if selected(7):
    REPORT_7 = execute(7, CONFIG)


# %% 8 — OPTIONAL / DEFERRED: external trigger and shot accounting (last)
# Default trigger_bench_ready=False: this section records DEFERRED and opens no camera.
# 1. Verify connector, voltage, polarity and pulse width against installed manual.
#    Connect a known pulse source and an independent counter/scope. Configure a
#    finite train: pulses must occur ONLY while the camera is armed. Use camera
#    busy/exposure outputs or verified hardware gating to bound the active window.
# 2. Start at a known slow rate (e.g. 10 Hz after verifying electrical settings),
#    trigger_frames=100; pulse spacing must remain below the driver's ~1 s wait
#    timeout. Set trigger_bench_ready=True and source="physical", then run cell.
#    Arrange the finite train to start after recording is armed, not before.
# 3. Record independently observed pulses in the actual acquisition window. Set
#    observed_trigger_pulses for the next run; never replace measurement with the
#    requested count. A train longer than N cannot be diagnosed merely by stopping
#    acquisition after N frames. Check scope busy/exposure and end-of-train timing.
# 4. Increase rate gradually toward the SCIENTIFIC requirement, repeating counts
#    and scope timing. Add no-pulse timeout case: explicit failure, no completed
#    .h5, then recovery in AUTO_SEQUENCE. Disable pulse source after each trial.
# Benchmarks: independent pulses = exposures = saved frames, correct shutter timing,
# zero ID gaps/errors at required rate. Consecutive recorder IDs alone cannot
# detect laser pulses the camera never accepted. Host timestamps are receipt times.
# Datasheet reference: 455 fps GS at 2560x128 USB; not proof for this ROI or 1 kHz.
# https://www.excelitas.com/assets/product/document/pcoedge-55-usb-datasheet.pdf?file=26806.pdf
if selected(8):
    REPORT_8 = execute(8, CONFIG)
