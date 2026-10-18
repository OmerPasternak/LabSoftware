"""Quiet notebook diagnostics 4C (batches), 4D (formats), 4E (pixel readback).

All outputs are retained in new BENCHMARK_ONLY directories. 4C uses the reviewed
camera configuration path; 4D never constructs or connects a physical camera.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import shutil
import time
import traceback

import h5py
import numpy as np

from scripts import lab_computer_acceptance as lab
from scripts import capture_diagnostics as capture_diag
from hhg_control.sequencer.scan_manager import CameraScanManager, _frame_chunk_shape


ROI_WINDOWS = {
    "small": ((0, 1016, 2560, 1144), 455.0),
    "medium": ((0, 540, 2560, 1620), None),  # Half the full-frame pixels; full width, half height.
    "large": ((0, 0, 2560, 2160), 28.0),
}
ROI_CHART_SOURCE = "https://www.excelitas.com/assets/product/document/pcoedge-55-usb-datasheet.pdf?file=26806.pdf"


def window_settings(cfg, window=None):
    """Return a copy with an unbinned centered ROI and an explicit report/name tag.

    Medium is exactly half the full-frame area, not binning. The vendor GS table
    has no exact 2560x1080 entry: its reference remains unknown, never extrapolated.
    None preserves caller settings for existing scripts and small offline mocks.
    """
    if window is not None and window not in ROI_WINDOWS:
        raise ValueError("Window must be small, medium or large")
    roi = tuple(cfg.roi) if window is None else ROI_WINDOWS[window][0]
    label = window or next((name for name, item in ROI_WINDOWS.items() if item[0] == roi), "custom")
    reference = ROI_WINDOWS[label][1] if label in ROI_WINDOWS and cfg.readout == lab.ReadoutMode.GLOBAL_SHUTTER else None
    width, height = roi[2] - roi[0], roi[3] - roi[1]
    info = {"name": label, "tag": f"{label}_{width}x{height}", "roi": list(roi),
            "shape_y_x": [height, width], "bytes_per_frame": width * height * 2,
            "chart_reference_fps": reference, "chart_source": ROI_CHART_SOURCE,
            "reference_note": "GS chart reference, not measured cadence; medium has no exact table entry."}
    return replace(cfg, roi=roi, target_fps=reference if window is not None and reference is not None else cfg.target_fps,
                   camware_fps=cfg.camware_fps if tuple(cfg.roi) == roi else None), info


class _WindowManager(CameraScanManager):
    """Retain the production writer while placing the ROI tag at the filename end."""

    def __init__(self, *args, window_tag, **kwargs):
        super().__init__(*args, **kwargs)
        self.window_tag = window_tag

    def _unique_filepath(self, *args, **kwargs):
        path = super()._unique_filepath(*args, **kwargs)
        path = path.with_name(f"{path.stem}_{self.window_tag}{path.suffix}")
        if path.exists() or path.with_suffix(".h5.partial").exists():
            raise FileExistsError(f"Tagged diagnostic output already exists: {path}")
        return path


def _volume_key(path: Path) -> str:
    """Identify a Windows drive or POSIX anchor for preflight volume separation."""
    return PureWindowsPath(str(path)).drive.casefold() or path.anchor


def _destinations(cfg: lab.Settings, output_dirs: dict[str, Path] | None) -> dict[str, Path]:
    """Resolve explicit destinations, defaulting to the setup cell's output folder."""
    destinations = {str(key): Path(value).resolve() for key, value in
                    (output_dirs or {"selected": cfg.output_dir}).items()}
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Diagnostic destinations must be different folders")
    if len({_volume_key(path) for path in destinations.values()}) != len(destinations):
        raise ValueError("Use only one destination per drive so the combined space budget is checked correctly")
    return destinations


def _analysis_report(report: dict) -> dict:
    """Build one small, chart-ready table with shared context instead of repeated dumps."""
    if report["section"] in ("4e", "4f"):
        packet = {key: report[key] for key in (
            "section", "utc", "status", "source", "error", "report_file", "execution_log",
            "diagnostic_settings", "drives", "limitations", "windows", "window_tag") if key in report}
        packet.update(report_schema_version="2.0", settings=lab.compact_settings(report["settings"]),
                      physical_acceptance=False)
        return _rounded_values(packet)
    settings = report["settings"]
    width, height = settings["roi"][2] - settings["roi"][0], settings["roi"][3] - settings["roi"][1]
    frame_bytes = width * height * 2
    packet = {key: report[key] for key in
              ("section", "utc", "status", "source", "error", "report_file", "execution_log") if key in report}
    packet.update(report_schema_version="2.0", analysis_request="Diagnose bottlenecks and make comparison charts.",
                  shape_y_x=[height, width], bytes_per_frame=frame_bytes,
                  target_fps=settings["target_fps"],
                  required_raw_MB_per_s=frame_bytes * settings["target_fps"] / 1e6,
                  margin_target_MB_per_s=frame_bytes * settings["target_fps"] * settings["storage_margin"] / 1e6,
                  physical_acceptance=False, drives={})
    if "diagnostic_settings" in report:
        packet["diagnostic_settings"] = report["diagnostic_settings"]
    if "windows" in report:
        packet["windows"] = report["windows"]
        packet["window_tag"] = report["window_tag"]
        if len(report["windows"]) != 1:
            for key in ("shape_y_x", "bytes_per_frame", "target_fps", "required_raw_MB_per_s", "margin_target_MB_per_s"):
                packet[key] = None  # Mixed sizes have per-window values, not one fictitious common rate.
        else:
            info = next(iter(report["windows"].values()))
            packet["shape_y_x"] = info["shape_y_x"]
            packet["bytes_per_frame"] = info["bytes_per_frame"]
            packet["target_fps"] = info["chart_reference_fps"]
            packet["required_raw_MB_per_s"] = (info["bytes_per_frame"] * info["chart_reference_fps"] / 1e6
                                               if info["chart_reference_fps"] is not None else None)
            packet["margin_target_MB_per_s"] = (packet["required_raw_MB_per_s"] * settings["storage_margin"]
                                                 if packet["required_raw_MB_per_s"] is not None else None)
    if report["section"] == "4c":
        packet["settings"] = lab.compact_settings(settings)
        packet["settings"].pop("batch_size", None)  # Actual batches are in the rows.
        packet["settings"].pop("target_fps", None)  # Per-window reference above; unknown is not 455 fps.
        packet["columns"] = ["drive", "repetition", "batch_size", "frames", "file",
                             "capture_fps", "saved_fps", "durable_fps", "durable_MB_per_s",
                             "capture_s", "write_close_s", "durable_s", "final_fsync_s",
                             "complete", "consecutive_frame_ids", "stage_timing_s", "queue_discard_fps", "window_tag"]
        packet["notes"] = "Sequential runs; caching may affect rankings. Per-window chart references are not measured cadence. Stages overlap; do not add them."
        packet["settings_readback"] = {
            label: {"model": value["sensor"].get("model"), "serial": value["sensor"].get("serial_number"),
                    "roi": value["roi"], "exposure_s": value["exposure_s"],
                    "readout": value["readout"], "trigger": value["trigger"],
                    "sdk_timing_configuration": value.get("sdk_timing_configuration")}
            for label, value in report.get("settings_readback", {}).items()
        }
    else:
        packet["BIN_metadata_sidecar"] = "frame_metadata_<window_tag>.jsonl (beside each BIN file)"
        packet["columns"] = ["drive", "format", "sync_policy", "frames", "file",
                             "write_close_MB_per_s", "durable_MB_per_s", "equivalent_durable_fps",
                             "write_close_s", "durable_s", "final_fsync_s", "second_fsync_s",
                             "periodic_fsync_s", "periodic_fsync_count", "pixels_equal_source",
                             "consecutive_frame_ids", "verification_s", "window_tag", "shape_y_x", "bytes_per_frame"]
        packet["notes"] = "Synthetic synchronous control; not camera/queue throughput. Preparation/readback excluded from durability."
        for key in ("expected_pixel_sha256", "source_preparation_s", "image_layout", "batch_size",
                    "compare_periodic_sync", "raw_GB_per_format"):
            if key in report:
                packet[key] = report[key]
    packet["rows"] = []
    for label, drive in report["drives"].items():
        directory = Path(drive["output_directory"])
        packet["drives"][label] = {key: drive[key] for key in
                                   ("output_directory", "free_GB_before", "output_GB", "within_output_cap",
                                    "interpretation", "diagnostics", "stage_output_budget_GB", "planned_raw_GB")
                                   if key in drive}
        for run in drive["runs"]:
            if report["section"] == "4c":
                saved, capture = run["saved"], run["capture_only"]
                values = [label, run["repetition"], run["batch_size"], saved.get("frames"), saved.get("file"),
                          capture.get("total_fps"), saved.get("total_fps"), saved.get("synced_fps"), run.get("durable_raw_MB_per_s"),
                          capture.get("elapsed_s"), saved.get("total_close_s"), saved.get("synced_s"),
                          saved.get("final_os_fsync_s"), saved.get("complete"), saved.get("consecutive_ids"),
                          saved.get("pipeline_stage_timing_s"), run.get("queue_discard", {}).get("total_fps"), run.get("window_tag")]
            else:
                values = [label, run["format"], run["sync_policy"], run["frames"], run["file"],
                          run["write_close_raw_MB_per_s"], run["durable_raw_MB_per_s"], run["equivalent_durable_fps"],
                          run["write_close_s"], run["durable_s"], run["final_os_fsync_s"], run["immediate_second_fsync_s"],
                          run["periodic_fsync_s"], run["periodic_fsync_count"], run["pixels_equal_source"],
                          run["consecutive_frame_ids"], run["verification_s"], run.get("window_tag"),
                          run.get("shape_y_x"), run.get("bytes_per_frame")]
            if values[4] is not None:
                values[4] = str(Path(values[4]).relative_to(directory))
            packet["rows"].append(values)
    return _rounded_values(packet)


def _rounded_values(value):
    """Use microsecond/report precision without long floating-point tails."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _rounded_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded_values(item) for item in value]
    return value


def _report(section: str, cfg: lab.Settings, destinations: dict[str, Path], operation, *, tag=None) -> dict:
    """Save results and a progress log silently, including ordinary failures."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    destination = next(iter(destinations.values()))
    destination.mkdir(parents=True, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    folder_name = f"BENCHMARK_ONLY_section_{section}_{stamp}{suffix}"
    report_path = destination / f"section_{section}_{stamp}{suffix}.json"
    log_path = report_path.with_suffix(".log")
    report = {"section": section, "utc": datetime.now(timezone.utc).isoformat(),
              "status": "FAILED", "source": cfg.source if section in ("4c", "4e", "4f") else "synthetic",
              "settings": asdict(cfg), "report_file": str(report_path),
              "execution_log": str(log_path), "drives": {}, "physical_acceptance": False}
    interrupted = None
    with log_path.open("w", encoding="utf-8") as log, redirect_stdout(log):
        try:
            operation(report, folder_name)
            report["status"] = "EXECUTED"
        except BaseException as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            print(report["error"])
            traceback.print_exc(file=log)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = exc
        finally:
            report_path.write_text(json.dumps(_analysis_report(report), indent=1, default=lab.json_default),
                                   encoding="utf-8")
    if interrupted is not None:
        raise interrupted
    return report


def _preflight(cfg, destinations, frames_per_drive, files_per_drive, cap_gb, *, approved_stage=False):
    """Check the complete retained output budget on every drive before any run."""
    if not math.isfinite(cap_gb) or not 0 < cap_gb <= (200 if approved_stage else 30):
        raise ValueError(f"Output cap must be finite, positive and at most {200 if approved_stage else 30} GB per destination")
    frame_bytes = (cfg.roi[2] - cfg.roi[0]) * (cfg.roi[3] - cfg.roi[1]) * 2
    raw = frames_per_drive * frame_bytes
    # JSON/VLEN metadata scales with frame count, especially for very small ROIs.
    # Reserve 4 KiB per frame plus file-level overhead before any payload write.
    reserve = frames_per_drive * 4096 + files_per_drive * 100_000_000
    if raw + reserve > cap_gb * 1e9:
        raise ValueError("Planned data plus metadata reserve exceeds the per-drive cap; reduce run sizes")
    free = {}
    for label, destination in destinations.items():
        lab.prepare(replace(cfg, output_dir=destination,
                            max_section_raw_gb=max(cfg.max_section_raw_gb, cap_gb) if approved_stage else cfg.max_section_raw_gb),
                    frames_per_drive)
        free[label] = shutil.disk_usage(destination).free
        if free[label] < 1.5 * (raw + reserve) + 100_000_000:
            raise OSError(f"Not enough free space for all retained outputs on {label}")
    return frame_bytes, raw, free


def stage_frames(cfg, budget_gb, files):
    """Fit a stage's total retained payload plus conservative metadata/file reserves.

    Explicit approved budgets are decimal 20/40/100/200 GB. Return a per-file
    frame count; never treat the stage budget as a budget for every variant.
    """
    if budget_gb not in (20.0, 40.0, 100.0, 200.0) or files < 1:
        raise ValueError("Choose an approved 20/40/100/200 GB total stage and positive file count")
    frame_bytes = (cfg.roi[2] - cfg.roi[0]) * (cfg.roi[3] - cfg.roi[1]) * 2
    frames = int((budget_gb * 1e9 - files * 100_000_000) // (files * (frame_bytes + 4096)))
    if frames < 1:
        raise ValueError("Stage budget does not fit a frame in every output")
    return frames


def stage_profile(drive, d_stage_gb=20):
    """Select one notebook stage sized to fit the 45 GB C: space allowance."""
    if drive == "C":
        return 20.0
    if drive == "D" and d_stage_gb in (20, 100, 200):
        return float(d_stage_gb)
    raise ValueError("Choose drive C or D; D stage must be 20, 100 or 200 GB")


def _measure_output(drive_report, folder: Path, cap_gb: float):
    """Measure only this diagnostic's retained files, never existing research data."""
    size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
    drive_report.update(output_bytes=size, output_GB=size / 1e9,
                        within_output_cap=size <= cap_gb * 1e9)
    if not drive_report["within_output_cap"]:
        raise OSError("Measured diagnostic output exceeded its configured cap")


def run_batch_size_trial(
    cfg: lab.Settings, *, output_dirs: dict[str, Path] | None = None,
    batch_sizes: tuple[int, ...] = (4, 16, 64), frames: int = 2000,
    repeats: int = 1, max_output_gb_per_drive: float = 8.0,
    stage_output_gb: float | None = None, selected_batch_size: int | None = None,
    verify_pixels: bool = True,
    window: str | None = None,
) -> dict:
    """4C: compare capture, bounded queue-to-discard and production HDF5.

    Physical configuration, shutter/exposure and Auto Sequence are unchanged.
    Each batch records exactly the same frame count. All requested drives are
    preflighted together. Optional 20 GB C or 20/100/200 GB D stage budgets
    include every retained file and metadata reserve. Stages >=100 GB require
    an explicit selected batch and retain one continuous file. No stage auto-runs.
    Pixel hashes are enabled equally across all three measured modes; an
    uninstrumented short control reports diagnostic overhead separately.
    """
    cfg, window_info = window_settings(cfg, window)
    if cfg.source == "physical" and not cfg.expected_serial:
        cfg = replace(cfg, expected_serial=lab.DEFAULT_CAMERA_SERIAL)
    destinations = _destinations(cfg, output_dirs)

    def operation(report, folder_name):
        report.update(window_tag=window_info["tag"], windows={window_info["name"]: window_info})
        run_frames, batches, run_repeats, cap = frames, batch_sizes, repeats, max_output_gb_per_drive
        if window in ("medium", "large") and batch_sizes == (4, 16, 64):
            batches = (4, 16)  # Keep the existing 256-MiB bound for larger image batches.
        if stage_output_gb is not None:
            if stage_output_gb >= 100:
                if not isinstance(selected_batch_size, int) or selected_batch_size < 1:
                    raise ValueError("For a 100/200 GB continuous stage, set selected_batch_size from the screening results")
                batches, run_repeats = (selected_batch_size,), 1
            cap = stage_output_gb
            run_frames = stage_frames(cfg, cap, len(batches) * run_repeats)
        if (not isinstance(frames, int) or frames < 1 or
                not isinstance(repeats, int) or repeats < 1 or not batch_sizes or
                any(not isinstance(size, int) or size < 1 for size in batch_sizes)):
            raise ValueError("Frame count, repeats and batch sizes must be positive integers")
        if cfg.source == "physical" and not cfg.expected_serial:
            raise ValueError("Set the reviewed physical camera serial before 4C")
        report["diagnostic_settings"] = {"repeats": run_repeats,
                                          "batch_sizes": list(batches), "max_output_GB_per_drive": cap,
                                          "frames_per_run": run_frames, "stage_output_GB": stage_output_gb,
                                          "source_hashing_all_modes": verify_pixels,
                                          "user_entered_camware_fps": cfg.camware_fps,
                                          "uninstrumented_control_max_frames": 2000,
                                          "CamWare_reference": "Confirm measured memory-recording cadence; displayed/calculated FPS is not host-wall FPS"}
        total_frames = run_frames * run_repeats * len(batches)
        frame_bytes, raw, free = _preflight(
            cfg, destinations, total_frames, run_repeats * len(batches), cap,
            approved_stage=stage_output_gb is not None,
        )
        if min(run_frames, max(batches)) * frame_bytes > 256 * 2**20:
            raise ValueError("Diagnostic batch exceeds 256 MiB; reduce batch size")
        report.update(frames_per_run=run_frames, repeats=run_repeats, batch_sizes=list(batches),
                      planned_raw_GB_per_drive=raw / 1e9, max_output_GB_per_drive=cap,
                      limitations=["Runs are sequential and changing system load can affect rankings.",
                                   "Short-run caching does not establish sustained storage throughput.",
                                   "Larger batches also change the time between the writer's 32-batch HDF5 flushes."])
        for label, destination in destinations.items():
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            drive = {"output_directory": str(folder), "free_GB_before": free[label] / 1e9,
                     "runs": [], "diagnostics": [], "stage_output_budget_GB": cap,
                     "planned_raw_GB": raw / 1e9}
            report["drives"][label] = drive
            with lab.configured_camera(replace(cfg, output_dir=folder)) as camera:
                report.setdefault("settings_readback", {})[label] = lab.snapshot(camera)
                if cfg.source == "physical" and getattr(camera, "_cam", None) is not None:
                    # Once per session, outside all rate intervals; no SDK setter.
                    try:
                        config = camera._cam.configuration
                        timing_config = {key: config[key] for key in
                                         ("pixel rate", "binning", "roi", "exposure time", "delay time", "trigger")
                                         if key in config}
                    except Exception as exc:
                        timing_config = {"readback_error": f"{type(exc).__name__}: {exc}"}
                    report["settings_readback"][label]["sdk_timing_configuration"] = timing_config
                    if "binning" in timing_config and tuple(timing_config["binning"][:2]) != (1, 1):
                        raise ValueError("Camera reports binning; select unbinned settings before 4C")
                for repetition in range(run_repeats):
                    order = batches if repetition % 2 == 0 else tuple(reversed(batches))
                    for batch in order:
                        run_cfg = replace(cfg, output_dir=folder, batch_size=batch,
                                          speed_frames=run_frames, repeats=1)
                        run = {"repetition": repetition + 1, "batch_size": batch, "window_tag": window_info["tag"],
                               "capture_only": {}, "queue_discard": {}, "saved": {}}
                        details = {"repetition": repetition + 1, "batch_size": batch, "window_tag": window_info["tag"]}
                        drive["runs"].append(run)
                        drive["diagnostics"].append(details)
                        try:
                            short_frames = min(run_frames, 2000)
                            details["uninstrumented_control"] = lab.capture_only(camera, run_cfg, short_frames)
                            # Same count/window for a useful observer/hash overhead check.
                            with capture_diag.profiled_camera(camera, hash_pixels=verify_pixels) as (profiled, probe):
                                details["instrumented_short_control"] = lab.capture_only(profiled, run_cfg, short_frames)
                            details["instrumented_short_profile"] = probe.summary()
                            for mode in ("capture_only", "queue_discard", "saved"):
                                with capture_diag.profiled_camera(camera, hash_pixels=verify_pixels) as (profiled, probe):
                                    try:
                                        if mode == "capture_only":
                                            run[mode].update(lab.capture_only(profiled, run_cfg, run_frames))
                                        elif mode == "queue_discard":
                                            run[mode].update(capture_diag.queue_to_discard(profiled, run_cfg, run_frames))
                                        else:
                                            managers = []
                                            def make_manager(*args, **kwargs):
                                                manager = _WindowManager(*args, window_tag=window_info["tag"], **kwargs)
                                                managers.append(manager)
                                                return manager
                                            try:
                                                run[mode].update(lab.saved_run(profiled, run_cfg, f"BENCHMARK_ONLY_batch_{batch}",
                                                                               run_frames, manager_class=make_manager))
                                            finally:
                                                if managers:
                                                    run[mode]["pipeline_stage_timing_s"] = managers[0].last_step_timing
                                    finally:
                                        run[mode]["profile"] = probe.summary()
                                        details[mode] = run[mode]["profile"]
                                if mode == "saved":
                                    saved = run[mode]
                                    if verify_pixels:
                                        manifest = Path(saved["file"]).with_suffix(".pixels.sha256.json")
                                        manifest.write_text(json.dumps({"shape_frame_y_x": saved["shape_frame_y_x"],
                                                                        "blocks": probe.blocks}, separators=(",", ":")), encoding="utf-8")
                                        with manifest.open("r+b", buffering=0) as handle:
                                            os.fsync(handle.fileno())
                                        details["pixel_integrity"] = {"status": "FAILED", "manifest": str(manifest)}
                                        details["pixel_integrity"].update(verify_hdf5_pixels(Path(saved["file"]), manifest))
                                        details["pixel_integrity"]["status"] = "VERIFIED"
                                    else:
                                        details["pixel_integrity"] = {"status": "NOT_TESTED"}
                                    run["durable_raw_MB_per_s"] = saved["synced_fps"] * frame_bytes / 1e6
                        except BaseException as exc:
                            details["error"] = f"{type(exc).__name__}: {exc}"
                            details["partial_files"] = [str(path) for path in folder.glob("*.partial")]
                            raise
                        finally:
                            details["interpretation"] = capture_diag.interpret(
                                run["capture_only"], run["queue_discard"], run["saved"],
                                window_info["chart_reference_fps"], cfg.source)
                            details["interpretation"]["pixel_integrity_status"] = details.get("pixel_integrity", {}).get("status", "NOT_COMPLETED")
            drive["median_durable_fps_by_batch"] = {
                str(batch): float(np.median([run["saved"]["synced_fps"] for run in drive["runs"]
                                            if run["batch_size"] == batch]))
                for batch in batches
            }
            drive["fastest_measured_batch"] = max(drive["median_durable_fps_by_batch"],
                                                 key=drive["median_durable_fps_by_batch"].get)
            _measure_output(drive, folder, cap)

    return _report("4c", cfg, destinations, operation, tag=window_info["tag"])


def _blocks(bank, frames):
    """Yield bounded little-endian uint16 blocks with a unique 32-bit frame marker."""
    for start in range(0, frames, len(bank)):
        block = bank[:min(len(bank), frames - start)].copy()
        ids = np.arange(start, start + len(block), dtype=np.uint32)
        block[:, 0, 0] = ids & 65535
        block[:, 0, 1] = ids >> 16
        yield start, block


class _PixelReferenceCamera:
    """Delegate camera operations while hashing each source batch before queueing.

    Only hashes and ranges are retained, not image arrays. Hashing is in the
    producer path and its possible FPS cost must be measured against a baseline.
    """

    def __init__(self, camera):
        self.camera = camera
        self.blocks = []
        self.hash_s = 0.0

    def __getattr__(self, name):
        return getattr(self.camera, name)

    def iter_frames(self, *args, **kwargs):
        """Yield original batches after SHA-256 over canonical little-endian pixels."""
        iterator = self.camera.iter_frames(*args, **kwargs)
        start = 0
        try:
            for images, metadata in iterator:
                began = time.perf_counter()
                pixels = np.ascontiguousarray(images, dtype="<u2")
                digest = hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()
                self.hash_s += time.perf_counter() - began
                self.blocks.append({"start": start, "frames": len(images), "sha256": digest})
                start += len(images)
                yield images, metadata
        finally:
            iterator.close()


def verify_hdf5_pixels(path: Path, manifest_path: Path) -> dict:
    """Read every saved pixel against pre-write SHA-256 references, in bounded blocks.

    The acquisition must be closed and synced first. No camera is opened. A
    mismatch, missing frame, incomplete file or invalid layout raises an error.
    SHA-256 verifies storage against host source pixels, not sensor correctness.
    Cached reads are allowed; this does not prove persistence through power loss.
    """
    started = time.perf_counter()
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    count = 0
    with h5py.File(path, "r") as h5:
        images = h5["images"]
        if (images.shape != tuple(manifest["shape_frame_y_x"]) or images.dtype != np.dtype("<u2")
                or not h5.attrs.get("complete", False)
                or int(h5.attrs.get("frames_written", -1)) != len(images)):
            raise AssertionError("HDF5 shape/dtype/completion differs from source manifest")
        for block in manifest["blocks"]:
            start, frames = block["start"], block["frames"]
            if start != count or frames < 1 or start + frames > len(images):
                raise AssertionError("Pixel manifest has a gap or invalid block range")
            pixels = np.ascontiguousarray(images[start:start + frames], dtype="<u2")
            if hashlib.sha256(memoryview(pixels).cast("B")).hexdigest() != block["sha256"]:
                raise AssertionError(f"HDF5 pixels differ from source at frames {start}:{start + frames}")
            count += frames
        if count != len(images):
            raise AssertionError("Pixel manifest does not cover every saved frame")
    elapsed = time.perf_counter() - started
    return {"pixels_equal_source": True, "frames_verified": count,
            "verification_s": elapsed,
            "verification_raw_MB_per_s": count * np.prod(manifest["shape_frame_y_x"][1:]) * 2 / 1e6 / elapsed}


def run_hdf5_pixel_verification_trial(
    cfg: lab.Settings, *, output_dirs: dict[str, Path] | None = None,
    frames: int = 2000, fps_tolerance_percent: float = 5.0,
    max_output_gb_per_drive: float = 8.0,
) -> dict:
    """4E: baseline and hashed production acquisitions, then full HDF5 pixel readback.

    Uses CONFIG's reviewed camera settings, Auto Sequence and bounded writer.
    Default retains two 2000-frame files (~2.62 GB/drive at 2560x128).
    FPS includes capture/write/close, excluding fsync and post-acquisition checks;
    durable FPS includes fsync. Total trial time includes all checks. The measured
    source-hash overhead is reported honestly; unchanged FPS is never assumed.
    """
    if cfg.source == "physical" and not cfg.expected_serial:
        cfg = replace(cfg, expected_serial=lab.DEFAULT_CAMERA_SERIAL)
    destinations = _destinations(cfg, output_dirs)

    def operation(report, folder_name):
        if (not isinstance(frames, int) or frames < 1 or
                not math.isfinite(fps_tolerance_percent) or fps_tolerance_percent < 0):
            raise ValueError("Frames must be a positive integer; FPS tolerance must be finite and nonnegative")
        if cfg.source == "physical" and not cfg.expected_serial:
            raise ValueError("Set the reviewed physical camera serial before 4E")
        report["diagnostic_settings"] = {"frames_per_run": frames,
                                          "fps_tolerance_percent": fps_tolerance_percent,
                                          "max_output_GB_per_drive": max_output_gb_per_drive}
        report["limitations"] = [
            "Sequential short runs and changing load/caches affect the baseline comparison.",
            "Source SHA-256 costs CPU during capture; full readback begins only after close and fsync.",
            "Readback may use OS cache; no power-loss guarantee, sensor or external-trigger validation.",
            "Manifest and data must both be retained; hashes detect accidental changes, not malicious tampering."]
        frame_bytes, _, free = _preflight(cfg, destinations, frames * 2, 2, max_output_gb_per_drive)
        if min(frames, cfg.batch_size) * frame_bytes > 256 * 2**20:
            raise ValueError("Pixel verification block exceeds 256 MiB; reduce CONFIG.batch_size")
        for label, destination in destinations.items():
            began = time.perf_counter()
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=False)
            drive = {"output_directory": str(folder), "free_GB_before": free[label] / 1e9}
            report["drives"][label] = drive
            run_cfg = replace(cfg, output_dir=folder)
            with lab.configured_camera(run_cfg) as camera:
                baseline_started = time.perf_counter()
                baseline = lab.saved_run(camera, run_cfg, "BENCHMARK_ONLY_baseline", frames)
                baseline_wall_s = time.perf_counter() - baseline_started
                drive["baseline"] = {key: baseline[key] for key in
                                     ("file", "frames", "total_fps", "synced_fps", "total_close_s", "synced_s")}
                reference = _PixelReferenceCamera(camera)
                saved_started = time.perf_counter()
                saved = lab.saved_run(reference, run_cfg, "BENCHMARK_ONLY_pixel_reference", frames)
                saved_wall_s = time.perf_counter() - saved_started
            drive["verified_run"] = {key: saved[key] for key in
                                     ("file", "frames", "total_fps", "synced_fps", "total_close_s", "synced_s",
                                      "final_os_fsync_s", "consecutive_ids")}
            manifest_path = Path(saved["file"]).with_suffix(".pixels.sha256.json")
            post_started = time.perf_counter()
            manifest_path.write_text(json.dumps({"algorithm": "sha256", "dtype": "<u2",
                                                 "shape_frame_y_x": saved["shape_frame_y_x"],
                                                 "blocks": reference.blocks}, separators=(",", ":")), encoding="utf-8")
            with manifest_path.open("r+b", buffering=0) as handle:
                os.fsync(handle.fileno())
            drive["reference_manifest"] = str(manifest_path)
            drive["source_hash_s"] = reference.hash_s
            drive["verification"] = {"pixels_equal_source": False}
            drive["verification"].update(verify_hdf5_pixels(Path(saved["file"]), manifest_path))
            post_s = time.perf_counter() - post_started
            delta = 100 * (saved["total_fps"] / baseline["total_fps"] - 1)
            drive.update(acquisition_fps_change_percent=delta,
                         acquisition_fps_within_tolerance=abs(delta) <= fps_tolerance_percent,
                         post_acquisition_added_s=post_s,
                         source_hash_s_in_acquisition=reference.hash_s,
                         durable_plus_verification_s=saved["synced_s"] + post_s,
                         fps_including_durability_and_verification=frames / (saved["synced_s"] + post_s),
                         measured_durable_time_change_vs_baseline_s=saved["synced_s"] - baseline["synced_s"],
                         measured_total_added_vs_baseline_s=saved["synced_s"] + post_s - baseline["synced_s"],
                         baseline_all_checks_s=baseline_wall_s,
                         verified_run_all_checks_s=saved_wall_s + post_s,
                         measured_wall_time_added_vs_baseline_s=saved_wall_s + post_s - baseline_wall_s,
                         whole_trial_s=time.perf_counter() - began)
            _measure_output(drive, folder, max_output_gb_per_drive)

    return _report("4e", cfg, destinations, operation)


def _metadata(start, count):
    """Return identical synthetic per-frame metadata for both format controls."""
    return [{"frame_id": index, "timestamp_source": "synthetic_format_control"}
            for index in range(start, start + count)]


def _sync_paths(paths):
    """Request OS durability for each closed or explicitly flushed diagnostic file."""
    started = time.perf_counter()
    for path in paths:
        with path.open("r+b", buffering=0) as handle:
            os.fsync(handle.fileno())
    return time.perf_counter() - started


def _verify_control(path, metadata_path, kind, bank, frames, expected_digest):
    """Check every saved pixel, marker and metadata ID outside throughput timing."""
    started = time.perf_counter()
    digest = hashlib.sha256()
    if kind == "hdf5":
        with h5py.File(path, "r") as h5:
            if (h5["images"].shape != (frames, *bank.shape[1:]) or
                    h5["images"].dtype != np.dtype("<u2") or not h5.attrs["complete"] or
                    int(h5.attrs["frames_written"]) != frames or
                    h5["frame_metadata/json"].shape != (frames,)):
                raise AssertionError("HDF5 control shape, dtype or completion differs")
            for start, expected in _blocks(bank, frames):
                actual = h5["images"][start:start + len(expected)]
                if not np.array_equal(actual, expected):
                    raise AssertionError(f"HDF5 pixels differ at block {start}")
                digest.update(actual.tobytes(order="C"))
                rows = [json.loads(row) for row in h5["frame_metadata/json"][start:start + len(expected)]]
                if rows != _metadata(start, len(expected)):
                    raise AssertionError("HDF5 metadata differs")
    else:
        if path.stat().st_size != frames * bank.shape[1] * bank.shape[2] * 2:
            raise AssertionError("BIN size differs from its manifest")
        with path.open("rb") as handle, metadata_path.open(encoding="utf-8") as metadata:
            for start, expected in _blocks(bank, frames):
                data = handle.read(expected.nbytes)
                actual = np.frombuffer(data, dtype="<u2").reshape(expected.shape)
                if not np.array_equal(actual, expected):
                    raise AssertionError(f"BIN pixels differ at block {start}")
                digest.update(data)
                rows = [json.loads(metadata.readline()) for _ in range(len(expected))]
                if rows != _metadata(start, len(expected)):
                    raise AssertionError("BIN metadata differs")
            if metadata.readline():
                raise AssertionError("BIN metadata contains extra frames")
    if digest.hexdigest() != expected_digest:
        raise AssertionError("Readback pixel checksum differs from prepared source")
    return {"pixels_equal_source": True, "consecutive_frame_ids": True,
            "pixel_sha256": digest.hexdigest(), "verification_s": time.perf_counter() - started}


def _write_control(folder, kind, bank, frames, expected_digest, sync_every_bytes, *, window_tag=None):
    """Write one matched format control with periodic optional and final mandatory fsync.

    HDF5 uses the production chunk shape and _write_batch implementation; BIN
    carries identical JSON metadata in a sidecar. This isolates storage I/O,
    and does not reproduce the production asynchronous capture/queue pipeline.
    """
    folder.mkdir(parents=True, exist_ok=False)
    suffix = f"_{window_tag}" if window_tag else ""
    path = folder / f"BENCHMARK_ONLY_frames{suffix}.{'h5' if kind == 'hdf5' else 'bin'}"
    metadata_path = folder / f"frame_metadata{suffix}.jsonl"
    paths = [path] if kind == "hdf5" else [path, metadata_path]
    partial = path.with_suffix(path.suffix + ".partial")
    periodic_sync_s, periodic_count = 0.0, 0
    frame_bytes = bank.shape[1] * bank.shape[2] * 2
    written, next_sync = 0, sync_every_bytes
    progress = capture_diag.Samples()
    cpu_started = time.process_time()
    started = time.perf_counter()

    def checkpoint(flush):
        nonlocal periodic_sync_s, periodic_count, next_sync
        if sync_every_bytes and written * frame_bytes >= next_sync:
            flush()
            periodic_sync_s += _sync_paths([partial] if kind == "hdf5" else [partial, metadata_path])
            periodic_count += 1
            next_sync = written * frame_bytes + sync_every_bytes

    if kind == "hdf5":
        writer = CameraScanManager(lab.MockPcoCamera(), folder)
        with h5py.File(partial, "x") as h5:
            h5.attrs.update(schema_version="diagnostic-1.0", source="synthetic",
                            complete=False, frames_written=0, storage_compression="none")
            if window_tag is not None:
                h5.attrs["diagnostic_window_tag"] = window_tag
            images = h5.create_dataset("images", shape=(frames, *bank.shape[1:]),
                                      dtype="<u2", chunks=_frame_chunk_shape(*bank.shape[1:]))
            images.attrs.update(dimension_order="[frame, y, x]", units="ADU")
            metadata = h5.create_dataset("frame_metadata/json", shape=(frames,),
                                        dtype=h5py.string_dtype("utf-8"), chunks=(min(256, frames),))
            for number, (start, block) in enumerate(_blocks(bank, frames), 1):
                # Retain the production pixel and JSON serialization operations
                # without involving its asynchronous acquisition queue.
                writer._write_batch(images, metadata, start, block, _metadata(start, len(block)))
                written += len(block)
                if number % 32 == 0:
                    h5.attrs["frames_written"] = written
                    h5.flush()
                checkpoint(h5.flush)
                progress.add(time.perf_counter() - started, written, written * frame_bytes)
            h5.attrs.update(complete=True, frames_written=written)
            h5.flush()
    else:
        with partial.open("xb", buffering=0) as binary, metadata_path.open("x", encoding="utf-8", newline="\n") as metadata:
            for number, (start, block) in enumerate(_blocks(bank, frames), 1):
                data = memoryview(block).cast("B")
                while data:
                    count = binary.write(data)
                    if not count:
                        raise OSError("BIN write made no progress")
                    data = data[count:]
                metadata.writelines(json.dumps(row, sort_keys=True) + "\n" for row in _metadata(start, len(block)))
                written += len(block)
                if number % 32 == 0:
                    metadata.flush()
                checkpoint(metadata.flush)
                progress.add(time.perf_counter() - started, written, written * frame_bytes)
            metadata.flush()
    partial.rename(path)
    close_s = time.perf_counter() - started
    final_sync_s = _sync_paths(paths)
    durable_s = time.perf_counter() - started
    cpu_s = time.process_time() - cpu_started
    second_sync_s = _sync_paths(paths)
    checked = _verify_control(path, metadata_path, kind, bank, frames, expected_digest)
    return {"format": kind, "file": str(path), "metadata_file": str(metadata_path) if kind == "bin" else None,
            "frames": frames, "raw_bytes": frames * frame_bytes,
            "output_bytes": sum(item.stat().st_size for item in paths),
            "write_close_s": close_s, "durable_s": durable_s,
            "write_close_raw_MB_per_s": frames * frame_bytes / 1e6 / close_s,
            "durable_raw_MB_per_s": frames * frame_bytes / 1e6 / durable_s,
            "equivalent_durable_fps": frames / durable_s,
            "final_os_fsync_s": final_sync_s, "immediate_second_fsync_s": second_sync_s,
            "periodic_fsync_s": periodic_sync_s, "periodic_fsync_count": periodic_count,
            "cpu_s": cpu_s, "write_progress_columns": ["seconds", "frames", "raw_bytes"],
            "write_progress": progress.rows,
            "sync_policy": "periodic_and_final" if sync_every_bytes else "final_only", **checked}


def run_bin_hdf5_trial(
    cfg: lab.Settings, *, output_dirs: dict[str, Path] | None = None,
    target_raw_gb_per_format: float = 2.0, batch_size: int = 16,
    compare_periodic_sync: bool = True, sync_every_gb: float = 0.25,
    max_output_gb_per_drive: float = 30.0,
    stage_output_gb: float | None = None, include_bin: bool = True,
    windows: tuple[str, ...] | None = None,
) -> dict:
    """4D: synthetic format/sync comparison, optionally sweeping all ROI windows.

    The notebook requests small/medium/large automatically; library callers can
    preserve their current ROI with windows=None. A stage's total retained GB
    is divided across ALL windows/formats/policies, never multiplied by three.
    Every requested window and the combined free-space budget are preflighted
    before writes. No camera is opened. Equivalent FPS is storage throughput.
    """
    if windows is not None and (not windows or len(set(windows)) != len(windows)):
        raise ValueError("Choose a nonempty list of distinct small/medium/large windows")
    configurations = [window_settings(cfg, name) for name in (windows or (None,))]
    destinations = _destinations(cfg, output_dirs)
    report_tag = "all_windows" if len(configurations) > 1 else configurations[0][1]["tag"]

    def operation(report, folder_name):
        if (not math.isfinite(target_raw_gb_per_format) or target_raw_gb_per_format <= 0 or
                not isinstance(batch_size, int) or batch_size < 1 or
                not math.isfinite(sync_every_gb) or sync_every_gb <= 0):
            raise ValueError("Payload GB, batch size and sync interval GB must be positive and finite")
        policies = [0, max(1, int(sync_every_gb * 1e9))] if compare_periodic_sync else [0]
        formats = ("hdf5", "bin") if include_bin else ("hdf5",)
        controls = len(formats) * len(policies)
        count = len(configurations)
        cap = max_output_gb_per_drive if stage_output_gb is None else stage_output_gb
        report.update(window_tag=report_tag, windows={}, compare_periodic_sync=compare_periodic_sync)
        report["diagnostic_settings"] = {
            "batch_size": batch_size, "compare_periodic_sync": compare_periodic_sync,
            "sync_every_GB": sync_every_gb, "stage_output_GB": stage_output_gb,
            "include_bin": include_bin, "max_output_GB_per_drive": cap,
            "window_order": [info["name"] for _, info in configurations],
            "budget_note": "One total budget across every window, format and sync policy.",
        }
        plans, raw_total, reserved_total = [], 0, 0
        for window_cfg, info in configurations:
            width, height = info["shape_y_x"][1], info["shape_y_x"][0]
            if width < 2 or height < 1:
                raise ValueError("Synthetic frame markers require at least two columns and one row")
            frame_bytes = info["bytes_per_frame"]
            frames = (max(1, int(target_raw_gb_per_format * 1e9) // frame_bytes) if stage_output_gb is None
                      else stage_frames(window_cfg, stage_output_gb, controls * count))
            bank_frames = min(batch_size, frames)
            if bank_frames * frame_bytes > 256 * 2**20:
                raise ValueError("Prepared synthetic bank exceeds 256 MiB; reduce batch size")
            synthetic_cfg = replace(window_cfg, source="mock", expected_serial=None)
            _preflight(synthetic_cfg, destinations, frames * controls, controls, cap / count,
                       approved_stage=stage_output_gb is not None)
            raw = frames * controls * frame_bytes
            reserve = frames * controls * 4096 + controls * 100_000_000
            raw_total += raw
            reserved_total += raw + reserve
            info = dict(info, frames_per_output=frames, raw_GB_per_output=frames * frame_bytes / 1e9)
            report["windows"][info["name"]] = info
            plans.append((info, frames, bank_frames))
        if reserved_total > cap * 1e9:
            raise ValueError("Combined windows plus metadata reserve exceed the total per-drive cap")
        for label, destination in destinations.items():
            free = shutil.disk_usage(destination).free
            if free < 1.5 * reserved_total + 100_000_000:
                raise OSError(f"Not enough free space for all windows on {label}")
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=False)
            report["drives"][label] = {"output_directory": str(folder), "free_GB_before": free / 1e9,
                                        "runs": [], "stage_output_budget_GB": cap,
                                        "planned_raw_GB": raw_total / 1e9}
        report.update(planned_raw_GB_per_drive=raw_total / 1e9, max_output_GB_per_drive=cap,
                      batch_size=batch_size,
                      limitations=["Synthetic synchronous control; equivalent FPS is not measured camera FPS.",
                                   "Preparation and pixel readback are outside durable throughput.",
                                   "Sequential windows/variants and caching can affect rankings; every run ends with fsync.",
                                   "The stage budget is shared across windows; compare matched-window rows."])
        for window_index, (info, frames, bank_frames) in enumerate(plans):
            height, width = info["shape_y_x"]
            preparation = time.perf_counter()
            bank = np.random.default_rng(42).integers(0, 65536, (bank_frames, height, width), dtype=np.uint16).astype("<u2")
            expected = hashlib.sha256()
            for _, block in _blocks(bank, frames):
                expected.update(block.tobytes(order="C"))
            info.update(source_preparation_s=time.perf_counter() - preparation,
                        expected_pixel_sha256=expected.hexdigest())
            if count == 1:  # Preserve existing library report fields for one window.
                report.update(frames_per_format=frames, raw_GB_per_format=info["raw_GB_per_output"],
                              source_preparation_s=info["source_preparation_s"],
                              expected_pixel_sha256=expected.hexdigest(),
                              image_layout={"shape": [frames, height, width], "dtype": "uint16",
                                            "byte_order": "little-endian", "dimension_order": "[frame, y, x]"})
            for drive_index, (label, drive) in enumerate(report["drives"].items()):
                folder = Path(drive["output_directory"])
                drive_order = int(label.upper() == "D") if label.upper() in ("C", "D") else drive_index
                reverse = (drive_order + window_index) % 2
                order = tuple(reversed(formats)) if reverse else formats
                for policy in policies:
                    name = "periodic" if policy else "final"
                    for kind in order:
                        run = _write_control(folder / f"{kind}_{name}_{info['tag']}", kind, bank,
                                             frames, expected.hexdigest(), policy, window_tag=info["tag"])
                        run.update(window_tag=info["tag"], shape_y_x=info["shape_y_x"], bytes_per_frame=info["bytes_per_frame"])
                        drive["runs"].append(run)
                drive["diagnostics"] = [{key: run[key] for key in
                    ("window_tag", "format", "sync_policy", "cpu_s", "write_progress_columns", "write_progress")}
                    for run in drive["runs"]]
                drive["interpretation"] = storage_interpretation(drive["runs"])
                _measure_output(drive, folder, cap)
            del bank  # Release this window's bank before preparing a larger one.

    return _report("4d", cfg, destinations, operation, tag=report_tag)


def storage_interpretation(runs):
    """Interpret synthetic storage timing without claiming physical capture performance."""
    findings = ["Synthetic storage only: no camera, USB, producer queue or trigger conclusion.",
                "A shorter final fsync is a gain only if total durable time also improves."]
    for window in sorted({run.get("window_tag", "current") for run in runs}):
        for kind in sorted({run["format"] for run in runs}):
            pair = {run["sync_policy"]: run for run in runs if run["format"] == kind and run.get("window_tag", "current") == window}
            if {"final_only", "periodic_and_final"} <= pair.keys():
                ratio = pair["final_only"]["durable_s"] / pair["periodic_and_final"]["durable_s"]
                findings.append(f"{window} / {kind}: periodic/final-only durable throughput ratio {ratio:.3f}; sequential run load may affect it.")
    return {"findings": findings,
            "next_action": "Compare sustained write progress and full durable rates with physical 4C; repeat matched format trials before retiring BIN.",
            "BIN_winner_conclusive": False}
