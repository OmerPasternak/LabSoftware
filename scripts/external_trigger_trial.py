"""4H: external-trigger diagnostics through the current GUI acquisition pipeline.

Importing this module never opens hardware. Physical execution applies the
reviewed Settings via configured_camera; no commands go to a pulse generator
or oscilloscope. Raw pixels/metadata remain unchanged. Scope edge analysis is
separate from camera/host timestamps: their clocks are not interchangeable.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import csv
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import traceback

import numpy as np

from hhg_control.camera.drivers.base_camera import TriggerMode
from hhg_control.camera.sequencer.scan_manager import CameraScanManager
from hhg_control.safe_io import filesystem_path
from scripts import capture_diagnostics as capture
from scripts import lab_computer_acceptance as lab
from scripts import storage_diagnostics as storage


def _stats(values):
    """Summarize finite scalar observations; callers choose and label units."""
    if not len(values):
        return None
    data = np.asarray(values, dtype=np.float64)
    return dict(count=len(data), mean=float(data.mean()), std=float(data.std()),
                min=float(data.min()), p50=float(np.percentile(data, 50)),
                p95=float(np.percentile(data, 95)), p99=float(np.percentile(data, 99)),
                max=float(data.max()))


class _TimingCamera:
    """Pass raw batches through, retaining scalar timestamps only (<=100k frames)."""

    def __init__(self, camera, probe):
        self.camera, self.probe = camera, probe
        self.host_ns, self.camera_s = [], []

    def __getattr__(self, name):
        return getattr(self.camera, name)

    def iter_frames(self, *args, **kwargs):
        """Observe timing without modifying, dropping, or holding image batches."""
        iterator = self.camera.iter_frames(*args, **kwargs)
        try:
            for images, metadata in iterator:
                began = time.perf_counter()
                for item in metadata:
                    self.host_ns.append(item.get("host_frame_read_monotonic_ns"))
                    self.camera_s.append(capture._camera_seconds(item.get("camera_timestamp"))
                                         if item.get("timestamp_source") == "pco_sdk" else None)
                self.probe.stage("trigger_timestamp_observer", time.perf_counter() - began)
                yield images, metadata
        finally:
            iterator.close()

    def timing(self, source_hz, physical):
        """Separate host receipt intervals from native camera timestamp cadence."""
        result = {}
        for name, values, scale in (("host_receipt", self.host_ns, 1e-9),
                                    ("camera_timestamp", self.camera_s, 1.0)):
            valid = len(values) > 1 and all(v is not None for v in values)
            intervals = np.diff(values) * scale if valid else np.array([])
            valid = bool(valid and np.all(np.isfinite(intervals)) and np.all(intervals > 0))
            result[name] = {"status": "AVAILABLE" if valid else "UNAVAILABLE",
                            "fps": (len(values)-1)/float(intervals.sum()) if valid else None,
                            "interval_ms": _stats(intervals * 1000) if valid else None}
            if valid and name == "camera_timestamp" and physical and source_hz is not None:
                multiples = np.rint(intervals * source_hz).astype(np.int64)
                bins, counts = np.unique(multiples, return_counts=True)
                result[name].update(
                    nominal_trigger_period_multiples={str(int(k)): int(n) for k, n in zip(bins, counts)},
                    nominal_grid_residual_us=_stats((intervals - multiples/source_hz) * 1e6),
                    cadence_to_nominal_source_ratio=result[name]["fps"] / source_hz,
                    note="Nominal-grid periodicity, not measured trigger rejection or electrical jitter.")
        result["trigger_to_exposure_latency"] = "NOT_MEASURED; requires two-channel scope evidence"
        result["trigger_to_host_latency"] = "NOT_MEASURED; no common clock with external trigger"
        return result


def analyze_scope_edges(path, *, max_latency_s, window_start_s, window_end_s):
    """Analyze independent trigger_s/exposure_s edge lists from ONE scope timebase.

    CSV columns are seconds, ascending within each column; blank entries permit
    unequal counts. Supply the actual valid acquisition window on that scope.
    max_latency_s is a reviewed positive pairing bound, strictly shorter than
    every trigger interval. No clock alignment to the PC/camera is assumed.
    Edges in the final incomplete response window are censored, not called lost.
    This analyzes electrical output edges, not photon arrival or pixel fidelity.
    """
    if (not all(math.isfinite(v) for v in (max_latency_s, window_start_s, window_end_s))
            or max_latency_s <= 0 or window_end_s <= window_start_s):
        raise ValueError("Use a finite positive latency bound and increasing scope window in seconds")
    trigger, exposure = [], []
    with filesystem_path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["trigger_s", "exposure_s"]:
            raise ValueError("Scope CSV must have exactly trigger_s,exposure_s columns (edge times in seconds)")
        for row in reader:
            for key, target in (("trigger_s", trigger), ("exposure_s", exposure)):
                value = row.get(key)
                if value is not None and value.strip():
                    edge = float(value)
                    if not math.isfinite(edge) or not window_start_s <= edge <= window_end_s:
                        raise ValueError("Scope edge is nonfinite or outside the supplied capture window")
                    if target and edge <= target[-1]:
                        raise ValueError("Scope edges must be strictly increasing in each column")
                    target.append(edge)
                if len(target) > 1_000_000:
                    raise ValueError("Scope edge CSV exceeds the one-million-edge analysis bound")
    if not trigger or not exposure:
        raise ValueError("Scope evidence needs trigger and exposure edges; check output enable/polarity")
    if len(trigger) > 1 and max_latency_s >= min(np.diff(trigger)):
        raise ValueError("Latency bound overlaps adjacent trigger windows; pairing is ambiguous")
    eligible = np.asarray(trigger) + max_latency_s <= window_end_s
    used, delays, unmatched_exposure = set(), [], 0
    for edge in exposure:
        index = int(np.searchsorted(trigger, edge, side="right")) - 1
        if index < 0 or edge - trigger[index] > max_latency_s or index in used:
            unmatched_exposure += 1
        elif eligible[index]:
            used.add(index)
            delays.append((edge - trigger[index]) * 1e6)
    tested = int(eligible.sum())
    return {"status": "SCOPE_EDGES_ANALYZED", "file": str(Path(path).resolve()),
            "scope_window_s": [window_start_s, window_end_s], "max_pairing_latency_s": max_latency_s,
            "trigger_edges": len(trigger), "exposure_edges": len(exposure),
            "eligible_trigger_edges": tested, "matched_responses": len(delays),
            "unmatched_eligible_triggers": tested-len(delays),
            "unmatched_exposure_edges": unmatched_exposure,
            "end_censored_triggers": int(len(trigger)-tested),
            "response_fraction_in_scope_window": len(delays)/tested if tested else None,
            "trigger_to_exposure_latency_us": _stats(delays),
            "jitter_definition": "population std of matched electrical edge delays; includes scope/cable error",
            "saved_frame_accounting": "NOT_ESTABLISHED; scope window is not aligned to the saved-frame interval"}


def _interpret(report):
    """Explain evidence boundaries instead of attributing every FPS deficit to loss."""
    profile = report.get("capture_profile", {})
    timing = report.get("timing", {})
    camera_rate = timing.get("camera_timestamp", {}).get("fps")
    host_rate = timing.get("host_receipt", {}).get("fps")
    return {
        "timing_evidence": ("MOCK_ONLY; no external trigger response tested" if report["source"] != "physical"
                            else "Native camera cadence available; scope required for input-response timing"
                            if camera_rate else "Host receipt only; exposure cadence and trigger response unresolved"),
        "camera_cadence_fps": camera_rate, "host_receipt_fps": host_rate,
        "upstream_vs_processing": "Camera timestamps describe recorded frame cadence when available. Host receipt includes SDK/USB and Python scheduling. A lower durable/verified FPS includes disk drain and integrity work; it is not itself missing camera frames.",
        "input_pulse_accounting": "NOT_ESTABLISHED; nominal Hz and consecutive recorder IDs do not count rejected triggers",
        "storage_or_host_pressure": bool(profile.get("fifo_overflow") or profile.get("recorder_error")
                                         or report.get("reader", {}).get("queue_full_events")),
        "next_check": "Use scope input vs exposure edges to separate busy/rejected triggers from host retrieval. Compare the camera cadence with matched Auto Sequence 4F/4G, then compare saved, durable and verified boundaries. Stage times overlap and must not be added."}


def run_external_trigger_trial(cfg, *, files=5, frames_per_file=2000, nominal_source_hz=1000.0,
                               max_capture_s=120.0, max_output_gb=10.0):
    """Run one reviewed external recording through the GUI's exact sequencer.

    No source pacing/division is applied. Finite frames (<=100k), decimal GB,
    and a cooperative acquisition deadline bound output/work. Physical driver
    image waits retain their ~1 s timeout; this is not a new slow-trigger mode.
    Workers may finish durability/verification after the acquisition deadline.
    Returns a report with FAILED on error; the notebook wrapper raises it.
    KeyboardInterrupt is re-raised after preserving the failure report.
    """
    if (not isinstance(files, int) or isinstance(files, bool) or not 1 <= files <= 100
            or not isinstance(frames_per_file, int) or isinstance(frames_per_file, bool)
            or frames_per_file < 1 or files*frames_per_file > 100_000):
        raise ValueError("Use 1–100 files and 1–100,000 total frames")
    if nominal_source_hz is not None and (not math.isfinite(nominal_source_hz) or nominal_source_hz <= 0):
        raise ValueError("nominal_source_hz must be positive/finite or None (unknown)")
    if not math.isfinite(max_capture_s) or max_capture_s <= 0 or not math.isfinite(max_output_gb) or not 0 < max_output_gb <= 40:
        raise ValueError("Use a positive finite deadline and output cap up to 40 GB")
    if cfg.source not in ("mock", "physical"):
        raise ValueError("source must be mock or physical")
    if cfg.source == "physical" and not cfg.expected_serial:
        raise ValueError("Set the verified physical camera serial explicitly")
    # The GUI's sequencer uses batches of four, independent of storage-test profiles.
    cfg = replace(cfg, batch_size=4, output_dir=Path(cfg.output_dir).resolve())
    destination = cfg.output_dir
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    width, height = cfg.roi[2]-cfg.roi[0], cfg.roi[3]-cfg.roi[1]
    tag = f"external_{width}x{height}"
    report_path = destination / f"section_4h_{stamp}_{tag}.json"
    folder = destination / f"BENCHMARK_ONLY_4h_{stamp}_{tag}"
    log_path = report_path.with_suffix(".log")
    total = files * frames_per_file
    report = {"section": "4h", "report_schema_version": "1.0", "utc": datetime.now(timezone.utc).isoformat(),
              "status": "FAILED", "source": cfg.source, "report_file": str(report_path),
              "execution_log": str(log_path), "output_directory": str(folder),
              "physical_acceptance": False, "settings": lab.compact_settings(vars(cfg)),
              "test": dict(frames_requested=total, files=files, frames_per_file=frames_per_file,
                           nominal_source_hz=nominal_source_hz, max_capture_s=max_capture_s,
                           max_output_GB=max_output_gb, software_pacing=False, pulse_division=False,
                           pipeline="CameraScanManager.execute_scan(continuous=True), same as GUI", batch_size=4),
              "files": [], "limitations": [
                  "Mock does not simulate electrical pulses or their acceptance.",
                  "Host times and camera timestamps are not synchronized to external trigger edges.",
                  "Camera timestamp overlay is never enabled: raw pixels must remain unchanged.",
                  "Physical image waits retain the existing ~1 s timeout; sparse/absent pulses can time out.",
                  "OS fsync and cached verification do not establish power-loss persistence."]}
    manager = probe = timing = None
    first_step = last_close = finished = None
    interrupted = None
    whole_started = time.perf_counter()
    with filesystem_path(log_path).open("w", encoding="utf-8") as log, redirect_stdout(log):
        try:
            storage._preflight(cfg, {"selected": destination}, total, files, max_output_gb, approved_stage=True)
            folder.mkdir(exist_ok=False)
            with lab.configured_camera(cfg, TriggerMode.EXTERNAL_EXPOSURE_START) as camera:
                report["settings_readback"] = lab.snapshot(camera)
                with capture.profiled_camera(camera) as (profiled, probe):
                    timing = _TimingCamera(profiled, probe)
                    manager = CameraScanManager(timing, folder, compression=None,
                        writer_queue_byte_budget=int(cfg.writer_queue_gib*2**30),
                        writer_memory_reserve_bytes=int(cfg.writer_memory_reserve_gib*2**30))
                    def step_started(step, value):
                        nonlocal first_step
                        if first_step is None:
                            first_step = time.perf_counter()
                    def abort():
                        return first_step is not None and time.perf_counter()-first_step >= max_capture_s
                    for _, _, path, _ in manager.execute_scan(
                            "BENCHMARK_ONLY_4h", "File index (metadata only)", 0, 1,
                            files, frames_per_file, continuous=True,
                            on_step_start=step_started, abort_check=abort):
                        last_close = time.perf_counter()
                        report["files"].append({"file": path.name, "writer": dict(manager.last_step_timing)})
                    finished = time.perf_counter()
                    if len(report["files"]) != files or probe.frames != total:
                        raise TimeoutError("Acquisition stopped before every requested file/frame completed")
                    if manager.last_scan_pipeline.get("files_verified") != files:
                        raise RuntimeError("Not every file completed production durability/pixel verification")
            storage._measure_output(report, folder, max_output_gb)
            report["status"] = "EXECUTED" if cfg.source == "physical" else "MOCK_ONLY"
        except BaseException as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc(file=log)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = exc
        finally:
            report["whole_trial_s_including_setup_cleanup"] = time.perf_counter()-whole_started
            if probe is not None:
                report["capture_profile"] = probe.summary()
                report["timing"] = timing.timing(nominal_source_hz, cfg.source == "physical")
            if manager is not None:
                report["pipeline"] = manager.last_scan_pipeline
                report["reader"] = manager.last_step_timing.get("reader", {})
            closed_frames = 0
            for row in report["files"]:
                closed_frames += row["writer"].get("written_frames", 0)
                sidecar = filesystem_path(folder / row["file"]).with_suffix(".verification.json")
                try:
                    checked = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
                except (OSError, ValueError) as exc:
                    checked = {"status": "FAILED", "error": f"Cannot read integrity result: {exc}"}
                if checked.get("status") != "VERIFIED":
                    report["status"] = "FAILED"
                    report.setdefault("error", checked.get("error") or "Closed file has no VERIFIED integrity result")
                row["integrity"] = {key: checked.get(key) for key in
                    ("status", "frames_verified", "pixels_equal_source", "consecutive_frame_ids", "sync_s", "verification_s", "error")}
                row["writer"] = {key: row["writer"].get(key) for key in
                    ("written_frames", "writer_queue_max_batches_used", "producer_queue_full_wait_events",
                     "producer_total_s", "hdf5_batch_write_s", "writer_drain_after_producer_s")}
            successful = report["status"] in ("EXECUTED", "MOCK_ONLY")
            lifecycle = report.get("capture_profile", {}).get("iterator_lifecycle_s")
            report["rates"] = dict(
                frames_retrieved=probe.frames if probe else 0, frames_in_closed_files=closed_frames,
                capture_fps=total/lifecycle if successful and lifecycle else None,
                saved_fps=total/(last_close-first_step) if successful else None,
                durable_fps=report.get("pipeline", {}).get("durable_fps"),
                complete_verified_fps=total/(finished-first_step) if successful else None,
                saved_elapsed_s=last_close-first_step if last_close is not None else None,
                complete_verified_elapsed_s=finished-first_step if finished is not None else None)
            report["partial_files"] = [p.name for p in filesystem_path(folder).glob("*.partial")]
            report["interpretation"] = _interpret(report)
            filesystem_path(report_path).write_text(json.dumps(report, indent=1, default=lab.json_default), encoding="utf-8")
    if interrupted is not None:
        raise interrupted
    return report


def main(argv=None):
    """Explicit --physical opt-in; default command exercises mocks only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-hz", type=float, default=1000.0)
    parser.add_argument("--files", type=int, default=5)
    parser.add_argument("--frames-per-file", type=int, default=2000)
    args = parser.parse_args(argv)
    cfg = lab.Settings(source="physical" if args.physical else "mock", output_dir=args.output_dir)
    lab.run_notebook_cell("Section 4H", lambda: run_external_trigger_trial(
        cfg, files=args.files, frames_per_file=args.frames_per_file, nominal_source_hz=args.source_hz))


if __name__ == "__main__":
    main()
