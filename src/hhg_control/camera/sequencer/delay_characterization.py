"""Gated camera-delay measurements using the existing raw writer and intensity analysis."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from ..analysis.harmonic_intensity import compute_harmonic_intensity
from ..drivers.base_camera import TriggerMode
from .scan_manager import AcquisitionAborted
from ...safe_io import check_disk_space, filesystem_path


def delay_points_ns(limits: tuple[float, float, float]) -> list[float]:
    """Return 5 points at 0–100 ns and 10 at 1900–2100 ns on the device grid.

    Reject unsupported ranges or increments too coarse to yield distinct points.
    Delays are camera exposure-start delays, not optical-stage delays.
    """
    minimum, maximum, increment = limits
    if not all(np.isfinite(limits)) or minimum < 0 or increment <= 0 or maximum < minimum:
        raise ValueError("Invalid camera delay limits.")
    points = []
    for low, high, count in ((0, 100, 5), (1900, 2100, 10)):
        if minimum > low * 1e-9 or maximum < high * 1e-9:
            raise ValueError(f"Camera does not support the requested {low}–{high} ns range.")
        grid = minimum + np.rint((np.linspace(low, high, count) * 1e-9 - minimum) / increment) * increment
        values = np.round(grid * 1e9, 6)
        if len(np.unique(values)) != count or values.min() < low or values.max() > high:
            raise ValueError("Camera delay increment is too coarse for the requested points.")
        points.extend(float(v) for v in values)
    return points


def estimate_duration_s(frames_per_point: int, exposure_s: float, shape_yx) -> tuple[float, int]:
    """Estimate seconds and raw bytes for 15 points plus a reference.

    Assume 50 MiB/s storage, a 10 ms frame/readout floor, and 1 s overhead/file.
    External trigger waits and manual rectangle selection have unknown duration.
    """
    count = 15 * frames_per_point + 1
    raw_bytes = count * int(np.prod(shape_yx)) * 2
    return 16 + count * max(exposure_s, 0.01) + raw_bytes / (50 * 2**20), raw_bytes


@contextmanager
def _session(manager):
    """Own camera serialization; restore delay, trigger mode and input state on exit."""
    with manager._operation("DELAY_DIAGNOSTIC"), manager._camera_lock:
        camera = manager.camera
        original_delay = camera.get_delay_time()
        original_trigger = camera.get_trigger_mode()
        original_external_input = camera.get_external_trigger_input_enabled()
        try:
            camera.set_trigger_mode(TriggerMode.EXTERNAL_EXPOSURE_START)
            camera.set_external_trigger_input_enabled(True)
            yield camera, original_delay, original_trigger
        finally:
            failures = []
            for restore in (lambda: camera.set_delay_time(original_delay),
                            lambda: camera.set_trigger_mode(original_trigger),
                            lambda: camera.set_external_trigger_input_enabled(original_external_input)):
                try:
                    restore()
                except Exception as exc:
                    failures.append(str(exc))
            if failures:
                camera.state_unknown = True
                raise RuntimeError("Delay diagnostic cleanup failed; camera state unknown: " + "; ".join(failures))


def _save(manager, run_id, index, delay_ns, count, abort_check):
    """Use the existing bounded, integrity-checked raw writer while holding session ownership."""
    if abort_check():
        raise AcquisitionAborted("Camera delay characterization stopped.")
    if (manager.camera.get_trigger_mode() != TriggerMode.EXTERNAL_EXPOSURE_START
            or not manager.camera.get_external_trigger_input_enabled()):
        raise RuntimeError("Delay characterization requires External Exposure Start with its input enabled.")
    return manager._acquire_and_save_step(
        "Camera delay", index, "Camera delay (ns)", delay_ns, count,
        run_id=run_id, batch_size=1, queue_batches=None, abort_check=abort_check,
    )[0]


def acquire_delay_reference(manager, run_id: str, frames_per_point: int, abort_check, log,
                            *, start_message: str = "[DELAY] Starting camera-delay characterization.") -> Path:
    """Preflight all points/storage, then save one externally triggered reference unchanged."""
    if frames_per_point < 2:
        raise ValueError("At least two frames per delay are required for sample STDEV.")
    with _session(manager) as (camera, _, _):
        points = delay_points_ns(camera.get_delay_limits())
        roi = camera.get_roi()
        seconds, raw_bytes = estimate_duration_s(frames_per_point, camera.get_exposure_time(),
                                               (roi[3] - roi[1], roi[2] - roi[0]))
        log(f"Estimated characterization time: ~{seconds:.0f} s ({seconds/60:.1f} min), "
            f"15 delays × {frames_per_point} frames + 1 reference; ~{raw_bytes/2**30:.2f} GiB raw. "
            "Estimate assumes sufficient external triggers and 50 MiB/s storage; "
            "trigger waits and window-selection time are additional. STOP cancels.")
        check_disk_space(manager.storage_dir, int(raw_bytes * 1.1))
        log(start_message)
        log("Waiting for an external trigger to save the harmonic-selection reference.")
        path = _save(manager, run_id, 0, camera.get_delay_time() * 1e9, 1, abort_check)
        log(f"Reference saved: {path}. Delay ranges: {points} ns.")
        return path


def characterize_delay(manager, run_id: str, frames_per_point: int, window: dict,
                       abort_check, log) -> Path:
    """Save every frame at each verified delay and separately report raw sums ± sample STDEV.

    Re-arm a separate recorder per point, so frames cannot cross delay boundaries.
    Preserve successful point reports and incomplete raw files after cancellation/error.
    """
    if frames_per_point < 2:
        raise ValueError("At least two frames per delay are required for sample STDEV.")
    started = time.perf_counter()
    report_path = manager.storage_dir / f"camera_delay_{run_id}.json"
    report = {"kind": "camera_delay_characterization", "schema_version": "1.0",
              "created_utc": datetime.now(timezone.utc).isoformat(), "complete": False,
              "frames_per_point": frames_per_point, "window": window, "points": [],
              "error_bars": "Sample STDEV (ddof=1) across individual raw window sums; not SEM"}
    # Reserve a new result before acquiring; never replace existing results.
    with filesystem_path(report_path).open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    try:
        with _session(manager) as (camera, original_delay, original_trigger):
            report["original_delay_s"] = original_delay
            report["original_trigger_mode"] = original_trigger.value
            report["camera"] = camera.get_sensor_info()
            points = delay_points_ns(camera.get_delay_limits())
            for index, requested_ns in enumerate(points, 1):
                if abort_check():
                    raise AcquisitionAborted("Camera delay characterization stopped.")
                camera.set_delay_time(requested_ns * 1e-9)
                actual_ns = camera.get_delay_time() * 1e9
                if not np.isclose(actual_ns, requested_ns, rtol=0, atol=0.001):
                    raise RuntimeError("Delay readback changed before acquisition.")
                log(f"Delay {index}/15: requested {requested_ns:g} ns, actual {actual_ns:g} ns; acquiring.")
                path = _save(manager, run_id, index, actual_ns, frames_per_point, abort_check)
                result = compute_harmonic_intensity([path], window)
                result.update(requested_delay_ns=requested_ns, actual_delay_ns=actual_ns)
                report["points"].append(result)
                log(f"Delay {index}/15: Average {result['average_adu']:.6g} ADU; "
                    f"STDEV {result['stdev_adu']:.6g} ADU; {path}")
                _write_report(report_path, report)
        report["complete"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["elapsed_s"] = time.perf_counter() - started
        _write_report(report_path, report)
    return report_path


def _write_report(path: Path, report: dict) -> None:
    """Replace only this run's reserved derived report, never a raw acquisition file."""
    temporary = filesystem_path(path.with_suffix(".json.tmp"))
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    temporary.replace(filesystem_path(path))
