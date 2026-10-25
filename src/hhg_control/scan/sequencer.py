"""One reserved motor, one finite recording per visit, verified delay attribution."""

from dataclasses import asdict
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from hhg_control.camera.sequencer.capture_pipeline import ClosedFilePipeline, verify_file
from hhg_control.camera.sequencer.recording import RecordingBoundary
from hhg_control.camera.sequencer.scan_manager import AcquisitionAborted
from hhg_control.safe_io import check_disk_space, filesystem_path
from hhg_control.tc_motor.drivers.base import MotorSafetyError
from .plan import ScanPlan


def stamp():
    """Record UTC and process-local monotonic nanoseconds for event ordering."""
    return {"utc": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns()}


def save_json(path, record):
    """Atomically publish/fsync this run's metadata without touching raw files."""
    temporary = Path(str(path) + ".tmp")
    with filesystem_path(temporary).open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(filesystem_path(temporary), filesystem_path(path))


@contextmanager
def reserve_run(directory):
    """Hold an OS file lock against concurrent recovery; process exit releases the lock."""
    with filesystem_path(directory / ".scan-owner.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class MotorCameraScan:
    """Coordinate public sequencers with a recording barrier before every next move.

    STOP is latched. Recovery never appends to or relabels an earlier recording.
    The coordinator commands only the existing motor/camera contracts.
    """

    def __init__(self, motor, camera_manager):
        """Accept connected sequencers with reviewed limits; perform no I/O."""
        self.motor, self.camera = motor, camera_manager
        self._stop = threading.Event()
        self._running = threading.Lock()
        self.last_run_dir = None

    def request_stop(self):
        """Latch cancellation from any thread; instrument owners perform all I/O."""
        self._stop.set()
        self.motor.request_stop()

    def acknowledge_stop(self):
        """Rearm explicitly after idle camera stop and motor standstill are confirmed."""
        if not self._running.acquire(blocking=False):
            raise RuntimeError("Scan cleanup is still active.")
        try:
            with self.motor.reserve(), self.camera.reserve():
                self.camera.confirm_recording_stopped()
                self.motor.acknowledge_stop()
                self._stop.clear()
        finally:
            self._running.release()

    def _cancelled(self):
        """Read events without device I/O, safe for the camera reader."""
        return self._stop.is_set() or self.motor.stop_requested

    def _check_cancel(self):
        """Refuse further motion/acquisition after STOP."""
        if self._cancelled():
            raise AcquisitionAborted("Scan STOP is latched; acknowledge before a new attempt.")

    def _position(self, target, tolerance):
        """Sample READY/position on the motor I/O owner; this is not continuous metrology."""
        self._check_cancel()
        status = self.motor.snapshot()
        if (status.error_code or not status.is_ready or status.is_moving or status.position_mm is None
                or abs(status.position_mm - target) > tolerance):
            raise MotorSafetyError(f"Motor is not READY within {tolerance:g} mm of {target:g} mm: {status}")
        return status

    def _settings(self):
        """Read actual camera settings while reserved and idle, before any move."""
        camera = self.camera.camera
        self.camera.confirm_recording_stopped()
        settings = {"identity": camera.get_sensor_info(), "roi": list(camera.get_roi()),
                    "exposure_s": camera.get_exposure_time(), "trigger_mode": camera.get_trigger_mode().value,
                    "readout_mode": camera.get_readout_mode().name}
        if settings["trigger_mode"].startswith("external"):
            settings["external_input"] = camera.get_external_trigger_input_settings()
        return json.loads(json.dumps(settings, default=str, sort_keys=True))

    def run(self, plan: ScanPlan, *, resume_directory=None, on_event=None):
        """Run/recover ordered visits; retain attempts and leave the motor at its last position.

        Recovery requires the identical plan, identities and camera settings.
        Only verified visits are skipped by index. An unfinished visit always
        gets a new attempt and recorder. No home, zero, return, or retry occurs.
        """
        if not isinstance(plan, ScanPlan):
            raise TypeError("A validated ScanPlan is required.")
        if not self._running.acquire(blocking=False):
            raise RuntimeError("Another scan is active.")
        execution_started_ns = time.monotonic_ns()
        execution_started = stamp()
        original_dir = self.camera.storage_dir
        record, run_dir, active = None, None, None
        metrics_enabled = False
        try:
            with self.motor.reserve(), self.camera.reserve(), ExitStack() as leases:
                try:
                    self._check_cancel()
                    if not self.motor.motion_enabled or self.motor.limits is None:
                        raise MotorSafetyError("Motor requires its reviewed safety profile and verified mm units.")
                    if not self.camera.camera.supports_isolated_recordings or self.camera.state != "IDLE":
                        raise RuntimeError("An idle camera with isolated recording support is required.")
                    settings = self._settings()
                    identity = asdict(self.motor.driver.info)
                    if identity["simulation"] != self.camera.camera.is_simulated:
                        raise ValueError("Motor and camera must both be Mock or both Physical; mixed provenance is refused.")
                    initial = self.motor.snapshot()
                    if not initial.is_ready or initial.is_moving or initial.error_code or initial.position_mm is None:
                        raise MotorSafetyError("Motor must be referenced, READY, and calibrated before a scan.")
                    points = list(plan.points())
                    if resume_directory is None:
                        run_dir = original_dir / ("scan_" + uuid4().hex)
                        filesystem_path(run_dir).mkdir()
                        leases.enter_context(reserve_run(run_dir))
                        record = {"version": 1, "run_id": run_dir.name, "plan_id": plan.plan_id,
                                  "plan": plan.to_dict(), "camera_settings": settings, "motor_identity": identity,
                                  "motor_inspection": self.motor.inspection_settings,
                                  "motor_limits": asdict(self.motor.limits),
                                  "simulation": identity["simulation"], "created": stamp(), "status": "PREFLIGHT",
                                  "points": [{"status": "PENDING", "attempts": []} for _ in points]}
                        metrics_enabled = True
                    else:
                        run_dir = Path(resume_directory).resolve()
                        leases.enter_context(reserve_run(run_dir))
                        record = json.loads(filesystem_path(run_dir / "run.json").read_text(encoding="utf-8"))
                        if (record["plan_id"] != plan.plan_id or record["camera_settings"] != settings
                                or record["motor_identity"] != identity
                                or record.get("motor_limits") != asdict(self.motor.limits)):
                            record = None  # Refusal must not overwrite the original run's state.
                            raise ValueError("Recovery plan or instrument identity/settings differ from the original run.")
                    self.last_run_dir = run_dir
                    next_index = next((i for i, point in enumerate(record["points"]) if point["status"] != "VERIFIED"), len(points))
                    if len(record["points"]) != len(points) or any(
                            point["status"] == "VERIFIED" for point in record["points"][next_index:]):
                        raise ValueError("Recovery contains an inconsistent point sequence.")
                    if resume_directory is not None:
                        for index, point in enumerate(record["points"][:next_index]):
                            self._check_cancel()
                            path = Path(point["attempts"][-1]["file"]).resolve()
                            if run_dir not in path.parents:
                                raise ValueError("Recovery data path is outside its run directory.")
                            verify_file(path, path.with_suffix(".pixels.sha256.jsonl"))
                            with filesystem_path(path.with_suffix(".pixels.sha256.jsonl")).open(encoding="utf-8") as handle:
                                context = json.loads(json.loads(next(handle))["settings"]["recording_context_json"])
                            if context["point_index"] != index or context["plan_id"] != plan.plan_id:
                                raise ValueError("Verified recovery file belongs to another plan/point.")
                            if (context["run_id"] != record["run_id"]
                                    or context["recording_id"] != point["attempts"][-1]["recording_id"]
                                    or context["target_position_mm"] != points[index][3].position_mm):
                                raise ValueError("Verified recovery recording identity or target does not match its visit.")
                    metrics_enabled = True
                    current = initial.position_mm
                    for _, _, _, visit in points[next_index:]:
                        self.motor.limits.validate_move(current, visit.position_mm)
                        current = visit.position_mm
                    width, height = settings["roi"][2] - settings["roi"][0], settings["roi"][3] - settings["roi"][1]
                    check_disk_space(run_dir, int((len(points) - next_index) * plan.frames_per_point * width * height * 2 * 1.1))
                    save_json(run_dir / "run.json", record)
                    preflight = ClosedFilePipeline(startup_timeout_s=plan.storage_timeout_s)
                    try:
                        preflight.start(self._cancelled)
                    finally:
                        preflight.close()
                    self._check_cancel()
                    for index, repeat, visit_index, visit in points[next_index:]:
                        self._check_cancel()
                        point = record["points"][index]
                        attempt_index = len(point["attempts"]) + 1
                        recording_id = uuid4().hex
                        attempt_dir = run_dir / f"point_{index:06d}" / f"attempt_{attempt_index:04d}_{recording_id}"
                        filesystem_path(attempt_dir).mkdir(parents=True)
                        active = {"recording_id": recording_id, "point_index": index, "repeat_index": repeat,
                                  "visit_index": visit_index, "attempt_index": attempt_index,
                                  "directory": str(attempt_dir), "status": "MOVING", "move_started": stamp()}
                        point["attempts"].append(active)
                        point["status"] = "IN_PROGRESS"
                        record["status"] = "RUNNING"
                        save_json(run_dir / "run.json", record)
                        if on_event:
                            on_event({"state": "MOVING", "point_index": index, "target_mm": visit.position_mm})
                        self.motor.move_to(visit.position_mm, timeout_s=plan.motion_timeout_s,
                                           settle_s=plan.settle_s, cancel_event=self._stop)
                        before = self._position(visit.position_mm, plan.position_tolerance_mm)
                        if self._settings() != settings:
                            raise RuntimeError("Camera settings changed before the recording.")
                        active["motor_before"], active["settled"] = asdict(before), stamp()
                        context = {"version": 1, "run_id": record["run_id"], "plan_id": plan.plan_id,
                                   **{k: active[k] for k in ("recording_id", "point_index", "repeat_index", "visit_index", "attempt_index")},
                                   "position_unit": "mm", "target_position_mm": visit.position_mm,
                                   "motor_before": asdict(before), "label": visit.label,
                                   "settled": active["settled"], "motor_identity": identity,
                                   "camera_settings": settings, "simulation": identity["simulation"],
                                   "attribution_method": "isolated_finite_recording_with_position_hold"}
                        if plan.fs_per_mm is not None:
                            context.update({"requested_delay_fs": (visit.position_mm - plan.zero_mm) * plan.fs_per_mm,
                                            "delay_zero_mm": plan.zero_mm, "delay_fs_per_mm": plan.fs_per_mm})
                        boundary = RecordingBoundary.create(context, capture_timeout_s=plan.capture_timeout_s,
                            storage_timeout_s=plan.storage_timeout_s, cleanup_timeout_s=plan.cleanup_timeout_s,
                            health_check=lambda target=visit.position_mm: self._position(target, plan.position_tolerance_mm))
                        self.camera.set_storage_dir(attempt_dir)
                        active["status"], active["capture_started"] = "ACQUIRING", stamp()
                        save_json(run_dir / "run.json", record)
                        with self.motor.hold_position():
                            if on_event:
                                on_event({"state": "ACQUIRING", "point_index": index, "target_mm": visit.position_mm})
                            rows = list(self.camera.execute_scan("motor_camera", "Stage position (mm)",
                                visit.position_mm, 0, 1, plan.frames_per_point, run_id=recording_id,
                                continuous=False, recording=boundary, abort_check=self._cancelled))
                            self._check_cancel()
                            if len(rows) != 1 or self.camera.last_scan_pipeline.get("files_verified") != 1:
                                raise AcquisitionAborted("Point did not finish its recording and all verification checks.")
                            self.camera.confirm_recording_stopped()
                            after = self._position(visit.position_mm, plan.position_tolerance_mm)
                        if self._settings() != settings:
                            raise RuntimeError("Camera settings changed during measurement.")
                        report = json.loads(filesystem_path(rows[0][2].with_suffix(".verification.json")).read_text())
                        if report["status"] != "VERIFIED" or not report.get("recording_context_verified"):
                            raise RuntimeError("Frame-to-delay attribution has not been verified.")
                        active.update({"status": "VERIFIED", "motor_after": asdict(after), "completed": stamp(),
                                       "file": str(rows[0][2]), "verification": report,
                                       "pipeline": self.camera.last_scan_pipeline})
                        point["status"] = "VERIFIED"
                        save_json(run_dir / "run.json", record)
                        active = None
                        if on_event:
                            on_event({"state": "VERIFIED", "point_index": index, "file": str(rows[0][2])})
                    record["status"], record["completed"] = "COMPLETE", stamp()
                    save_json(run_dir / "run.json", record)
                    return run_dir
                except BaseException as error:
                    self.request_stop()
                    cleanup = None
                    try:
                        self.motor.stop()
                    except Exception as stop_error:
                        cleanup = str(stop_error)
                    if record is not None and run_dir is not None:
                        record.update({"status": "CANCELLED" if isinstance(error, AcquisitionAborted) else "FAILED",
                                       "error": str(error), "ended": stamp(), "motor_stop_cleanup_error": cleanup})
                        if active is not None:
                            active.update({"status": record["status"], "error": str(error), "ended": stamp()})
                        try:
                            save_json(run_dir / "run.json", record)
                        except Exception as metadata_error:
                            error.args = (f"{error}; run status could not be saved: {metadata_error}",)
                    if cleanup:
                        error.args = (f"{error}; motor STOP cleanup uncertain: {cleanup}",)
                    raise
                finally:
                    self.camera.set_storage_dir(original_dir)
        finally:
            if metrics_enabled and record is not None and run_dir is not None:
                ended = stamp()
                elapsed_s = max(0.0, (time.monotonic_ns() - execution_started_ns) / 1e9)
                record.setdefault("execution_segments", []).append({
                    "started": execution_started, "ended": ended, "elapsed_s": elapsed_s,
                })
                verified_frames = sum(
                    int(attempt.get("verification", {}).get("frames_verified", 0))
                    for point in record.get("points", [])
                    for attempt in point.get("attempts", [])
                    if attempt.get("status") == "VERIFIED"
                )
                active_s = sum(segment["elapsed_s"] for segment in record["execution_segments"])
                record["performance"] = {
                    "verified_frames": verified_frames,
                    "active_execution_s": active_s,
                    "effective_fps": verified_frames / active_s if active_s > 0 else 0.0,
                    "definition": "verified frames divided by active scan execution time, including preflight, motor actions, settling, acquisition, storage, and verification; recovery downtime excluded",
                }
                try:
                    save_json(run_dir / "run.json", record)
                except Exception:
                    pass  # Preserve the original failure/result if performance metadata cannot be updated.
            self._running.release()
