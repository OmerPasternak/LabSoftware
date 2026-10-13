"""Opt-in physical trial of the pinned PCO Python SDK's direct pcoraw recorder.

This diagnostic uses the existing reviewed camera configuration path. It does
not change binning or impose a frame-rate limit. Auto Sequence is used, so
external laser-trigger accounting remains a separate acceptance step.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import statistics
import time

import numpy as np

from scripts import lab_computer_acceptance as lab


def _decision_input(report: dict, cfg: lab.Settings,
                    reference_report: dict | None) -> dict:
    """Build one self-contained block for the final storage decision."""
    width = cfg.roi[2] - cfg.roi[0]
    height = cfg.roi[3] - cfg.roi[1]
    frame_bytes = width * height * 2
    required_MB_s = frame_bytes * cfg.target_fps / 1e6
    packet = {
        "request": (
            "Use these measured results to decide whether the current acquisition "
            "is primarily limited by storage, USB/camera capture, or the HDF5 writer. "
            "Estimate the benefit of replacing the destination drive, state whether "
            "the official PCO direct-file method is viable, and recommend the minimum "
            "sustained and margin-tested write speeds for durable target-FPS acquisition."
        ),
        "test_status": report.get("status"),
        "test_error": report.get("error"),
        "destination": report.get("output_directory"),
        "free_GB_before_trial": report.get("free_GB_before_trial"),
        "camera": {
            "serial_requested": cfg.expected_serial,
            "settings_readback": report.get("settings_readback"),
            "roi_width_px": width,
            "roi_height_px": height,
            "bytes_per_frame_uint16": frame_bytes,
            "target_fps": cfg.target_fps,
            "raw_rate_required_MB_per_s": required_MB_s,
            "writer_rate_with_margin_MB_per_s": required_MB_s * cfg.storage_margin,
            "exposure_ms": cfg.exposure_s * 1000,
            "readout": cfg.readout.name,
            "trigger_mode_for_this_test": lab.TriggerMode.AUTO_SEQUENCE.value,
            "binning": "none",
        },
        "configured_storage_estimate_MB_per_s": cfg.estimated_storage_MB_per_s,
        "camware_same_settings_fps": cfg.camware_fps,
        "hdf5_section_4": None,
        "pco_direct_file": None,
        "limitations": [
            "This short Auto Sequence trial does not validate external trigger accounting.",
            "A purchase decision should still be confirmed by a sustained run on the final drive.",
        ],
    }
    if reference_report is not None and reference_report.get("status") == "EXECUTED":
        result = reference_report["result"]
        synced_values = [item["saved"]["synced_fps"] for item in result["runs"]]
        synced_median = statistics.median(synced_values)
        packet["hdf5_section_4"] = {
            "capture_only_median_fps": result["capture_median_fps"],
            "saved_through_close_median_fps": result["saved_median_fps"],
            "durable_synced_fps_each_run": synced_values,
            "durable_synced_median_fps": synced_median,
            "durable_raw_equivalent_MB_per_s": synced_median * frame_bytes / 1e6,
            "saved_over_capture_ratio": result["saved_over_capture"],
            "same_settings_camware_fps": result.get("camware_fps"),
        }
    if report.get("runs"):
        long_run = report["runs"][-1]
        packet["pco_direct_file"] = {
            "frames_requested": long_run.get("frames_requested"),
            "frames_read_back": long_run.get("frames_read_back"),
            "consecutive_frame_ids": long_run.get("consecutive_frame_ids"),
            "recorder_status": long_run.get("recorder_status"),
            "capture_fps": long_run.get("capture_fps"),
            "capture_raw_equivalent_MB_per_s": long_run.get(
                "capture_raw_equivalent_MB_per_s"
            ),
            "durable_capture_and_sync_fps": long_run.get("capture_and_sync_fps"),
            "durable_raw_equivalent_MB_per_s": long_run.get(
                "capture_and_sync_raw_equivalent_MB_per_s"
            ),
            "final_os_fsync_s": long_run.get("final_os_fsync_s"),
            "file_bytes": long_run.get("file_bytes"),
            "comparison_with_hdf5": report.get("section_4_comparison"),
            "short_trial_decision": report.get("decision"),
        }
    return packet


def _print_decision_input(packet: dict) -> None:
    """Print a compact block intended to be pasted into a follow-up chat."""
    print("\n=== COPY FROM HERE — FINAL HARD-DRIVE BOTTLENECK DECISION ===")
    print(json.dumps(packet, indent=2, default=lab.json_default))
    print("=== END COPY — FINAL HARD-DRIVE BOTTLENECK DECISION ===")


def _read_recorded_frames(cam, frames: int, shape: tuple[int, int]) -> dict:
    """Read every native-file frame through pco.python after recording stops.

    This verifies the SDK can retrieve all 16-bit images and exposes recorder
    frame-number gaps. It does not prove external trigger-to-frame accounting.
    """
    previous_id = None
    first_id = None
    last_id = None
    for index in range(frames):
        image, metadata = cam.image(image_index=index, data_format="Mono16")
        image = np.asarray(image)
        if image.dtype != np.uint16 or image.shape != shape:
            raise RuntimeError(
                f"Native file frame {index} is {image.shape}/{image.dtype}; "
                f"expected {shape}/uint16."
            )
        if not isinstance(metadata, dict) or "recorder image number" not in metadata:
            raise RuntimeError(f"Native file frame {index} has no recorder image number.")
        frame_id = int(metadata["recorder image number"])
        if previous_id is not None and frame_id != (previous_id + 1) % 2**32:
            raise RuntimeError(
                f"Native file frame-ID gap at index {index}: "
                f"previous {previous_id}, current {frame_id}."
            )
        first_id = frame_id if first_id is None else first_id
        last_id = frame_id
        previous_id = frame_id
    return {"frames_read_back": frames, "consecutive_frame_ids": True,
            "first_frame_id": first_id, "last_frame_id": last_id}


def _record_one(cam, path: Path, frames: int, shape: tuple[int, int],
                reference_fps: float) -> dict:
    """Record a finite Auto Sequence directly to a new PCO file and verify it."""
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing recorder file: {path}")
    timeout_s = max(10.0, 3.0 * frames / reference_fps + 5.0)
    started = time.perf_counter()
    status = None
    try:
        cam.record(number_of_images=frames, mode="pcoraw", file_path=str(path))
        while True:
            status = cam.rec.get_status()
            if status.get("dwLastError") or status.get("bFIFOOverflow"):
                raise RuntimeError(f"PCO native recorder reported an error: {status}")
            if not status.get("is running", status.get("bIsRunning", True)):
                break
            if time.perf_counter() - started > timeout_s:
                raise TimeoutError(f"PCO file-mode recording exceeded {timeout_s:.1f} s: {status}")
            time.sleep(0.02)
        capture_s = time.perf_counter() - started
    finally:
        cam.stop()

    if int(status.get("dwReqImgCount", -1)) != frames:
        raise RuntimeError(f"PCO changed the requested recorder count: {status}")
    if int(status.get("dwProcImgCount", -1)) != frames:
        raise RuntimeError(f"PCO recorded fewer or more frames than requested: {status}")
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"PCO did not leave a nonempty pcoraw file at {path}")

    sync_started = time.perf_counter()
    with path.open("r+b", buffering=0) as handle:
        os.fsync(handle.fileno())
    synced_s = time.perf_counter() - started
    verification_started = time.perf_counter()
    checked = _read_recorded_frames(cam, frames, shape)
    verification_s = time.perf_counter() - verification_started
    raw_bytes = frames * shape[0] * shape[1] * 2
    return {
        "file": str(path.resolve()), "frames_requested": frames,
        "pco_version": importlib.metadata.version("pco"),
        "recorder_status": status,
        "capture_s": capture_s, "capture_fps": frames / capture_s,
        "capture_raw_equivalent_MB_per_s": raw_bytes / 1e6 / capture_s,
        "capture_and_sync_s": synced_s,
        "capture_and_sync_fps": frames / synced_s,
        "capture_and_sync_raw_equivalent_MB_per_s": raw_bytes / 1e6 / synced_s,
        "final_os_fsync_s": synced_s - (sync_started - started),
        "file_bytes": path.stat().st_size,
        "verification_s_after_capture": verification_s,
        **checked,
    }


def run(cfg: lab.Settings, *, reference_report: dict | None = None,
        frames: tuple[int, ...] = (20, 2000)) -> dict:
    """Run a reviewed, opt-in native recorder trial on the physical PCO camera.

    Reuses the selected serial, sensor-pixel ROI, exposure seconds and Global
    Shutter settings from the acceptance notebook. Results stay in a unique
    subfolder; a failed or partial native file is retained for inspection.
    """
    if cfg.source != "physical":
        raise ValueError("PCO direct-file trial requires CONFIG.source = 'physical'.")
    if not frames or any(n < 1 for n in frames):
        raise ValueError("Trial frame counts must be positive.")
    if reference_report is not None:
        if reference_report.get("section") != 4 or reference_report.get("source") != "physical":
            raise ValueError("Reference must be a physical section 4 report.")
        settings = reference_report.get("settings", {})
        if tuple(settings.get("roi", ())) != tuple(cfg.roi):
            raise ValueError("Section 4 reference ROI differs from the direct-file trial.")
        if (settings.get("expected_serial") != cfg.expected_serial
                or settings.get("readout") != cfg.readout
                or float(settings.get("exposure_s", -1)) != cfg.exposure_s):
            raise ValueError("Section 4 reference serial, shutter or exposure differs.")
    destination = lab.prepare(cfg, sum(frames))
    run_dir = destination / f"PCO_FILEMODE_TRIAL_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}"
    run_dir.mkdir(exist_ok=False)
    report_path = run_dir / "report.json"
    report = {"status": "FAILED", "source": cfg.source,
              "output_directory": str(run_dir), "serial_requested": cfg.expected_serial,
              "free_GB_before_trial": shutil.disk_usage(destination).free / 1e9,
              "roi_requested": list(cfg.roi), "exposure_s_requested": cfg.exposure_s,
              "readout_requested": cfg.readout.name,
              "trigger_requested": lab.TriggerMode.AUTO_SEQUENCE.value,
              "runs": [], "physical_acceptance": False}
    try:
        with lab.configured_camera(cfg, trigger=lab.TriggerMode.AUTO_SEQUENCE) as camera:
            if not hasattr(camera._cam, "record") or not hasattr(camera._cam, "rec"):
                raise RuntimeError("Installed pco package has no direct recorder interface.")
            report["settings_readback"] = lab.snapshot(camera)
            shape = (cfg.roi[3] - cfg.roi[1], cfg.roi[2] - cfg.roi[0])
            for count in frames:
                path = run_dir / f"native_{count}.pcoraw"
                result = _record_one(camera._cam, path, count, shape, cfg.target_fps)
                report["runs"].append(result)
        long_run = report["runs"][-1]
        report["camware_reference_fps"] = cfg.camware_fps
        report["capture_over_camware"] = (
            long_run["capture_fps"] / cfg.camware_fps
            if cfg.camware_fps is not None and cfg.camware_fps > 0 else None
        )
        report["capture_and_sync_over_camware"] = (
            long_run["capture_and_sync_fps"] / cfg.camware_fps
            if cfg.camware_fps is not None and cfg.camware_fps > 0 else None
        )
        if reference_report is not None:
            section_4 = reference_report["result"]
            hdf5_synced = statistics.median(
                item["saved"]["synced_fps"] for item in section_4["runs"]
            )
            report["section_4_comparison"] = {
                "capture_only_median_fps": section_4["capture_median_fps"],
                "hdf5_saved_median_fps": section_4["saved_median_fps"],
                "hdf5_synced_median_fps": hdf5_synced,
                "direct_file_capture_fps": long_run["capture_fps"],
                "direct_file_capture_and_sync_fps": long_run["capture_and_sync_fps"],
                "direct_file_over_hdf5_synced": (
                    long_run["capture_and_sync_fps"] / hdf5_synced
                    if hdf5_synced > 0 else None
                ),
            }
        report["decision"] = (
            "SHORT_TRIAL_PASS" if report["capture_over_camware"] is not None
            and report["capture_over_camware"] >= cfg.speed_fraction
            and report["capture_and_sync_over_camware"] >= cfg.speed_fraction
            else "PENDING_MATCHED_CAMWARE_BASELINE" if cfg.camware_fps is None
            else "BELOW_MATCHED_CAMWARE_BASELINE"
        )
        report["status"] = "EXECUTED"
        report["pending"] = (
            "Repeat on the final drive for a sustained run, validate independent "
            "trigger counts later, and prove pcoraw-to-HDF5/MATLAB metadata conversion "
            "before using direct file mode for research scans."
        )
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["hard_drive_decision_input"] = _decision_input(
            report, cfg, reference_report
        )
        report_path.write_text(json.dumps(report, indent=2, default=lab.json_default),
                               encoding="utf-8")
        print("\n=== IMPORTANT METRICS — PCO DIRECT FILE TRIAL ===")
        print(json.dumps(report, indent=2, default=lab.json_default))
        _print_decision_input(report["hard_drive_decision_input"])
    return report
