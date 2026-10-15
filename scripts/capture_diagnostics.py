"""Opt-in 4C measurement helpers; never discover or connect camera hardware.

SDK proxies only time calls the existing driver already makes. Host return time
is not exposure time. Counters remain bounded and summaries survive failures.
"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
from queue import Queue, Full, Empty
import threading
import time

import numpy as np


class Samples:
    """Retain a bounded, progressively coarsened time series for charting."""

    def __init__(self):
        self.rows, self.interval, self.next_at = [], 1.0, 0.0

    def add(self, seconds, *values):
        if seconds < self.next_at:
            return
        if len(self.rows) >= 128:
            self.rows = self.rows[::2]
            self.interval *= 2
        self.rows.append([seconds, *values])
        self.next_at = seconds + self.interval


def _camera_seconds(value):
    """Decode documented PCO timestamp fields without treating host fallback as camera time."""
    try:
        if isinstance(value, dict):
            day = datetime(*(int(value[key]) for key in ("year", "month", "day", "hour", "minute")))
            return (day - datetime(2000, 1, 1)).total_seconds() + float(value["second"])
        if isinstance(value, datetime):
            return (value - datetime(2000, 1, 1)).total_seconds()
    except (ValueError, KeyError, TypeError, OverflowError):
        pass
    return None


class _SDKProxy:
    """Forward existing SDK calls, preserving arguments, results and exceptions."""

    def __init__(self, sdk, probe, recorder=False):
        self._target, self._probe, self._recorder = sdk, probe, recorder

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name == "rec" and not self._recorder:
            return _SDKProxy(value, self._probe, recorder=True)
        names = {"record": "sdk_record_start", "stop": "sdk_stop",
                 "wait_for_new_image": "sdk_wait", "image": "sdk_image_copy",
                 "get_status": "sdk_status"}
        if name not in names or not callable(value):
            return value

        def measured(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = value(*args, **kwargs)
                if name == "get_status":
                    self._probe.status(result)
                elif name == "image":
                    self._probe.sdk_image(result[1])
                return result
            finally:
                self._probe.stage(names[name], time.perf_counter() - started)
        return measured


class Probe:
    """Accumulate source counts, actual camera/host cadence, SDK costs and queue trends."""

    def __init__(self, camera, *, hash_pixels=False):
        self.camera, self.hash_pixels = camera, hash_pixels
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.stages, self.blocks = {}, []
        self.frames = self.gaps = self.duplicates = 0
        self.sdk_frames = self.sdk_gap_events = 0
        self.sdk_last_id = None
        self.iterator_started = self.iterator_finished = None
        self.first_id = self.last_id = None
        self.first_host = self.last_host = self.first_camera = self.last_camera = None
        self.host_count = self.camera_count = 0
        self.camera_invalid = False
        self.camera_counter_first = self.camera_counter_last = None
        self.camera_counter_gap_events = self.camera_counter_wrap_events = 0
        self.fifo_overflow, self.recorder_error, self.last_status = None, None, None
        self.queue_samples, self.delivery_samples = Samples(), Samples()
        self.window_start = None
        self.window_frames = 0
        self.window_rates = []

    def stage(self, name, seconds):
        row = self.stages.setdefault(name, {"total_s": 0.0, "max_s": 0.0, "calls": 0})
        row["total_s"] += seconds
        row["max_s"] = max(row["max_s"], seconds)
        row["calls"] += 1

    def status(self, status):
        keys = ("dwProcImgCount", "dwReqImgCount", "dwLastError", "bFIFOOverflow")
        self.last_status = {key: status[key] for key in keys if key in status}
        if "bFIFOOverflow" in status:
            self.fifo_overflow = bool(self.fifo_overflow or status["bFIFOOverflow"])
        if "dwLastError" in status:
            self.recorder_error = self.recorder_error or int(status["dwLastError"])

    def sdk_image(self, metadata):
        """Observe IDs before the driver can reject an offending frame."""
        self.sdk_frames += 1
        if not isinstance(metadata, dict) or "recorder image number" not in metadata:
            return
        frame_id = int(metadata["recorder image number"])
        if self.sdk_last_id is not None and frame_id != (self.sdk_last_id + 1) % 2**32:
            self.sdk_gap_events += 1
        self.sdk_last_id = frame_id

    def queue_sample(self, captured, written, batches, capacity_frames, capacity_bytes):
        self.queue_samples.add(time.perf_counter() - self.started, captured, written, batches)

    def observe(self, images, metadata):
        started = time.perf_counter()
        if self.hash_pixels:
            pixels = np.ascontiguousarray(images, dtype="<u2")
            began = time.perf_counter()
            digest = hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()
            self.stage("source_hash", time.perf_counter() - began)
            self.blocks.append({"start": self.frames, "frames": len(images), "sha256": digest})
        for item in metadata:
            frame_id = int(item["frame_id"])
            if self.last_id is not None:
                delta = (frame_id - self.last_id) % 2**32
                self.duplicates += int(delta == 0)
                self.gaps += int(delta != 1)
            if self.first_id is None:
                self.first_id = frame_id
            self.last_id = frame_id
            host = item.get("host_frame_read_monotonic_ns")
            if host is not None:
                seconds = int(host) / 1e9
                if self.first_host is None:
                    self.first_host = seconds
                self.last_host = seconds
                self.host_count += 1
                if self.window_start is None:
                    self.window_start = seconds
                self.window_frames += 1
                if seconds - self.window_start >= 1.0:
                    rate = (self.window_frames - 1) / (seconds - self.window_start)
                    # Bounded aggregate; retain a chart series separately.
                    if not self.window_rates:
                        self.window_rates = [rate, rate, 0.0, 0]
                    self.window_rates[0] = min(self.window_rates[0], rate)
                    self.window_rates[1] = max(self.window_rates[1], rate)
                    self.window_rates[2] += rate
                    self.window_rates[3] += 1
                    self.delivery_samples.add(seconds - self.first_host, rate)
                    self.window_start, self.window_frames = seconds, 1
            if item.get("timestamp_source") == "pco_sdk":
                timestamp = item.get("camera_timestamp")
                if isinstance(timestamp, dict) and "image counter" in timestamp:
                    counter = int(timestamp["image counter"])
                    if self.camera_counter_last is not None:
                        if counter < self.camera_counter_last:
                            self.camera_counter_wrap_events += 1
                        elif counter != self.camera_counter_last + 1:
                            self.camera_counter_gap_events += 1
                    if self.camera_counter_first is None:
                        self.camera_counter_first = counter
                    self.camera_counter_last = counter
                seconds = _camera_seconds(timestamp)
                if seconds is not None:
                    self.camera_invalid |= self.last_camera is not None and seconds <= self.last_camera
                    if self.first_camera is None:
                        self.first_camera = seconds
                    self.last_camera = seconds
                    self.camera_count += 1
        self.frames += len(images)
        self.stage("diagnostic_observer", time.perf_counter() - started)

    def summary(self):
        def rate(first, last, count):
            return (count - 1) / (last - first) if count > 1 and last > first else None
        camera_rate = rate(self.first_camera, self.last_camera, self.camera_count)
        if self.camera_count != self.frames or self.camera_invalid:
            camera_rate = None
        return {"frames_returned": self.frames, "first_frame_id": self.first_id,
                "last_frame_id": self.last_id, "frame_id_gap_events": self.gaps,
                "duplicate_id_events": self.duplicates,
                "sdk_images_returned": self.sdk_frames if hasattr(self.camera, "_cam") else None,
                "sdk_frame_id_gap_events": self.sdk_gap_events if hasattr(self.camera, "_cam") else None,
                "frame_id_source": "SDK recorder" if hasattr(self.camera, "_cam") else "mock/driver",
                "host_first_to_last_fps": rate(self.first_host, self.last_host, self.host_count),
                "process_cpu_s": time.process_time() - self.cpu_started,
                "camera_cadence_fps": camera_rate,
                "camera_timestamp_status": "AVAILABLE" if camera_rate is not None else "UNAVAILABLE",
                "embedded_camera_counter": {"first": self.camera_counter_first, "last": self.camera_counter_last,
                                            "gap_events": self.camera_counter_gap_events,
                                            "wrap_or_reset_events_unresolved": self.camera_counter_wrap_events,
                                            "status": "AVAILABLE" if self.camera_counter_first is not None else "UNAVAILABLE"},
                "time_to_first_host_image_s": self.first_host - self.started if self.first_host is not None else None,
                "iterator_lifecycle_s": (self.iterator_finished - self.iterator_started
                                           if self.iterator_finished is not None and self.iterator_started is not None else None),
                "host_window_fps_min_mean_max": [self.window_rates[0], self.window_rates[2] / self.window_rates[3],
                                                   self.window_rates[1]] if self.window_rates else None,
                "delivery_series_columns": ["seconds", "host_fps"], "delivery_series": self.delivery_samples.rows,
                "queue_series_columns": ["seconds", "captured", "written", "pending_batches"],
                "queue_series": self.queue_samples.rows,
                "fifo_capacity_frames": getattr(self.camera, "last_fifo_capacity_frames", None),
                "fifo_capacity_bytes": getattr(self.camera, "last_fifo_capacity_bytes", None),
                "fifo_overflow": self.fifo_overflow, "recorder_error": self.recorder_error,
                "recorder_last_raw_status": self.last_status,
                "fifo_occupancy": "UNAVAILABLE; recorder counters are not assumed to be occupancy",
                "stages": self.stages}


class ProfileCamera:
    """Delegate the production interface, measuring driver work and consumer pauses."""

    def __init__(self, camera, probe):
        self.camera, self.acquisition_diagnostics = camera, probe

    def __getattr__(self, name):
        return getattr(self.camera, name)

    def iter_frames(self, *args, **kwargs):
        probe = self.acquisition_diagnostics
        probe.iterator_started = time.perf_counter()
        iterator = self.camera.iter_frames(*args, **kwargs)
        try:
            while True:
                began = time.perf_counter()
                try:
                    images, metadata = next(iterator)
                except StopIteration:
                    probe.stage("driver_next", time.perf_counter() - began)
                    break
                probe.stage("driver_next", time.perf_counter() - began)
                probe.observe(images, metadata)
                began = time.perf_counter()
                yield images, metadata
                probe.stage("consumer_between_batches", time.perf_counter() - began)
        finally:
            try:
                iterator.close()
            finally:
                probe.iterator_finished = time.perf_counter()


@contextmanager
def profiled_camera(camera, *, hash_pixels=False):
    """Attach passive instrumentation only for this explicit diagnostic, then restore it."""
    probe = Probe(camera, hash_pixels=hash_pixels)
    original_sdk = getattr(camera, "_cam", None)
    previous = getattr(camera, "acquisition_diagnostics", None)
    try:
        camera.acquisition_diagnostics = probe
        if original_sdk is not None:
            camera._cam = _SDKProxy(original_sdk, probe)
        yield ProfileCamera(camera, probe), probe
    finally:
        if original_sdk is not None:
            camera._cam = original_sdk
        if previous is None:
            del camera.acquisition_diagnostics
        else:
            camera.acquisition_diagnostics = previous


def queue_to_discard(camera, cfg, frames):
    """Use a bounded producer/consumer queue with no disk I/O; close/join on failure."""
    from hhg_control.sequencer.scan_manager import _writer_queue_batches, AcquisitionBackpressure
    height, width = cfg.roi[3] - cfg.roi[1], cfg.roi[2] - cfg.roi[0]
    capacity = _writer_queue_batches(frames, cfg.batch_size, height, width,
                                     byte_budget=int(cfg.writer_queue_gib * 2**30))
    queue, done = Queue(maxsize=capacity), threading.Event()
    counters = {"consumed": 0, "max_batches": 0, "full_events": 0, "put_s": 0.0, "copy_s": 0.0,
                "latest_frame_copy_s": 0.0}

    def consume():
        while not done.is_set() or not queue.empty():
            try:
                images, _ = queue.get(timeout=0.05)
                counters["consumed"] += len(images)
            except Empty:
                continue

    started = time.perf_counter()
    worker = threading.Thread(target=consume, name="diagnostic-discard")
    worker.start()
    iterator = camera.iter_frames(frames, batch_size=cfg.batch_size)
    try:
        for images, metadata in iterator:
            began = time.perf_counter()
            owned = images if camera.yields_owned_batches else images.copy()
            counters["copy_s"] += time.perf_counter() - began
            began = time.perf_counter()
            while True:
                try:
                    queue.put((owned, metadata), timeout=0.05)
                    break
                except Full:
                    counters["full_events"] += 1
                    if not camera.can_pause_acquisition:
                        raise AcquisitionBackpressure("Queue-to-discard filled; acquisition stopped")
            counters["put_s"] += time.perf_counter() - began
            counters["max_batches"] = max(counters["max_batches"], queue.qsize())
            camera.acquisition_diagnostics.queue_sample(camera.acquisition_diagnostics.frames,
                                                        counters["consumed"], queue.qsize(), capacity * cfg.batch_size,
                                                        capacity * cfg.batch_size * height * width * 2)
            began = time.perf_counter()
            latest_frame = images[-1].copy()  # Match the production manager's latest-frame copy.
            counters["latest_frame_copy_s"] += time.perf_counter() - began
    finally:
        try:
            iterator.close()
        finally:
            done.set()
            worker.join()
    elapsed = time.perf_counter() - started
    if counters["consumed"] != frames:
        raise AssertionError("Queue-to-discard frame count differs")
    return {"frames": frames, "total_fps": frames / elapsed, "elapsed_s": elapsed,
            "queue_capacity_batches": capacity, **counters}


def interpret(capture, queued, saved, target_fps, source, tolerance=0.005):
    """Generate cautious measured-transfer versus post-processing conclusions."""
    checks = saved.get("profile", {})
    statements = []
    profiles = [run.get("profile", {}) for run in (capture, queued, saved)]
    loss = any(bool(item.get("frame_id_gap_events") or item.get("duplicate_id_events") or
                   item.get("sdk_frame_id_gap_events") or item.get("fifo_overflow") or item.get("recorder_error"))
               for item in profiles)
    loss |= any(bool(item.get("embedded_camera_counter", {}).get("gap_events")) for item in profiles)
    incomplete = not all(run.get("frames") for run in (capture, queued, saved))
    statements.append("Transfer integrity error observed; inspect counts/IDs/FIFO and retain partial files."
                      if loss else ("Run incomplete: transfer integrity is unresolved; inspect the saved error and counts."
                                    if incomplete else "No transfer-loss evidence in the available checks; finite counts do not prove external trigger accounting."))
    actual = checks.get("camera_cadence_fps")
    host = checks.get("host_first_to_last_fps")
    baseline_host = capture.get("profile", {}).get("host_first_to_last_fps") or capture.get("total_fps")
    queued_host = queued.get("profile", {}).get("host_first_to_last_fps") or queued.get("total_fps")
    if source != "physical":
        statements.append("Mock source: physical camera/USB conclusions are unavailable.")
    elif actual is None:
        statements.append(f"Camera cadence unavailable: host timing cannot establish whether the camera ran at {target_fps:g} fps or quantify camera-side loss.")
    elif actual < target_fps * (1 - tolerance):
        statements.append("Camera timestamps show cadence below the reference; this does not alone identify sensor, SDK or host-induced throttling.")
    elif host is not None and host < actual * (1 - tolerance):
        statements.append("Camera cadence exceeds host retrieval: investigate SDK copying, Python handling and growing FIFO backlog; rate gap is not a missing-frame count.")
    else:
        statements.append("Available camera cadence and host retrieval are close; compare startup and post-capture processing.")
    if queued_host and baseline_host and queued_host < baseline_host * (1 - tolerance):
        statements.append("Queue-to-discard is slower than capture-only; queue/thread/copy overhead or changing run load is a candidate.")
    if host is not None and queued_host and host < queued_host * (1 - tolerance):
        statements.append("Saved-run host retrieval slows relative to discard: writing or concurrent processing may feed back onto acquisition.")
    if saved.get("synced_s", 0) > saved.get("total_close_s", 0):
        statements.append("Final fsync occurs after capture/close: it lowers durable FPS and increases turnaround, without changing already transferred frame counts.")
    statements.append("Stage times overlap. Hashing is measured acquisition work; use the uninstrumented short control to assess diagnostic overhead.")
    next_actions = ["Compare discard with writer queue trend, write/flush time and final fsync.",
                    "Use matched 4D format/sync results to test storage explanations."]
    if actual is None:
        next_actions.insert(0, "Obtain actual matched CamWare memory-recording cadence and supported camera timestamps; displayed FPS and host fallback cannot settle camera-side cadence.")
    else:
        next_actions.insert(0, "Compare actual camera cadence, host windows, SDK wait/image-copy time, and Python handling/stacking.")
    stages = saved.get("pipeline_stage_timing_s", {})
    series = checks.get("queue_series", [])
    if len(series) > 1 and series[-1][3] > series[0][3] + 2:
        statements.append("Pending queue grows during the saved run: writer service lags production; bounded RAM only postpones failure.")
    return {"observed_transfer_error": loss, "run_complete": not incomplete,
            "camera_side_missing_frame_count": None,
            "rate_comparison_note": "Differences below compare measurement boundaries/runs, not independent lost-frame counts or additive causes.",
            "reference_minus_camera_fps": target_fps - actual if actual else None,
            "camera_minus_host_fps": actual - host if actual and host else None,
            "host_minus_saved_wall_fps": host - saved["total_fps"] if host and saved.get("total_fps") else None,
            "saved_minus_durable_fps": saved["total_fps"] - saved["synced_fps"]
                                       if saved.get("total_fps") and saved.get("synced_fps") else None,
            "saved_time_outside_host_first_last_s": saved["total_close_s"] - (checks["frames_returned"] - 1) / host
                                                     if host and saved.get("total_close_s") else None,
            "fifo_fill_estimate_s_if_rates_persist": checks.get("fifo_capacity_frames") / (actual - host)
                                                     if actual and host and actual > host and checks.get("fifo_capacity_frames") else None,
            "fifo_fill_estimate_note": "Upper bound using empty FIFO/full capacity and steady rates; occupancy and throttling unknown. Not a measured overflow time.",
            "post_capture_fsync_s": saved.get("final_os_fsync_s"),
            "post_producer_writer_drain_s": stages.get("writer_drain_after_producer_s"),
            "external_trigger_accounting": "NOT_TESTED", "findings": statements,
            "next_actions": next_actions}
