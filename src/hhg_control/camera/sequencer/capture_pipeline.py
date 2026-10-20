"""Bounded camera retrieval, source-pixel references and closed-file checks.

Production counterpart of the 4F/4G candidate. Camera commands belong to the
reader thread; spawned storage workers receive paths and plain metadata only.
No camera opens at import. All files are retained, including failed checks.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import time
import traceback

import numpy as np

from ...safe_io import available_physical_memory_bytes, filesystem_path


class BufferedCamera:
    """Drain one step continuously into a byte-bounded, lazily allocated queue.

    Hashes and manifest writes run in the consumer, never in the SDK reader.
    The default reader stops at a physical step boundary. Explicit continuous
    mode slices one recording across metadata-only files, as in 4F/4G. Units
    are bytes and frames; continuous mode must not span actuator changes.
    """

    yields_owned_batches = True
    can_pause_acquisition = True  # RAM consumer can wait; physical reader cannot.
    acquisition_diagnostics = None

    def __init__(self, camera, frames, batch_size, budget, reserve, poll, *, continuous=False):
        """Cache settings and preflight byte budgets before starting SDK reads."""
        self.camera, self.frames, self.batch_size, self.poll = camera, frames, batch_size, poll
        self.sensor = camera.get_sensor_info()
        self.roi = camera.get_roi()
        self.exposure = camera.get_exposure_time()
        self.readout = camera.get_readout_mode()
        self.trigger = camera.get_trigger_mode()
        self.shape = (self.roi[3] - self.roi[1], self.roi[2] - self.roi[0])
        batch_bytes = min(frames, batch_size) * math.prod(self.shape) * 2
        if budget < 2 * batch_bytes:
            raise MemoryError("Acquisition RAM budget must fit a reader and a writer batch.")
        self.capacity = min(math.ceil(frames / batch_size), budget // batch_bytes - 1)
        self.capacity_bytes = self.capacity * batch_bytes
        self.reserve = reserve
        self.working_bytes = 6 * batch_bytes
        self.available_before = available_physical_memory_bytes()
        # Preserve small-queue operation on development PCs; expanded lab queues
        # must leave the explicit GUI reserve plus active working arrays free.
        self.expanded = (self.capacity + 1) * batch_bytes > 512 * 2**20
        needed = (self.capacity + 1) * batch_bytes + self.working_bytes
        if self.expanded:
            needed += reserve
        if self.available_before < needed:
            raise MemoryError(f"Camera reader/writer RAM preflight needs {needed / 2**30:.2f} GiB; "
                              f"only {self.available_before / 2**30:.2f} GiB is available.")
        self.pending = Queue(maxsize=self.capacity)
        self.stop, self.done = threading.Event(), threading.Event()
        self.reader = threading.Thread(target=self._read, name="camera-sdk-reader")
        self.error = None
        self.retrieved = self.delivered = self.peak_batches = self.full_events = 0
        self.min_available = self.available_before
        self.first_id = self.last_id = None
        self.hash_s = 0.0
        self.manifest = self.manifest_partial = self._manifest = None
        self.continuous = continuous
        self.carry = None
        self.file_start = self.file_frames = 0

    @property
    def is_connected(self):
        """Use the driver's existing connection state without an SDK query."""
        return self.camera.is_connected

    def get_sensor_info(self):
        """Return identity cached before recording starts."""
        return self.sensor

    def get_roi(self):
        """Return applied, unbinned pixel bounds cached before recording."""
        return self.roi

    def get_exposure_time(self):
        """Return cached exposure seconds without concurrent SDK access."""
        return self.exposure

    def get_readout_mode(self):
        """Preserve the actual shutter mode; never force a mode for speed."""
        return self.readout

    def get_trigger_mode(self):
        """Preserve auto or external triggering exactly as configured."""
        return self.trigger

    def prepare_manifest(self, filepath, attrs):
        """Stream source references beside the HDF5 file, without a RAM-sized list."""
        self.manifest = filepath.with_suffix(".pixels.sha256.jsonl")
        self.manifest_partial = Path(str(self.manifest) + ".partial")
        self._manifest = filesystem_path(self.manifest_partial).open("x", encoding="utf-8")
        self.file_start, self.file_frames = self.delivered, attrs["num_frames"]
        header = {"version": 1, "algorithm": "sha256", "dtype": "<u2",
                  "shape": [self.file_frames, *self.shape], "settings": {
                      key: attrs[key] for key in ("camera_serial", "roi_bounds", "exposure_time_s",
                                                 "trigger_mode", "readout_mode", "scan_step_index")}}
        self._manifest.write(json.dumps(header, separators=(",", ":")) + "\n")

    def _read(self):
        """Own iterator creation, SDK reads and iterator cleanup on one thread."""
        from .scan_manager import AcquisitionBackpressure
        iterator = None
        previous = None
        try:
            iterator = self.camera.iter_frames(self.frames, batch_size=self.batch_size,
                                               stop_check=self.stop.is_set)
            for images, metadata in iterator:
                if self.stop.is_set():
                    break
                if (images.ndim != 3 or images.shape[1:] != self.shape or images.dtype != np.uint16
                        or not 0 < len(images) <= self.batch_size
                        or self.retrieved + len(images) > self.frames or len(metadata) != len(images)):
                    raise ValueError("Camera returned an invalid batch, shape, dtype or metadata count.")
                for item in metadata:
                    frame_id = int(item["frame_id"])
                    if previous is not None and frame_id != (previous + 1) % 2**32:
                        raise RuntimeError("Camera frame-ID gap or duplicate in measurement step.")
                    previous = frame_id
                owned = images if self.camera.yields_owned_batches else images.copy()
                batch = owned, [dict(item) for item in metadata]
                self.retrieved += len(images)
                available = available_physical_memory_bytes()
                self.min_available = min(self.min_available, available)
                if self.expanded and available < self.reserve + self.working_bytes:
                    raise AcquisitionBackpressure("Camera reader reached the RAM reserve; partial data retained.")
                while not self.stop.is_set():
                    try:
                        self.pending.put(batch, timeout=0.05)
                        self.peak_batches = max(self.peak_batches, self.pending.qsize())
                        break
                    except Full as exc:
                        self.full_events += 1
                        if not self.camera.can_pause_acquisition:
                            raise AcquisitionBackpressure("Camera-reader RAM queue filled; downstream processing "
                                                          "cannot keep up; partial data retained.") from exc
            if not self.stop.is_set() and self.retrieved != self.frames:
                raise RuntimeError(f"Camera returned {self.retrieved} frames; expected {self.frames}.")
        except BaseException as exc:
            self.error = exc
        finally:
            try:
                if iterator is not None:
                    iterator.close()
            except BaseException as exc:
                if self.error is None:
                    self.error = exc
            self.done.set()

    def iter_frames(self, num_frames, batch_size=4, stop_check=None):
        """Consume RAM batches, hashing original uint16 pixels before HDF5 queueing."""
        from .scan_manager import AcquisitionAborted
        if (batch_size != self.batch_size or num_frames != self.file_frames
                or not self.continuous and num_frames != self.frames
                or self.delivered + num_frames > self.frames):
            raise ValueError("Measurement dimensions changed after buffer planning.")
        file_end = self.delivered + num_frames
        succeeded = False
        try:
            self.poll()
            if stop_check is not None and stop_check():
                raise AcquisitionAborted("Measurement stopped before starting camera retrieval.")
            if self.reader.ident is None:
                self.reader.start()
            while self.delivered < file_end:
                self.poll()
                if stop_check is not None and stop_check():
                    raise AcquisitionAborted(f"Measurement stopped after {self.delivered} frames.")
                if self.error is not None:
                    raise self.error
                if self.carry is not None:
                    images, metadata = self.carry
                    self.carry = None
                else:
                    try:
                        images, metadata = self.pending.get(timeout=0.05)
                    except Empty:
                        if self.done.is_set():
                            if self.error is not None:
                                raise self.error
                            raise RuntimeError("Camera reader ended before delivering all frames.")
                        continue
                remaining = file_end - self.delivered
                if len(images) > remaining:
                    self.carry = images[remaining:], metadata[remaining:]
                    images, metadata = images[:remaining], metadata[:remaining]
                began = time.perf_counter()
                pixels = np.ascontiguousarray(images, dtype="<u2")
                digest = hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()
                self.hash_s += time.perf_counter() - began
                first, last = int(metadata[0]["frame_id"]), int(metadata[-1]["frame_id"])
                if self.first_id is None:
                    self.first_id = first
                self.last_id = last
                self._manifest.write(json.dumps({"start": self.delivered-self.file_start, "frames": len(images),
                    "sha256": digest, "first_id": first, "last_id": last}, separators=(",", ":")) + "\n")
                self.delivered += len(images)
                yield images, metadata
            if self.delivered == self.frames:
                self.reader.join()  # Includes the driver's final FIFO/error check and stop.
            if self.error is not None:
                raise self.error
            succeeded = True
        finally:
            if not succeeded or not self.continuous or self.delivered == self.frames:
                self.close()
            elif self._manifest is not None:
                self._manifest.close()

    def close(self):
        """Stop and join only this reader; never close the connected camera."""
        self.stop.set()
        if self.reader.ident is not None:
            self.reader.join()
        if self._manifest is not None:
            self._manifest.close()

    def finish_manifest(self):
        """Publish complete source references; incomplete references retain .partial."""
        if self._manifest is not None:
            self._manifest.close()
        if self.error is not None:
            raise self.error
        if (self.delivered-self.file_start != self.file_frames
                or not self.continuous and (self.delivered != self.frames or self.retrieved != self.frames)):
            raise RuntimeError("Cannot publish incomplete source-pixel references.")
        # Exclusive creation above and the unique HDF5 name establish ownership.
        if filesystem_path(self.manifest).exists():
            raise FileExistsError(self.manifest)
        os.rename(filesystem_path(self.manifest_partial), filesystem_path(self.manifest))
        return self.manifest

    def summary(self):
        """Return bounded diagnostics, without retaining frame or timing histories."""
        return {"scope": "recording", "frames_retrieved": self.retrieved, "frames_delivered": self.delivered,
                "buffered_unsaved_frames": self.retrieved-self.delivered,
                "capacity_bytes": self.capacity_bytes, "peak_pending_batches": self.peak_batches,
                "queue_full_events": self.full_events, "minimum_available_memory_bytes": self.min_available,
                "source_hash_s": self.hash_s, "first_frame_id": self.first_id, "last_frame_id": self.last_id,
                "fifo_capacity_frames": getattr(self.camera, "last_fifo_capacity_frames", None),
                "error": str(self.error) if self.error is not None else None,
                "reader_stopped": not self.reader.is_alive()}


def _write_status(path, result, *, durable=False):
    """Atomically replace this acquisition's status sidecar; never modify image pixels."""
    temporary = Path(str(path) + ".tmp")
    with filesystem_path(temporary).open("w", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"))
        handle.flush()
        if durable:
            os.fsync(handle.fileno())
    os.replace(filesystem_path(temporary), filesystem_path(path))


def verify_file(path, manifest_path, *, read_bytes=64 * 2**20):
    """Check every saved pixel and frame ID using bounded reads and streamed hashes.

    This is host-source fidelity, not sensor correctness or external-trigger
    accounting. OS fsync must precede verification; cached reads are permitted.
    """
    import h5py
    began = time.perf_counter()
    with filesystem_path(manifest_path).open(encoding="utf-8") as references, \
            h5py.File(filesystem_path(path), "r") as h5:
        header = json.loads(next(references))
        images, metadata = h5["images"], h5["frame_metadata/json"]
        if (header["version"] != 1 or header["algorithm"] != "sha256" or header["dtype"] != "<u2"
                or list(images.shape) != header["shape"] or images.dtype != np.dtype("<u2")
                or not h5.attrs.get("complete", False) or len(metadata) != len(images)
                or int(h5.attrs.get("frames_written", -1)) != len(images)):
            raise ValueError("Saved file shape, dtype, count or completion differs from source.")
        for key, expected in header["settings"].items():
            actual = h5.attrs[key]
            equal = (np.array_equal(actual, expected) if key == "roi_bounds" else
                     math.isclose(float(actual), expected, rel_tol=1e-5, abs_tol=1e-9)
                     if key == "exposure_time_s" else actual == expected)
            if not equal:
                raise ValueError(f"Saved acquisition setting differs from source: {key}.")
        count, previous, first = 0, None, None
        frame_bytes = math.prod(images.shape[1:]) * 2
        buffer_frames = max(1, min(len(images), read_bytes // frame_bytes))
        buffer = np.empty((buffer_frames, *images.shape[1:]), dtype="<u2")
        window_start = window_stop = 0
        window_ids = []
        for line in references:
            block = json.loads(line)
            frames = block["frames"]
            if block["start"] != count or not isinstance(frames, int) or frames < 1 or count + frames > len(images):
                raise ValueError("Source manifest has missing, repeated or invalid image ranges.")
            digest = hashlib.sha256()
            end = count + frames
            while count < end:
                if count >= window_stop:
                    window_start, window_stop = count, min(count + buffer_frames, len(images))
                    images.read_direct(buffer, np.s_[window_start:window_stop], np.s_[:window_stop-window_start])
                    window_ids = []
                    for record in metadata[window_start:window_stop]:
                        identifier = int(json.loads(record)["frame_id"])
                        if previous is not None and identifier != (previous + 1) % 2**32:
                            raise ValueError("Saved frame IDs have a gap or duplicate.")
                        if first is None:
                            first = identifier
                        previous = identifier
                        window_ids.append(identifier)
                stop = min(end, window_stop)
                if (count == block["start"] and window_ids[count-window_start] != block["first_id"]
                        or stop == end and window_ids[stop-window_start-1] != block["last_id"]):
                    raise ValueError("Saved frame IDs differ from source-pixel references.")
                digest.update(memoryview(buffer[count-window_start:stop-window_start]).cast("B"))
                count = stop
            if digest.hexdigest() != block["sha256"]:
                raise ValueError(f"Saved pixels differ from source at frames {block['start']}:{end}.")
        if count != len(images):
            raise ValueError("Source manifest does not cover every saved frame.")
    return {"frames_verified": count, "pixels_equal_source": True, "consecutive_frame_ids": True,
            "first_frame_id": first, "last_frame_id": previous, "verification_s": time.perf_counter()-began,
            "external_trigger_accounting": "NOT_TESTED"}


def _sync_files(tasks, results):
    """Sync closed HDF5 and source-reference files independently of pixel readback."""
    results.put(("ready_sync", None))
    while True:
        task = tasks.get()
        if task is None:
            return
        try:
            began = time.perf_counter()
            for name in (task["path"], task["manifest"]):
                with filesystem_path(name).open("r+b", buffering=0) as handle:
                    os.fsync(handle.fileno())
            synced_at = time.perf_counter()
            results.put(("synced", {**task, "sync_s": synced_at-began, "synced_at": synced_at}))
        except BaseException:
            results.put(("error", {**task, "error": traceback.format_exc()}))
            return


def _verify_files(tasks, results):
    """Verify only synced files; no camera, SDK or Qt object crosses this boundary."""
    results.put(("ready_verify", None))
    while True:
        task = tasks.get()
        if task is None:
            return
        try:
            checked = verify_file(task["path"], task["manifest"])
            record = {"status": "VERIFIED", "file": task["path"], "sync_s": task["sync_s"], **checked}
            acquisition = task.get("acquisition")
            if acquisition is not None:
                durable_s = task["synced_at"] - acquisition["started_at"]
                complete_s = time.perf_counter() - acquisition["started_at"]
                record.update(acquisition={k: v for k, v in acquisition.items() if k != "started_at"},
                              durable_elapsed_s=durable_s, verified_elapsed_s=complete_s,
                              durable_fps=checked["frames_verified"] / durable_s,
                              complete_verified_fps=checked["frames_verified"] / complete_s)
            _write_status(task["report"], record, durable=True)
            results.put(("verified", {**task, **record}))
        except BaseException:
            results.put(("error", {**task, "error": traceback.format_exc()}))
            return


class ClosedFilePipeline:
    """Overlap bounded closed-file sync and verification with subsequent steps.

    At a step boundary the sequencer can wait for a slot before starting the
    next exposure. Completed files and reports are never recycled or deleted.
    """

    def __init__(self, limit=5, startup_timeout_s=60.0):
        """Create bounded process queues; no process starts until start() is called."""
        ctx = multiprocessing.get_context("spawn")
        self.tasks, self.verify_tasks, self.results = ctx.Queue(limit), ctx.Queue(limit), ctx.Queue()
        self.sync = ctx.Process(target=_sync_files, args=(self.tasks, self.results), name="camera-file-sync", daemon=True)
        self.verify = ctx.Process(target=_verify_files, args=(self.verify_tasks, self.results), name="camera-file-verifier", daemon=True)
        self.limit, self.startup_timeout_s = limit, startup_timeout_s
        self.pending = {}
        self.completed = 0
        self.first_capture_at = self.last_synced_at = None
        self.frames_synced = 0
        self.peak_pending = 0
        self.closed = False

    def start(self, abort_check=None):
        """Warm workers before recording; fail without sending hardware commands."""
        try:
            self.sync.start()
            self.verify.start()
            ready, deadline = set(), time.monotonic() + self.startup_timeout_s
            while len(ready) != 2:
                if abort_check is not None and abort_check():
                    from .scan_manager import AcquisitionAborted
                    raise AcquisitionAborted("Stopped before camera acquisition during worker startup.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Storage worker startup exceeded its timeout.")
                try:
                    kind, _ = self.results.get(timeout=min(0.05, remaining))
                except Empty:
                    if any(p.exitcode is not None for p in (self.sync, self.verify)):
                        raise RuntimeError("Storage worker exited during startup.")
                    continue
                if kind not in ("ready_sync", "ready_verify"):
                    raise RuntimeError("Storage worker failed during startup.")
                ready.add(kind)
        except BaseException:
            self.close()
            raise

    def collect(self, timeout=0):
        """Surface worker failures during acquisition; keep only bounded pending state."""
        while True:
            try:
                kind, task = self.results.get(timeout=timeout) if timeout else self.results.get_nowait()
            except Empty:
                break
            timeout = 0
            if kind == "error":
                _write_status(task["report"], {"status": "FAILED", "file": task["path"], "error": task["error"]})
                raise RuntimeError("Acquisition file sync/pixel verification failed: " + task["error"])
            if kind == "synced":
                acquisition = task.get("acquisition")
                if acquisition is not None:
                    began = acquisition["started_at"]
                    self.first_capture_at = began if self.first_capture_at is None else min(self.first_capture_at, began)
                    self.last_synced_at = (task["synced_at"] if self.last_synced_at is None
                                           else max(self.last_synced_at, task["synced_at"]))
                    self.frames_synced += acquisition["writer"]["written_frames"]
                task["deadline"] = time.monotonic() + task["timeout_s"]
                self.pending[task["path"]] = task
                self.verify_tasks.put_nowait(task)
            elif kind == "verified":
                self.pending.pop(task["path"])
                self.completed += 1
        for process in (self.sync, self.verify):
            if process.exitcode is not None:
                raise RuntimeError(f"Acquisition storage worker exited unexpectedly: {process.name}.")
        for task in self.pending.values():
            if time.monotonic() > task["deadline"]:
                raise TimeoutError("Acquisition storage worker exceeded its size-dependent file timeout.")

    def wait_for_slot(self, abort_check=None):
        """Bound verification backlog before the camera starts another step."""
        from .scan_manager import AcquisitionAborted
        self.collect()
        while len(self.pending) >= self.limit:
            if abort_check is not None and abort_check():
                raise AcquisitionAborted("Stopped while awaiting a storage verification slot.")
            self.collect(timeout=0.05)

    def submit(self, path, manifest, *, acquisition=None):
        """Queue a closed file; sync includes its complete source manifest."""
        self.collect()
        if len(self.pending) >= self.limit:
            raise RuntimeError("Closed-file verification backlog exceeded its bound.")
        # Long steps may exceed 120 s of verification; scale the safety timeout
        # to file size rather than treating a TB file as a two-GB diagnostic.
        timeout_s = max(120.0, filesystem_path(path).stat().st_size / 10e6 + 60.0)
        task = {"path": str(path), "manifest": str(manifest), "report": str(path.with_suffix(".verification.json")),
                "timeout_s": timeout_s, "deadline": time.monotonic() + timeout_s,
                "acquisition": acquisition}
        _write_status(task["report"], {"status": "PENDING", "file": str(path)})
        self.pending[str(path)] = task
        self.peak_pending = max(self.peak_pending, len(self.pending))
        self.tasks.put_nowait(task)

    def finish(self):
        """Complete all submitted durability and integrity work before success."""
        while self.pending:
            self.collect(timeout=0.05)

    def durable_summary(self):
        """Rate for this scan segment: first capture preparation to final sync.

        Includes file transitions and sync queue delays, excludes worker startup
        and verification after final sync. Uses stored timestamps only.
        """
        if self.first_capture_at is None or self.last_synced_at is None:
            return {}
        elapsed = self.last_synced_at - self.first_capture_at
        if elapsed <= 0:
            return {}
        return {"frames_synced": self.frames_synced, "durable_elapsed_s": elapsed,
                "durable_fps": self.frames_synced / elapsed}

    def close(self):
        """Join owned workers; failure leaves files and explicit UNVERIFIED reports."""
        if self.closed:
            return
        self.closed = True
        for process, tasks in ((self.sync, self.tasks), (self.verify, self.verify_tasks)):
            if process.pid is not None:
                if process.is_alive() and not self.pending:
                    tasks.put_nowait(None)
                    process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            tasks.cancel_join_thread()
            tasks.close()
        self.results.close()
        for task in self.pending.values():
            report = filesystem_path(task["report"])
            # Preserve a diagnostic FAILED result or a verified worker result
            # delivered just as another step failed.
            existing = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
            if existing.get("status") in (None, "PENDING"):
                _write_status(task["report"], {"status": "UNVERIFIED", "file": task["path"],
                                              "error": "Acquisition ended before all background checks completed."})
