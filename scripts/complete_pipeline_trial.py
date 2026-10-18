"""4F: continuous acquisition with production HDF5 and process-based verification.

Only an explicit run connects a camera. The verifier process receives closed
file paths and settings, never a camera or SDK object. No physical test runs
at import, and the existing physical/mock configuration path is reused.
"""

from __future__ import annotations

from dataclasses import replace
import json
import math
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import time
import traceback

import numpy as np

from scripts import capture_diagnostics as capture
from scripts import lab_computer_acceptance as lab
from scripts import storage_diagnostics as storage


def _verify_worker(tasks, results, cfg):
    """Sync and verify closed files in another process, with no camera access."""
    results.put(("ready", None))
    while True:
        task = tasks.get()
        if task is None:
            return
        index, path, manifest = task
        try:
            started = time.perf_counter()
            for name in (path, manifest):
                with Path(name).open("r+b", buffering=0) as handle:
                    os.fsync(handle.fileno())
            synced = time.perf_counter()
            checked = lab.inspect_file(Path(path), cfg)
            pixels = storage.verify_hdf5_pixels(Path(path), Path(manifest))
            source = json.loads(Path(manifest).read_text(encoding="utf-8"))
            with storage.h5py.File(path, "r") as h5:
                ids = [int(json.loads(h5["frame_metadata/json"][i])["frame_id"]) for i in (0, -1)]
            if ids != [source["first_frame_id"], source["last_frame_id"]]:
                raise AssertionError("Saved frame-ID range differs from source across file boundary")
            finished = time.perf_counter()
            results.put(("verified", {"index": index, "started_at": started, "synced_at": synced,
                "verified_at": finished, "fsync_s": synced - started,
                "metadata_and_pixel_check_s": finished - synced,
                "frames_verified": pixels["frames_verified"],
                "pixels_equal_source": pixels["pixels_equal_source"],
                "consecutive_ids": checked["consecutive_ids"], "complete": checked["complete"]}))
        except BaseException:
            results.put(("error", traceback.format_exc()))
            return


class _Verifier:
    """Bound outstanding file work and stop the owned process on errors/timeouts."""

    def __init__(self, cfg, limit, timeout_s):
        ctx = multiprocessing.get_context("spawn")
        self.tasks, self.results = ctx.Queue(maxsize=limit), ctx.Queue()
        self.process = ctx.Process(target=_verify_worker, args=(self.tasks, self.results, cfg),
                                   name="4f-pixel-verifier", daemon=True)
        self.limit, self.timeout_s = limit, timeout_s
        self.pending, self.completed = 0, []
        self.max_pending, self.error = 0, None
        self.last_completion = time.perf_counter()

    def start(self):
        """Warm the spawned Python/HDF5 process before opening the camera."""
        self.process.start()
        try:
            kind, _ = self.results.get(timeout=self.timeout_s)
            if kind != "ready":
                raise RuntimeError("Verifier did not initialize")
        except BaseException:
            self.close()
            raise

    def collect(self, timeout=0):
        """Collect completed checks; a failed verifier aborts acquisition promptly."""
        while True:
            try:
                kind, data = self.results.get(timeout=timeout) if timeout else self.results.get_nowait()
            except Empty:
                break
            timeout = 0
            if kind == "error":
                self.error = data
            elif kind == "verified":
                self.completed.append(data)
                self.pending -= 1
                self.last_completion = time.perf_counter()
        if self.error:
            raise RuntimeError(f"Background verification failed: {self.error}")
        if self.pending and time.perf_counter() - self.last_completion > self.timeout_s:
            raise TimeoutError("Background verification made no completed-file progress within its timeout")
        if self.process.exitcode is not None and self.pending:
            raise RuntimeError("Verifier exited with files still pending")

    def submit(self, index, path, manifest):
        """Fail before building an unbounded disk verification backlog."""
        self.collect()
        if self.pending >= self.limit:
            raise RuntimeError("Verification backlog reached the configured file limit; pipeline is not sustainable")
        if not self.pending:
            self.last_completion = time.perf_counter()
        self.tasks.put_nowait((index, str(path), str(manifest)))
        self.pending += 1
        self.max_pending = max(self.max_pending, self.pending)

    def finish(self):
        """Wait for every submitted file, including the final verification tail."""
        while self.pending:
            self.collect(timeout=0.1)

    def close(self):
        """Join or terminate only this diagnostic's verifier; retain all files."""
        if self.process.pid is not None:
            if self.process.is_alive() and not self.pending:
                self.tasks.put_nowait(None)
                self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=5)
        self.tasks.cancel_join_thread()
        self.tasks.close()
        self.results.close()


class _ContinuousCamera:
    """Present consecutive file slices of ONE camera iterator to the writer.

    Source batch boundaries align with file boundaries. Closing an intermediate
    slice does not stop the SDK recorder. Cached getters avoid SDK setting
    queries during rotations. The recorder is stopped/exhausted exactly once.
    """

    acquisition_diagnostics = None  # File-local queue counters stay in manager timings.

    def __init__(self, camera, cfg, frames, poll):
        self.camera, self.cfg, self.frames, self.poll = camera, cfg, frames, poll
        self.sensor = camera.get_sensor_info()
        self.exposure_s = camera.get_exposure_time()
        self.iterator = camera.iter_frames(frames, batch_size=cfg.batch_size)
        self.count, self.first, self.previous, self.gaps = 0, None, None, 0

    def __getattr__(self, name):
        return getattr(self.camera, name)

    def get_sensor_info(self):
        return self.sensor

    def get_roi(self):
        return self.cfg.roi

    def get_exposure_time(self):
        return self.exposure_s

    def get_readout_mode(self):
        return self.cfg.readout

    def get_trigger_mode(self):
        return lab.TriggerMode.AUTO_SEQUENCE

    def iter_frames(self, num_frames, batch_size=4, stop_check=None):
        """Yield one file's frames, validating frame IDs across all file boundaries."""
        try:
            yield from self._file_frames(num_frames, batch_size, stop_check)
        except BaseException:
            # A consumer error/interrupt closes the slice: stop the live recorder
            # immediately, before the production manager drains its writer queue.
            self.close_iterator()
            raise

    def _file_frames(self, num_frames, batch_size, stop_check):
        """Normal exhaustion of an intermediate slice leaves recording active."""
        count = 0
        if batch_size != self.cfg.batch_size:
            raise ValueError("Continuous source batch size changed")
        while count < num_frames:
            self.poll()
            if stop_check is not None and stop_check():
                raise RuntimeError("Continuous acquisition aborted")
            try:
                images, metadata = next(self.iterator)
            except StopIteration as exc:
                raise RuntimeError("Continuous camera returned fewer frames than requested") from exc
            if len(images) != len(metadata) or not len(images) or count + len(images) > num_frames:
                raise ValueError("Source batch does not fit its file boundary")
            for item in metadata:
                frame_id = int(item["frame_id"])
                if self.previous is not None and frame_id != (self.previous + 1) % 2**32:
                    self.gaps += 1
                    raise RuntimeError("Frame-ID gap across continuous acquisition/file boundary")
                if self.first is None:
                    self.first = frame_id
                self.previous = frame_id
            count += len(images)
            self.count += len(images)
            yield images, metadata
        if self.count == self.frames:
            # Resume the final generator yield to run final SDK status/stop checks.
            try:
                next(self.iterator)
            except StopIteration:
                pass
            else:
                raise RuntimeError("Continuous camera returned extra frames")

    def close_iterator(self):
        """Stop the upstream recording on success, interruption or any error."""
        self.iterator.close()


def run_complete_pipeline_trial(
    cfg, *, output_dirs=None, window=None, stage_output_gb=40.0,
    file_raw_gb=2.0, max_pending_files=None, verifier_timeout_s=120.0,
    frames=None, frames_per_file=None,
):
    """4F: measure continuous capture through final durable, pixel-verified completion.

    GB are decimal. One destination and a maximum 40-GB stage are shared across
    all files, including metadata reserves; no automatic stage/ROI sweep. The
    40-GB default requires about 60.1 GB free with the existing margin. Tiny
    frame overrides support offline mock tests without allocating GB payloads.
    Source hashing is inline; fsync and full pixel/metadata checking run in a
    separate process. Pending verification is bounded by the finite stage's
    file count unless a stricter file limit is explicitly selected. This lets
    a slow verifier finish the test and exposes its cost in complete FPS.
    This is a measured candidate, not a proven global optimum.
    """
    cfg, window_info = storage.window_settings(cfg, window)
    if cfg.source == "physical" and not cfg.expected_serial:
        cfg = replace(cfg, expected_serial=lab.DEFAULT_CAMERA_SERIAL)
    destinations = storage._destinations(cfg, output_dirs)

    def operation(report, folder_name):
        if len(destinations) != 1:
            raise ValueError("4F uses one destination per run so its total output stays within 40 GB")
        if (not isinstance(cfg.batch_size, int) or cfg.batch_size < 1 or
                not math.isfinite(file_raw_gb) or file_raw_gb <= 0 or
                (max_pending_files is not None and
                 (not isinstance(max_pending_files, int) or not 1 <= max_pending_files <= 10000)) or
                not math.isfinite(verifier_timeout_s) or verifier_timeout_s <= 0):
            raise ValueError("Use positive batch/file sizes, None or 1–10000 pending files and a positive timeout")
        if not math.isfinite(stage_output_gb) or not 0 < stage_output_gb <= 40:
            raise ValueError("4F total output budget must be positive and at most 40 GB")
        frame_bytes = window_info["bytes_per_frame"]
        if cfg.batch_size * frame_bytes > 256 * 2**20:
            raise ValueError("4F batch exceeds 256 MiB")
        if cfg.source == "physical" and not cfg.expected_serial:
            raise ValueError("Set the reviewed physical camera serial before 4F")
        chunk = (max(cfg.batch_size, int(file_raw_gb * 1e9 / frame_bytes) // cfg.batch_size * cfg.batch_size)
                 if frames_per_file is None else frames_per_file)
        if not isinstance(chunk, int) or chunk < 1 or chunk % cfg.batch_size:
            raise ValueError("frames_per_file must be a positive multiple of batch_size")
        # Conservative upper bound on files, then fit pixels AND per-file reserves.
        files_upper = math.ceil(stage_output_gb * 1e9 / (chunk * frame_bytes))
        total = (int((stage_output_gb * 1e9 - files_upper * 100_000_000) // (frame_bytes + 4096))
                 if frames is None else frames)
        if not isinstance(total, int) or total < 1:
            raise ValueError("Frame count/stage must fit at least one frame")
        files = math.ceil(total / chunk)
        pending_limit = files if max_pending_files is None else min(max_pending_files, files)
        storage._preflight(cfg, destinations, total, files, stage_output_gb, approved_stage=True)
        report.update(windows={window_info["name"]: window_info}, window_tag=window_info["tag"],
            diagnostic_settings={"batch_size": cfg.batch_size, "frames": total,
                "frames_per_file": chunk, "files": files, "stage_output_GB": stage_output_gb,
                "planned_raw_GB": total * frame_bytes / 1e9,
                "max_pending_files": pending_limit, "verifier_timeout_s": verifier_timeout_s,
                "verification_processes": 1, "source_hashing": True, "compression": "none"},
            limitations=["Finite measured candidate, not proof of the global fastest configuration.",
                "Host cadence does not establish sensor/external-trigger accounting.",
                "OS fsync is requested; cached readback does not prove power-loss persistence.",
                "The SDK recording stays active across production-writer file rotations; rotation pauses can consume FIFO headroom.",
                "Verifier backlog bounds the disk work remaining, not an acquisition pacing mechanism."])
        for label, destination in destinations.items():
            whole_started = time.perf_counter()
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=False)
            drive = {"output_directory": str(folder), "files": [], "summary": {}}
            report["drives"][label] = drive
            verifier = _Verifier(cfg, pending_limit, verifier_timeout_s)
            source, probe, started = None, None, None
            written, queued_for_verification, pending_at_capture_close = 0, 0, None
            samples = capture.Samples()
            def collect():
                verifier.collect()
                if started is not None and source is not None:
                    samples.add(time.perf_counter()-started, source.count, written,
                                sum(r["frames_verified"] for r in verifier.completed), verifier.pending)
            try:
                verifier.start()  # Warm spawn/imports before configuring or recording.
                with lab.configured_camera(replace(cfg, output_dir=folder)) as camera:
                    drive["settings_readback"] = lab.snapshot(camera)
                    if cfg.source == "physical" and getattr(camera, "_cam", None) is not None:
                        configuration = camera._cam.configuration
                        drive["settings_readback"]["sdk_timing_configuration"] = {
                            key: configuration[key] for key in ("pixel rate", "binning", "delay time")
                            if key in configuration}
                        if "binning" in configuration and tuple(configuration["binning"][:2]) != (1, 1):
                            raise ValueError("Camera reports binning; 4F requires unbinned acquisition")
                    with capture.profiled_camera(camera, hash_pixels=False) as (profiled, probe):
                        source = _ContinuousCamera(profiled, cfg, total, collect)
                        started = time.perf_counter()
                        try:
                            for index in range(files):
                                collect()
                                count = min(chunk, total - written)
                                reference = storage._PixelReferenceCamera(source)
                                manager = storage._WindowManager(reference, folder, window_tag=window_info["tag"],
                                    writer_queue_byte_budget=int(cfg.writer_queue_gib * 2**30),
                                    writer_memory_reserve_bytes=int(cfg.writer_memory_reserve_gib * 2**30))
                                file_started = time.perf_counter()
                                path, _ = manager.acquire_and_save_step("BENCHMARK_ONLY_4f", index,
                                    "Continuous recording file index (metadata only)", float(index), count,
                                    batch_size=cfg.batch_size)
                                written += count
                                closed_at = time.perf_counter()
                                row = {"index": index, "file": path.name, "frames": count,
                                    "closed_at_s": closed_at - started, "save_close_s": closed_at - file_started,
                                    "source_hash_s": reference.hash_s, "verification_status": "PENDING",
                                    "first_frame_id": (source.first + written-count) % 2**32,
                                    "last_frame_id": source.previous,
                                    "writer": manager.last_step_timing}
                                drive["files"].append(row)
                                manifest = path.with_suffix(".pixels.sha256.json")
                                manifest.write_text(json.dumps({"algorithm": "sha256", "dtype": "<u2",
                                    "shape_frame_y_x": [count, cfg.roi[3]-cfg.roi[1], cfg.roi[2]-cfg.roi[0]],
                                    "first_frame_id": row["first_frame_id"], "last_frame_id": row["last_frame_id"],
                                    "blocks": reference.blocks}, separators=(",", ":")), encoding="utf-8")
                                verifier.submit(index, path, manifest)
                                queued_for_verification += 1
                                samples.add(time.perf_counter()-started, source.count, written,
                                            sum(r["frames_verified"] for r in verifier.completed), verifier.pending)
                            pending_at_capture_close = verifier.pending
                            verifier.finish()
                        finally:
                            source.close_iterator()
            finally:
                verifier.close()
                now = time.perf_counter()
                for checked in verifier.completed:
                    row = drive["files"][checked["index"]]
                    row.update({key: value for key, value in checked.items()
                                if key not in ("index", "started_at", "synced_at", "verified_at")})
                    row.update(verification_status="VERIFIED", synced_at_s=checked["synced_at"]-started,
                               verified_at_s=checked["verified_at"]-started,
                               verifier_queue_wait_s=max(0, checked["started_at"]-started-row["closed_at_s"]))
                summary = drive["summary"]
                summary.update(frames_requested=total, frames_captured=source.count if source else 0,
                    frames_written=written, frames_verified=sum(r["frames_verified"] for r in verifier.completed),
                    files_closed=len(drive["files"]), files_submitted=queued_for_verification,
                    files_verified=len(verifier.completed), verifier_max_pending_files=verifier.max_pending,
                    verifier_pending_files=verifier.pending,
                    files_pending_at_capture_close=pending_at_capture_close, whole_trial_s=now-whole_started,
                    whole_trial_fps=total/(now-whole_started) if written == total and len(verifier.completed) == files else None,
                    consecutive_frame_ids=(source.gaps == 0 and source.count == total) if source else False)
                drive.update(progress_columns=["seconds", "captured", "frames_in_closed_files", "verified", "pending_files"],
                             progress=samples.rows,
                             partial_files=[path.name for path in folder.glob("*.partial")])
                if probe is not None:
                    drive["capture_profile"] = probe.summary()
                if started is not None and verifier.completed:
                    verified_at = max(r["verified_at"] for r in verifier.completed)
                    synced_at = max(r["synced_at"] for r in verifier.completed)
                    all_verified = len(verifier.completed) == files and written == total
                    summary.update(all_files_verified=all_verified,
                        all_synced_s=synced_at-started if all_verified else None,
                        all_verified_s=verified_at-started if all_verified else None,
                        complete_verified_fps=total/(verified_at-started) if all_verified else None,
                        complete_verified_raw_MB_per_s=total*frame_bytes/1e6/(verified_at-started) if all_verified else None,
                        durable_fps=total/(synced_at-started) if all_verified else None,
                        verification_tail_after_last_close_s=max(0, verified_at-(started+drive["files"][-1]["closed_at_s"])),
                        source_hash_s=sum(r["source_hash_s"] for r in drive["files"]))
                storage._measure_output(drive, folder, stage_output_gb)
            if summary["frames_verified"] != total or not summary["consecutive_frame_ids"]:
                raise AssertionError("4F did not complete every requested frame and pixel check")
            if not drive["within_output_cap"]:
                raise AssertionError("4F output exceeded its approved stage budget")
            summary["capture_fps"] = total / drive["capture_profile"]["iterator_lifecycle_s"]
            summary["saved_fps"] = total / drive["files"][-1]["closed_at_s"]
            summary["host_first_to_last_fps"] = drive["capture_profile"]["host_first_to_last_fps"]
            summary["writer_queue_max_batches"] = max(r["writer"]["writer_queue_max_batches_used"] for r in drive["files"])
            summary["writer_queue_full_wait_events"] = sum(r["writer"]["producer_queue_full_wait_events"] for r in drive["files"])
            summary["sync_and_verification_work_overlapping_capture_s"] = sum(
                max(0, min(r["verified_at"], probe.iterator_finished) - max(r["started_at"], probe.iterator_started))
                for r in verifier.completed)
            drive["interpretation"] = {
                "complete_rate_includes_final_verification": True,
                "absolute_fastest_configuration_established": False,
                "external_trigger_accounting": "NOT_TESTED",
                "next_action": "Compare host cadence, complete_verified_fps, writer queues and verifier backlog with 4C; repeat matched file/batch sizes before selecting a winner."}

    return storage._report("4f", cfg, destinations, operation, tag=window_info["tag"])
