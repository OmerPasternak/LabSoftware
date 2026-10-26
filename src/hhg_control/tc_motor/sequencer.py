"""Reusable, serialized motor operations with bounded motion and latched STOP."""

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Generic, TypeVar

from .drivers.base import BaseMotor, MotorError, MotorInfo, MotorLimits, MotorSafetyError, MotorStatus, finite

T = TypeVar("T")


class MotionAborted(MotorError):
    """Motion or measurement sequencing was cancelled; completion is not claimed."""


class MotionTimeout(MotorError, TimeoutError):
    """Motion, settling, or stop verification exceeded its bounded deadline."""


@dataclass(frozen=True)
class ScanPoint(Generic[T]):
    """Position/state readback paired with the unmodified measurement result."""

    status: MotorStatus
    measurement: T


class MotorSequencer:
    """Own one driver and serialize its I/O for GUI workers or measurement loops.

    ``request_stop`` is the only method intended for another thread during an
    operation. It sets a persistent event; the I/O owner sends STOP on its next
    bounded poll. ``acknowledge_stop`` explicitly rearms an idle sequencer.
    A software STOP depends on working communication and is not an emergency
    hardware interlock. Measurement callbacks must themselves have bounded waits.
    """

    def __init__(
        self,
        driver: BaseMotor,
        poll_interval_s: float = 0.05,
        position_tolerance_mm: float = 0.001,
        *,
        stop_timeout_s: float = 3.0,
    ) -> None:
        """Set positive poll/stop times in s and arrival tolerance in mechanical mm."""
        self.driver = driver
        self.poll_interval_s = self._positive(poll_interval_s, "Poll interval (s)")
        self.position_tolerance_mm = self._positive(position_tolerance_mm, "Position tolerance (mm)")
        self.stop_timeout_s = self._positive(stop_timeout_s, "Stop verification timeout (s)")
        self._operation_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stop_generation = 0
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

    @staticmethod
    def _positive(value: float, name: str) -> float:
        """Validate a finite, strictly positive duration or tolerance."""
        number = finite(value, name)
        if number <= 0:
            raise MotorSafetyError(f"{name} must be positive.")
        return number

    @property
    def limits(self) -> MotorLimits | None:
        """Expose the driver's immutable envelope, if a profile was supplied."""
        return self.driver.limits

    @property
    def is_connected(self) -> bool:
        """Return cached session ownership without performing hardware I/O."""
        return self.driver.is_connected

    @property
    def motion_enabled(self) -> bool:
        """Return cached motion permission without performing hardware I/O."""
        return self.driver.motion_enabled

    @property
    def communication_uncertain(self) -> bool:
        """Return the driver's latched serial fault without performing I/O."""
        return self.driver.communication_uncertain

    @property
    def stop_requested(self) -> bool:
        """Return whether STOP is latched and requires explicit acknowledgement."""
        return self._stop_event.is_set()

    @property
    def inspection_settings(self) -> dict:
        """Copy cached controller inspection values without performing hardware I/O."""
        return dict(getattr(self.driver, "inspection_settings", {}))

    @contextmanager
    def _operation(self):
        """Reject concurrent operations instead of queueing stale motion commands."""
        if not self._operation_lock.acquire(blocking=False):
            raise MotorError("Another motor operation is active; use request_stop() to cancel it.")
        try:
            yield
        finally:
            self._operation_lock.release()

    def connect(self) -> MotorInfo:
        """Connect without clearing STOP, homing, zeroing, or changing settings."""
        with self._operation():
            return self.driver.connect()

    def snapshot(self) -> MotorStatus:
        """Read one status on the I/O owner; do not poll concurrently with moves."""
        with self._operation():
            return self.driver.get_status()

    def setup_report(self) -> dict:
        """Inspect current state without motion; keep native units explicitly unverified.

        Settings are the last connection/preflight readbacks, not fresh settings
        queries. This report is diagnostic evidence, never a motion profile or
        proof of mounted clearance, mm calibration, or physical acceptance.
        """
        with self._operation():
            status = self.driver.get_status()
            info = self.driver.info
            if info is None:
                raise MotorError("Connect before collecting a setup report.")
            return {
                "schema_version": 1,
                "report_type": "tc_motor_setup_inspection",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "identity": asdict(info),
                "status": asdict(status),
                "native_settings": self.inspection_settings,
                "settings_source": "Last connection/preflight readbacks; not re-queried for export.",
                "units_note": (
                    "Mock values are synthetic mechanical mm."
                    if info.simulation else
                    "Native settings/position are controller units. SU readback alone does not prove mm; "
                    "position_mm is populated only after the reviewed profile matches."
                ),
                "reviewed_limits": asdict(self.limits) if self.limits is not None else None,
                "motion_profile_enabled": self.motion_enabled,
                "stop_latched": self.stop_requested,
                "move_permitted_now": bool(self.motion_enabled and status.is_ready
                                           and not status.is_moving and not self.stop_requested),
                "reference_check": (
                    "Controller reports READY; physical reference/zero still needs review."
                    if status.is_ready else
                    "Controller does not report READY. Do not infer reference from position alone."
                ),
                "homing_supported": False,
                "homing_note": "Home type/speed/timeout are not collected. Homing needs a separate reviewed procedure.",
                "not_proven_by_report": [
                    "Physical controller label and installed actuator/serial pairing.",
                    "Stage-specific encoder calibration and actual mm displacement/direction.",
                    "Mounted safe travel, per-move limit, cable clearance, and stopping distance.",
                    "Supervised physical motion, STOP/holding, and disconnect acceptance.",
                ],
            }

    def request_stop(self) -> None:
        """Set sequencer/driver cancellation events from any thread, without I/O."""
        with self._stop_lock:
            self._stop_generation += 1
            self._stop_event.set()
            # The driver method is event-only, so it can interrupt its own
            # read-only preflight before it dispatches a later move command.
            self.driver.request_stop()

    def acknowledge_stop(self) -> None:
        """Explicitly clear STOP only while connected, idle, and confirmed stopped."""
        with self._operation():
            with self._stop_lock:
                generation = self._stop_generation
            if self.driver.get_status().is_moving:
                raise MotorSafetyError("Cannot acknowledge STOP while the motor is moving.")
            with self._stop_lock:
                if generation != self._stop_generation:
                    raise MotionAborted("A new STOP arrived during acknowledgement; STOP remains latched.")
                self.driver.acknowledge_stop()
                self._stop_event.clear()

    def _check_cancel(self, cancel_event: threading.Event | None) -> None:
        """Raise on either cancellation source; retain the persistent STOP latch."""
        if self._stop_event.is_set() or (cancel_event is not None and cancel_event.is_set()):
            self.request_stop()
            raise MotionAborted("Motor operation aborted; STOP remains latched.")

    def _stop_and_verify(self) -> MotorStatus:
        """Send a controlled STOP and confirm a nonmoving state within a deadline."""
        deadline = time.monotonic() + self.stop_timeout_s
        self.driver.stop()
        while True:
            status = self.driver.get_status()
            if time.monotonic() >= deadline:
                raise MotionTimeout("STOP verification timed out; stopping is uncertain.")
            if not status.is_moving:
                return status
            self._pause_event.wait(min(self.poll_interval_s, max(0.0, deadline - time.monotonic())))

    def _cleanup_after_error(self, original: BaseException) -> None:
        """Preserve the original error and explicitly attach uncertain STOP cleanup."""
        self.request_stop()
        try:
            self._stop_and_verify()
        except BaseException as cleanup:
            detail = f"STOP/cleanup uncertain: {type(cleanup).__name__}: {cleanup}"
            original.args = (f"{original}\n{detail}",)
            setattr(original, "stop_cleanup_error", cleanup)

    def stop(self) -> MotorStatus:
        """Latch STOP, send it on the owner thread, and verify motion has stopped."""
        self.request_stop()
        with self._operation():
            return self._stop_and_verify()

    def disconnect(self) -> bool:
        """Close the driver; return True if a faulted inspection release is unverified.

        Motion-capable sessions still require confirmed standstill. A read-only
        inspection session that has lost communication may release its port
        without pretending that STOP or a fresh position was verified.
        """
        self.request_stop()
        with self._operation():
            if not self.driver.is_connected:
                return False
            if self.driver.can_release_inspection_session:
                self.driver.close()
                return True
            try:
                status = self.driver.get_status()
            except Exception:
                if self.driver.can_release_inspection_session:
                    self.driver.close()
                    return True
                self._stop_and_verify()
            else:
                if status.is_moving or self.driver.motion_enabled:
                    self._stop_and_verify()
            self.driver.close()
            return self.driver.communication_uncertain

    def release_after_manual_verification(self) -> None:
        """Release a failed physical connection after an external standstill check.

        The driver enforces communication-loss and attempted-STOP prerequisites.
        No further serial query or motion command is issued here.
        """
        self.request_stop()
        with self._operation():
            self.driver.release_after_manual_verification()

    def _checked_status(self, *, require_ready: bool = False) -> MotorStatus:
        """Reject faults, unknown mm coordinates, or travel outside the reviewed range."""
        status = self.driver.get_status()
        if status.error_code:
            raise MotorError(f"Controller error {status.error_code:#x}, state {status.state_code}: {status.state}.")
        if status.is_ready and status.is_moving:
            raise MotorError("Controller returned contradictory READY and MOVING status.")
        if status.position_mm is None:
            raise MotorSafetyError("Mechanical millimetre units have not been verified.")
        position = finite(status.position_mm, "Position readback (mm)")
        assert self.limits is not None
        self.limits.validate_position(position)
        if require_ready and (not status.is_ready or status.is_moving):
            raise MotorSafetyError(f"Motor is not READY (state {status.state_code}: {status.state}).")
        return status

    def _timing(self, timeout_s: float, settle_s: float) -> tuple[float, float]:
        """Validate per-position total timeout and requested stable settling time in s."""
        timeout = self._positive(timeout_s, "Motion timeout (s)")
        settle = finite(settle_s, "Settling time (s)")
        if settle < 0:
            raise MotorSafetyError("Settling time must not be negative.")
        return timeout, settle

    def _wait(self, deadline: float, cancel_event: threading.Event | None) -> None:
        """Sleep with bounded cancellation latency rather than busy-polling."""
        self._check_cancel(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MotionTimeout("Motion/settling timed out.")
        self._stop_event.wait(min(self.poll_interval_s, remaining))

    def _check_motion_deadline(self, deadline: float, cancel_event: threading.Event | None) -> None:
        """Refuse cancellation or an expired deadline before any motion dispatch."""
        self._check_cancel(cancel_event)
        if time.monotonic() >= deadline:
            raise MotionTimeout("Motion/settling timed out before motion dispatch.")

    def _move(
        self,
        target_mm: float,
        timeout_s: float,
        settle_s: float,
        cancel_event: threading.Event | None,
        on_status: Callable[[MotorStatus], None] | None,
    ) -> MotorStatus:
        """Run one already-validated target while owning the operation lock."""
        deadline = time.monotonic() + timeout_s
        self._check_motion_deadline(deadline, cancel_event)
        initial = self._checked_status(require_ready=True)
        assert self.limits is not None and initial.position_mm is not None
        self.limits.validate_move(initial.position_mm, target_mm)
        # Check once more immediately before dispatch so queued STOP is not lost.
        self._check_motion_deadline(deadline, cancel_event)
        with self.driver.motion_guard(lambda: self._check_motion_deadline(deadline, cancel_event)):
            self.driver.move_to(target_mm)
        settled_since: float | None = None
        while True:
            self._check_cancel(cancel_event)
            status = self._checked_status()
            now = time.monotonic()
            if now >= deadline:
                raise MotionTimeout("Motion/settling timed out.")
            if on_status is not None:
                on_status(status)
            self._check_cancel(cancel_event)
            if not status.is_moving:
                if not status.is_ready:
                    raise MotorError(f"Motor stopped without READY (state {status.state_code}: {status.state}).")
                assert status.position_mm is not None
                if abs(status.position_mm - target_mm) > self.position_tolerance_mm:
                    raise MotorError(f"Motor stopped at {status.position_mm:g} mm before reaching {target_mm:g} mm.")
                if settled_since is None:
                    settled_since = now
                if now - settled_since >= settle_s:
                    return status
            elif settled_since is not None:
                raise MotorError("Motor resumed motion while settling.")
            self._wait(deadline, cancel_event)

    def move_to(
        self,
        target_mm: float,
        *,
        timeout_s: float = 30.0,
        settle_s: float = 0.0,
        cancel_event: threading.Event | None = None,
        on_status: Callable[[MotorStatus], None] | None = None,
    ) -> MotorStatus:
        """Move to mechanical mm and settle; timeout includes motion and settling.

        The progress callback runs on the owning worker, never an extra thread.
        Abort, fault, timeout, or callback failure causes best-effort STOP and
        remains an exception; uncertain cleanup is attached to that exception.
        """
        with self._operation():
            timeout, settle = self._timing(timeout_s, settle_s)
            target = self.driver.validate_position(target_mm)
            try:
                return self._move(target, timeout, settle, cancel_event, on_status)
            except BaseException as error:
                self._cleanup_after_error(error)
                raise

    def move_relative(
        self,
        delta_mm: float,
        *,
        timeout_s: float = 30.0,
        settle_s: float = 0.0,
        cancel_event: threading.Event | None = None,
        on_status: Callable[[MotorStatus], None] | None = None,
    ) -> MotorStatus:
        """Compute a fresh absolute mm target and enforce the same motion safeguards."""
        delta = finite(delta_mm, "Relative displacement (mm)")
        with self._operation():
            timeout, settle = self._timing(timeout_s, settle_s)
            if not self.motion_enabled or self.limits is None:
                raise MotorSafetyError("Motion is locked; a reviewed safety profile is required.")
            try:
                self._check_cancel(cancel_event)
                current = self._checked_status(require_ready=True)
                assert current.position_mm is not None
                target = self.driver.validate_position(current.position_mm + delta)
                return self._move(target, timeout, settle, cancel_event, on_status)
            except BaseException as error:
                self._cleanup_after_error(error)
                raise

    def scan(
        self,
        positions_mm: Iterable[float],
        measure: Callable[[MotorStatus], T],
        *,
        timeout_s: float = 30.0,
        settle_s: float = 0.0,
        cancel_event: threading.Event | None = None,
        on_status: Callable[[MotorStatus], None] | None = None,
    ) -> list[ScanPoint[T]]:
        """Prevalidate an entire mm path, then move/settle/measure in that order.

        Timeout applies to each position including settling. Measurement results
        are returned unchanged with their readbacks; no camera or data processing
        is performed here. On failure, ``error.partial_results`` retains completed
        callback results; saving external data remains the callback owner's
        responsibility. Callbacks must honor their own
        bounded waits/cancellation; a Python callback cannot be forcibly stopped.
        """
        with self._operation():
            timeout, settle = self._timing(timeout_s, settle_s)
            if not callable(measure):
                raise TypeError("measure must be callable.")
            targets = [self.driver.validate_position(value) for value in positions_mm]
            if not targets:
                return []
            results: list[ScanPoint[T]] = []
            try:
                self._check_cancel(cancel_event)
                initial = self._checked_status(require_ready=True)
                assert self.limits is not None and initial.position_mm is not None
                current = initial.position_mm
                for target in targets:
                    self.limits.validate_move(current, target)
                    current = target
                for target in targets:
                    status = self._move(target, timeout, settle, cancel_event, on_status)
                    self._check_cancel(cancel_event)
                    measurement = measure(status)
                    results.append(ScanPoint(status, measurement))
                    self._check_cancel(cancel_event)
                return results
            except BaseException as error:
                setattr(error, "partial_results", tuple(results))
                self._cleanup_after_error(error)
                raise
