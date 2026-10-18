"""Opt-in physical trial of the pinned PCO Python SDK's direct pcoraw recorder.

This diagnostic uses the existing reviewed camera configuration path. It does
not change binning or impose a frame-rate limit. Auto Sequence is used, so
external laser-trigger accounting remains a separate acceptance step.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import importlib.metadata
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import statistics
import time

import numpy as np

from scripts import lab_computer_acceptance as lab


def _section_4_result(reference: dict) -> dict:
    """Read old in-memory or compact saved Section 4 reports consistently."""
    if "result" in reference:
        return reference["result"]
    summary = reference["important_metrics"]
    return {"runs": summary["runs"], "capture_median_fps": summary["capture_only_median_fps"],
            "saved_median_fps": summary["saved_median_fps"],
            "saved_over_capture": summary["saved_over_capture_ratio"],
            "camware_fps": summary.get("camware_fps")}


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
            "PCO pixel readback may be unavailable for the installed file DLL; recorder "
            "counts and file size do not independently verify image pixels or frame IDs.",
            "A purchase decision should still be confirmed by a sustained run on the final drive.",
        ],
    }
    if reference_report is not None and reference_report.get("status") == "EXECUTED":
        result = _section_4_result(reference_report)
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
    elif reference_report is not None:
        packet["hdf5_section_4"] = {
            "status": reference_report.get("status"),
            "error": reference_report.get("error"),
        }
    if report.get("runs"):
        long_run = report["runs"][-1]
        packet["pco_direct_file"] = {
            "frames_requested": long_run.get("frames_requested"),
            "frames_read_back": long_run.get("frames_read_back"),
            "consecutive_frame_ids": long_run.get("consecutive_frame_ids"),
            "pixel_readback_status": long_run.get("pixel_readback_status"),
            "pixel_readback_error": long_run.get("pixel_readback_error"),
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
            "pixel_readback_status": "VERIFIED",
            "first_frame_id": first_id, "last_frame_id": last_id}


def _readback_unavailable_error(exc: Exception) -> bool:
    """Identify PCO's file-DLL unsupported readback response without hiding other errors."""
    message = str(exc).lower()
    return (
        "0x80123004" in message
        and "file dll" in message
        and "option is not available" in message
    )


def _record_one(cam, directory: Path, frames: int, shape: tuple[int, int],
                reference_fps: float) -> dict:
    """Record into a new SDK-owned directory and verify its generated PCO file."""
    if directory.exists():
        raise FileExistsError(f"Refusing to reuse an existing recorder directory: {directory}")
    directory.mkdir()
    timeout_s = max(10.0, 3.0 * frames / reference_fps + 5.0)
    started = time.perf_counter()
    status = None
    try:
        # pco.python file modes take an output directory and generate their own
        # filename.  Passing a desired filename can still let the recorder run,
        # but does not guarantee that a file appears at that exact path.
        cam.record(number_of_images=frames, mode="pcoraw", file_path=str(directory))
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
    generated = [
        candidate for candidate in directory.iterdir()
        if candidate.is_file() and candidate.suffix.lower() == ".pcoraw"
    ]
    nonempty = [candidate for candidate in generated if candidate.stat().st_size > 0]
    if len(nonempty) != 1:
        contents = [
            {"name": candidate.name, "bytes": candidate.stat().st_size}
            for candidate in directory.iterdir() if candidate.is_file()
        ]
        raise RuntimeError(
            "PCO did not leave exactly one nonempty pcoraw file in "
            f"{directory}; directory contents: {contents}"
        )
    path = nonempty[0]

    sync_started = time.perf_counter()
    with path.open("r+b", buffering=0) as handle:
        os.fsync(handle.fileno())
    synced_s = time.perf_counter() - started
    verification_started = time.perf_counter()
    try:
        checked = _read_recorded_frames(cam, frames, shape)
    except Exception as exc:
        if not _readback_unavailable_error(exc):
            raise
        # Some installed PCO file-DLL combinations successfully write pcoraw
        # but do not implement Recorder.copy_image(). Preserve the valid file,
        # recorder counts, and throughput timings while making pixel/ID
        # verification explicitly unavailable in the report.
        checked = {
            "frames_read_back": None,
            "consecutive_frame_ids": None,
            "pixel_readback_status": "UNAVAILABLE",
            "pixel_readback_error": str(exc),
        }
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
        frames: tuple[int, ...] = (20, 2000), emit_output: bool = True) -> dict:
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
        reference_readout = settings.get("readout")
        if isinstance(reference_readout, str):
            reference_readout = reference_readout.rsplit(".", 1)[-1]
        configured_readout = cfg.readout.name
        if tuple(settings.get("roi", ())) != tuple(cfg.roi):
            raise ValueError("Section 4 reference ROI differs from the direct-file trial.")
        if (settings.get("expected_serial") != cfg.expected_serial
                or reference_readout not in (cfg.readout, configured_readout)
                or float(settings.get("exposure_s", -1)) != cfg.exposure_s):
            raise ValueError("Section 4 reference serial, shutter or exposure differs.")
    destination = lab.prepare(cfg, sum(frames))
    run_dir = destination / f"PCO_FILEMODE_TRIAL_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}"
    run_dir.mkdir(exist_ok=False)
    report_path = destination / f"pco_filemode_{run_dir.name.removeprefix('PCO_FILEMODE_TRIAL_')}.json"
    report = {"status": "FAILED", "source": cfg.source,
              "output_directory": str(run_dir), "serial_requested": cfg.expected_serial,
              "free_GB_before_trial": shutil.disk_usage(destination).free / 1e9,
              "roi_requested": list(cfg.roi), "exposure_s_requested": cfg.exposure_s,
              "readout_requested": cfg.readout.name,
              "trigger_requested": lab.TriggerMode.AUTO_SEQUENCE.value,
              "runs": [], "physical_acceptance": False, "report_file": str(report_path)}
    try:
        with lab.configured_camera(cfg, trigger=lab.TriggerMode.AUTO_SEQUENCE) as camera:
            if not hasattr(camera._cam, "record") or not hasattr(camera._cam, "rec"):
                raise RuntimeError("Installed pco package has no direct recorder interface.")
            report["settings_readback"] = lab.snapshot(camera)
            shape = (cfg.roi[3] - cfg.roi[1], cfg.roi[2] - cfg.roi[0])
            for count in frames:
                directory = run_dir / f"native_{count}"
                result = _record_one(camera._cam, directory, count, shape, cfg.target_fps)
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
        if reference_report is not None and reference_report.get("status") == "EXECUTED":
            section_4 = _section_4_result(reference_report)
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
            "trigger counts later, verify pcoraw image contents with a supported reader, "
            "and prove pcoraw-to-HDF5/MATLAB metadata conversion before using direct "
            "file mode for research scans."
        )
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["hard_drive_decision_input"] = _decision_input(
            report, cfg, reference_report
        )
        stored = {key: value for key, value in report.items()
                  if key not in ("hard_drive_decision_input", "pending")}
        stored["runs"] = [
            {**run, "recorder_status": {key: value for key, value in run.get("recorder_status", {}).items()
                                       if key in ("dwReqImgCount", "dwProcImgCount", "dwLastError", "bFIFOOverflow")}}
            for run in report.get("runs", [])
        ]
        report_path.write_text(json.dumps(stored, indent=1, default=lab.json_default),
                               encoding="utf-8")
        if emit_output:
            print("\n=== IMPORTANT METRICS — PCO DIRECT FILE TRIAL ===")
            print(json.dumps(report, indent=2, default=lab.json_default))
            _print_decision_input(report["hard_drive_decision_input"])
    return report


def run_hard_drive_decision_test(
    cfg: lab.Settings,
    *,
    c_output_dir: Path = Path("C:/Users/Public/Documents/@@FPSTEST"),
    d_output_dir: Path = Path("D:/HHG measurements/@@FPSTEST"),
    max_output_gb_per_drive: float = 30.0,
    print_output: bool = False,
) -> dict:
    """Save the reviewed physical comparison and its log without printing.

    Camera settings and output limits are unchanged. Detailed progress and
    Section 4 output are retained in a matching .log beside the compact
    section_4b_<UTC>.json directly in the selected C: test folder.
    Preflight errors still propagate to the notebook before camera recording.
    """
    progress = io.StringIO()
    with redirect_stdout(progress):
        result = _run_hard_drive_decision_test(
            cfg, c_output_dir=c_output_dir, d_output_dir=d_output_dir,
            max_output_gb_per_drive=max_output_gb_per_drive,
        )
    report_path = Path(result["report_file"])
    log_path = report_path.with_suffix(".log")
    log_path.write_text(progress.getvalue(), encoding="utf-8")
    result["paste_this"]["execution_log"] = str(log_path)
    result["paste_this"]["report_file"] = str(report_path)
    report_path.write_text(
        json.dumps(result["comparison"], indent=1, default=lab.json_default),
        encoding="utf-8",
    )
    if print_output:
        _print_decision_input(result["paste_this"])
    return result


def _run_hard_drive_decision_test(
    cfg: lab.Settings,
    *,
    c_output_dir: Path = Path("C:/Users/Public/Documents/@@FPSTEST"),
    d_output_dir: Path = Path("D:/HHG measurements/@@FPSTEST"),
    max_output_gb_per_drive: float = 30.0,
) -> dict:
    """Compare PCO .pcoraw and production HDF5 writes on C: and D:.

    Each drive receives one .pcoraw file and one HDF5 file with identical frame
    counts and camera settings. Their combined size is capped at the requested
    per-drive budget. Frame count is limited by both that cap and the currently
    available space on the tighter drive; preflight reserves 1.5 times the
    combined raw payload plus 100 MB. Two GB of the output cap is reserved for
    format metadata and reports. All files are retained.

    This function connects to the selected physical camera and applies the ROI,
    exposure, Global Shutter and Auto Sequence settings already shown in the
    notebook. It runs direct PCO file mode and Section 4's capture-only plus
    saved-HDF5 path on each drive. It does not test external-trigger accounting.
    """
    cfg = replace(cfg, expected_serial=cfg.expected_serial or lab.DEFAULT_CAMERA_SERIAL)
    width = cfg.roi[2] - cfg.roi[0]
    height = cfg.roi[3] - cfg.roi[1]
    if (width, height) != (2560, 128):
        raise ValueError(
            "This drive-decision test is for the reviewed 2560 x 128 ROI; "
            f"the setup cell currently specifies {width} x {height}."
        )

    if max_output_gb_per_drive <= 2.1:
        raise ValueError("max_output_gb_per_drive must exceed the 2-GB overhead reserve")
    c_output_dir, d_output_dir = Path(c_output_dir), Path(d_output_dir)
    if _drive_id(c_output_dir) == _drive_id(d_output_dir):
        raise ValueError("C: and D: test destinations must be on different drives")
    frame_bytes = width * height * 2
    output_cap_bytes = int(max_output_gb_per_drive * 1e9)
    overhead_reserve_bytes = 2_000_000_000
    free_space_reserve_bytes = 100_000_000
    free_by_drive = {
        "C": _destination_free_bytes(c_output_dir),
        "D": _destination_free_bytes(d_output_dir),
    }
    tightest_free_bytes = min(free_by_drive.values())
    # There are two same-sized outputs per drive. lab.prepare's 1.5x free-space
    # rule therefore requires 3x one-format raw bytes before either run.
    max_raw_per_format = min(
        (output_cap_bytes - overhead_reserve_bytes) // 2,
        max(0, tightest_free_bytes - free_space_reserve_bytes) // 3,
    )
    frames = max_raw_per_format // frame_bytes
    if frames < 2000:
        raise OSError(
            "The drive comparison needs at least 2,000 frames per format on both drives. "
            f"Available free space is C: {free_by_drive['C'] / 1e9:.2f} GB, "
            f"D: {free_by_drive['D'] / 1e9:.2f} GB; clear space or choose another "
            "destination before retrying. No camera run was started."
        )
    planned_raw_per_format = frames * frame_bytes
    planned_raw_per_drive = 2 * planned_raw_per_format
    common_cfg = replace(
        cfg, source="physical", speed_frames=frames, repeats=1,
        run_offline_tests=False,
    )
    # Preflight the complete two-format payload on both drives before camera use.
    drive_cfgs = {
        "C": replace(common_cfg, output_dir=c_output_dir),
        "D": replace(common_cfg, output_dir=d_output_dir),
    }
    for drive_cfg in drive_cfgs.values():
        lab.prepare(drive_cfg, frames * 2)

    print(
        "C: VS D: PCO .PCORAW AND HDF5 COMPARISON\n"
        f"Camera serial: {common_cfg.expected_serial}\n"
        f"ROI: {width} x {height}; frames per format and drive: {frames:,}\n"
        f"Planned raw payload per format: {planned_raw_per_format / 1e9:.3f} GB\n"
        f"Planned raw payload across both formats per drive: {planned_raw_per_drive / 1e9:.3f} GB\n"
        f"Maximum output per drive: {max_output_gb_per_drive:.1f} GB\n"
        f"C: {c_output_dir.resolve()}\nD: {d_output_dir.resolve()}\n"
        "Both destinations were preflighted for both formats before camera use. "
        "All .pcoraw and .h5 files will remain on disk.\n"
        "Do not open CamWare or the LabSoftware camera GUI until both runs finish."
    )

    summary = {
        "status": "EXECUTED", "source": "physical",
        "serial": common_cfg.expected_serial,
        "roi": list(common_cfg.roi), "exposure_s": common_cfg.exposure_s,
        "readout": common_cfg.readout.name,
        "trigger": lab.TriggerMode.AUTO_SEQUENCE.value,
        "max_output_GB_per_drive": max_output_gb_per_drive,
        "output_overhead_reserve_GB_per_drive": overhead_reserve_bytes / 1e9,
        "frames_per_format_per_drive": frames,
        "planned_raw_GB_per_format": planned_raw_per_format / 1e9,
        "planned_raw_GB_across_formats_per_drive": planned_raw_per_drive / 1e9,
        "free_GB_before_test_by_drive": {
            drive: free / 1e9 for drive, free in free_by_drive.items()
        },
        "C": None, "D": None, "comparison": None,
        "physical_acceptance": False,
        "limitations": [
            "C: and D: runs are sequential, so other changing system load may affect the comparison.",
            "PCO file-DLL pixel readback is unavailable on this installation; counts and file size "
            "do not verify every pixel or consecutive camera frame ID.",
            "Auto Sequence does not validate external trigger accounting.",
            "Section 4 verifies HDF5 metadata and frame IDs; PCO pixel readback may remain unavailable.",
            "The selected frame count is space-limited and this is not final multi-hour acceptance.",
        ],
    }
    for drive, drive_cfg in drive_cfgs.items():
        before = set(drive_cfg.output_dir.glob("PCO_FILEMODE_TRIAL_*"))
        drive_summary = {
            "status": "PARTIAL",
            "output_directory": str(drive_cfg.output_dir.resolve()),
            "free_GB_before_test": free_by_drive[drive] / 1e9,
            "pcoraw": None,
            "hdf5": None,
            "combined_output_bytes": None,
            "combined_output_GB": None,
            "max_output_GB": max_output_gb_per_drive,
            "within_output_cap": None,
        }
        try:
            drive_report = run(
                drive_cfg, reference_report=None,
                frames=(frames,), emit_output=False,
            )
            drive_summary["pcoraw"] = _drive_run_summary(drive_report)
            pcoraw_bytes = drive_summary["pcoraw"].get("file_bytes")
            if pcoraw_bytes is None:
                raise RuntimeError(f"{drive}: PCO recorder report omitted the file size")
            projected_hdf5_bytes = planned_raw_per_format + 100_000_000
            if pcoraw_bytes + projected_hdf5_bytes > output_cap_bytes:
                raise RuntimeError(
                    f"{drive}: projected .pcoraw plus HDF5 output exceeds the "
                    f"{max_output_gb_per_drive:.1f}-GB per-drive cap; HDF5 run was not started"
                )

            hdf5_cfg = replace(drive_cfg, speed_frames=frames, repeats=1)
            hdf5_report = lab.execute(4, hdf5_cfg)
            drive_summary["hdf5"] = _hdf5_run_summary(
                hdf5_report, frame_bytes=frame_bytes,
            )
            hdf5_bytes = drive_summary["hdf5"].get("file_bytes")
            if hdf5_bytes is None:
                raise RuntimeError(f"{drive}: Section 4 HDF5 report omitted the file size")
            combined_bytes = pcoraw_bytes + hdf5_bytes
            drive_summary["combined_output_bytes"] = combined_bytes
            drive_summary["combined_output_GB"] = combined_bytes / 1e9
            drive_summary["within_output_cap"] = combined_bytes <= output_cap_bytes
            if not drive_summary["within_output_cap"]:
                raise RuntimeError(
                    f"{drive}: measured .pcoraw plus HDF5 output exceeded the "
                    f"{max_output_gb_per_drive:.1f}-GB per-drive cap"
                )
            drive_summary["status"] = "EXECUTED"
        except Exception as exc:
            if drive_summary["pcoraw"] is None:
                report_dirs = sorted(
                    set(drive_cfg.output_dir.glob("PCO_FILEMODE_TRIAL_*")) - before,
                    key=lambda path: path.stat().st_mtime,
                )
                saved_report = None
                saved_report_path = (drive_cfg.output_dir /
                                     f"pco_filemode_{report_dirs[-1].name.removeprefix('PCO_FILEMODE_TRIAL_')}.json"
                                     if report_dirs else None)
                if saved_report_path is not None and saved_report_path.is_file():
                    saved_report = json.loads(
                        saved_report_path.read_text(encoding="utf-8")
                    )
                drive_summary["pcoraw"] = _drive_run_summary(saved_report, error=exc)
            drive_summary["status"] = "FAILED"
            drive_summary["error"] = f"{type(exc).__name__}: {exc}"
            summary[drive] = drive_summary
            summary["status"] = "PARTIAL"
            summary["stopped_after"] = drive
            break
        summary[drive] = drive_summary
    if summary["C"] is not None and summary["D"] is not None:
        c_pco = (summary["C"].get("pcoraw") or {}).get("durable_raw_equivalent_MB_per_s")
        d_pco = (summary["D"].get("pcoraw") or {}).get("durable_raw_equivalent_MB_per_s")
        c_hdf5 = (summary["C"].get("hdf5") or {}).get("durable_raw_equivalent_MB_per_s")
        d_hdf5 = (summary["D"].get("hdf5") or {}).get("durable_raw_equivalent_MB_per_s")
        summary["comparison"] = {
            "pcoraw_D_over_C_durable_rate_ratio": d_pco / c_pco
            if c_pco and d_pco is not None else None,
            "pcoraw_D_minus_C_durable_MB_per_s": d_pco - c_pco
            if c_pco is not None and d_pco is not None else None,
            "hdf5_D_over_C_durable_rate_ratio": d_hdf5 / c_hdf5
            if c_hdf5 and d_hdf5 is not None else None,
            "hdf5_D_minus_C_durable_MB_per_s": d_hdf5 - c_hdf5
            if c_hdf5 is not None and d_hdf5 is not None else None,
            "C_pcoraw_over_hdf5_durable_rate_ratio": c_pco / c_hdf5
            if c_hdf5 and c_pco is not None else None,
            "D_pcoraw_over_hdf5_durable_rate_ratio": d_pco / d_hdf5
            if d_hdf5 and d_pco is not None else None,
            "required_raw_MB_per_s_at_target_fps": frame_bytes * common_cfg.target_fps / 1e6,
            "proposed_margin_target_MB_per_s": (
                frame_bytes * common_cfg.target_fps * common_cfg.storage_margin / 1e6
            ),
        }
    report_path = c_output_dir.resolve() / (
        f"section_4b_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}.json"
    )
    report_path.write_text(
        json.dumps(summary, indent=1, default=lab.json_default), encoding="utf-8"
    )
    return {"settings_used": common_cfg, "drives": drive_cfgs,
            "comparison": summary, "paste_this": summary,
            "report_file": str(report_path)}


def _destination_free_bytes(path: Path) -> int:
    """Read free bytes from an existing ancestor before creating a test folder."""
    probe = path.resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _drive_id(path: Path) -> str:
    """Return a Windows drive letter while keeping path validation testable elsewhere."""
    return PureWindowsPath(str(path)).drive.casefold()


def _hdf5_run_summary(report: dict, *, frame_bytes: int) -> dict:
    """Summarize one Section 4 capture-only/HDF5 run for drive comparison."""
    result = report.get("result", {})
    runs = result.get("runs", [])
    if report.get("status") != "EXECUTED" or len(runs) != 1:
        raise RuntimeError("Section 4 did not return exactly one successful HDF5 run")
    capture = runs[0]["capture_only"]
    saved = runs[0]["saved"]
    path = Path(saved["file"])
    file_bytes = path.stat().st_size
    return {
        "status": report.get("status"),
        "file": str(path.resolve()), "file_bytes": file_bytes,
        "frames": saved.get("frames"),
        "complete": saved.get("complete"),
        "consecutive_frame_ids": saved.get("consecutive_ids"),
        "capture_only_fps": capture.get("total_fps"),
        "saved_fps": saved.get("total_fps"),
        "durable_fps": saved.get("synced_fps"),
        "durable_raw_equivalent_MB_per_s": (
            saved["synced_fps"] * frame_bytes / 1e6
            if saved.get("synced_fps") is not None else None
        ),
        "final_os_fsync_s": saved.get("final_os_fsync_s"),
        "pipeline_stage_timing_s": saved.get("pipeline_stage_timing_s"),
    }


def _drive_run_summary(report: dict | None, *, error: Exception | None = None) -> dict:
    """Keep only decision-relevant fields for one large drive run."""
    report = report or {}
    long_run = report.get("runs", [{}])[-1] if report.get("runs") else {}
    status = long_run.get("recorder_status", {})
    return {
        "status": report.get("status", "FAILED" if error else "UNKNOWN"),
        "error": report.get("error", str(error) if error else None),
        "output_directory": report.get("output_directory"),
        "free_GB_before_trial": report.get("free_GB_before_trial"),
        "file": long_run.get("file"),
        "frames_requested": long_run.get("frames_requested"),
        "frames_processed": status.get("dwProcImgCount"),
        "recorder_error": status.get("dwLastError"),
        "fifo_overflow": status.get("bFIFOOverflow"),
        "file_bytes": long_run.get("file_bytes"),
        "capture_fps": long_run.get("capture_fps"),
        "capture_raw_equivalent_MB_per_s": long_run.get("capture_raw_equivalent_MB_per_s"),
        "durable_capture_and_sync_fps": long_run.get("capture_and_sync_fps"),
        "durable_raw_equivalent_MB_per_s": long_run.get("capture_and_sync_raw_equivalent_MB_per_s"),
        "final_os_fsync_s": long_run.get("final_os_fsync_s"),
        "pixel_readback_status": long_run.get("pixel_readback_status"),
        "pixel_readback_error": long_run.get("pixel_readback_error"),
        "consecutive_frame_ids": long_run.get("consecutive_frame_ids"),
    }
