"""Serialized motor sequencing outside the Qt GUI thread.

The only cross-thread sequencer call is ``request_stop``, whose contract is to
set a threading event without controller I/O. No queued motion survives STOP.
"""

from dataclasses import asdict, dataclass
from threading import Condition, Event
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits


@dataclass(frozen=True)
class ConnectionSettings:
    """Explicit connection selection; mock limits never become physical limits."""

    mode: str = "Mock"
    resource: str = ""
    axis: int = 1
    profile_path: str = ""


@dataclass(frozen=True)
class ConnectionState:
    """Worker-owned connection state copied to the main thread."""

    connected: bool
    motion_enabled: bool
    has_session: bool
    limits: MotorLimits | None
    communication_uncertain: bool = False
    polling_paused: bool = False


def create_sequencer(settings: ConnectionSettings):
    """Build an unconnected sequencer in the worker; physical imports are lazy."""
    from hhg_control.tc_motor.sequencer import MotorSequencer

    if settings.mode == "Mock":
        from hhg_control.tc_motor.drivers.mock import MockMotor

        # This envelope exists only for the synthetic demonstration.
        limits = MotorLimits(0, 10, 1, 2, 2)
        return MotorSequencer(MockMotor(limits))
    if settings.mode != "Physical":
        raise ValueError("Unknown motor mode.")
    if not settings.resource.strip():
        raise ValueError("Enter the explicit VISA serial resource before connecting.")
    from hhg_control.tc_motor.drivers.smc100 import SMC100Motor, SafetyProfile

    profile = (
        SafetyProfile.from_json(settings.profile_path)
        if settings.profile_path.strip()
        else None
    )
    return MotorSequencer(
        SMC100Motor(settings.resource.strip(), axis=settings.axis, profile=profile)
    )


class MotorWorker(QThread):
    """Own a sequencer on one persistent thread with at most one accepted action.

    STOP cancels an accepted, unstarted move, latches cancellation during active
    motion, and requests a verified controlled stop. Disconnect/close waits for
    the sequencer's safe-cleanup success. No thread is forcibly terminated.
    """

    status_ready = pyqtSignal(object)
    info_ready = pyqtSignal(object)
    connection_changed = pyqtSignal(object)
    busy_changed = pyqtSignal(bool)
    stop_latched = pyqtSignal(bool)
    activity = pyqtSignal(str)
    error = pyqtSignal(str)
    cleanup_done = pyqtSignal(bool, str)

    def __init__(self, factory: Callable = create_sequencer, parent=None):
        """Start with no driver/session; constructing the worker performs no I/O."""
        super().__init__(parent)
        self._factory = factory
        self._condition = Condition()
        self._cancel = Event()
        self._sequencer = None
        self._connection_settings = None
        self._pending = None
        self._pending_stop = False
        self._pending_disconnect = False
        self._shutdown_on_disconnect = False
        self._busy = False
        self._stop_generation = 0
        self._poll_faulted = False

    @property
    def is_busy(self) -> bool:
        """Return the latest reservation rather than relying on queued Qt signals."""
        with self._condition:
            return self._busy

    @property
    def is_stop_latched(self) -> bool:
        """Read the cancellation latch without performing controller I/O."""
        return self._cancel.is_set()

    def submit(self, operation: str, *args) -> bool:
        """Accept one explicit action; refuse queueing behind any active action."""
        with self._condition:
            if self._busy or self._pending is not None:
                return False
            if operation == "snapshot" and self._poll_faulted:
                return False
            if operation in {"move_to", "move_relative"} and self._cancel.is_set():
                return False
            self._pending = (operation, args)
            self._busy = True
            self._condition.notify()
        self.busy_changed.emit(True)
        return True

    def request_stop(self) -> None:
        """Latch immediate cancellation without performing hardware I/O here."""
        with self._condition:
            self._stop_generation += 1
            self._cancel.set()
            if self._sequencer is not None:
                self._sequencer.request_stop()
            if self._pending is not None and self._pending[0] in {
                "move_to", "move_relative", "acknowledge_stop"
            }:
                self._pending = None
            self._pending_stop = True
            self._busy = True
            self._condition.notify()
        self.stop_latched.emit(True)
        self.busy_changed.emit(True)

    def request_disconnect(self, *, close_window: bool = False) -> None:
        """Atomically latch cancellation and queue disconnect as the next action.

        Publishing STOP before the disconnect flag creates a race where the
        worker can dispatch a separate ST first, including in inspection mode.
        """
        with self._condition:
            self._stop_generation += 1
            self._cancel.set()
            if self._sequencer is not None:
                self._sequencer.request_stop()
            self._pending = None
            self._pending_stop = False
            self._pending_disconnect = True
            self._shutdown_on_disconnect = close_window
            self._busy = True
            self._condition.notify()
        self.stop_latched.emit(True)
        self.busy_changed.emit(True)

    def _publish_connection(self) -> None:
        """Copy sequencer state; this method executes exclusively in the worker."""
        seq = self._sequencer
        if seq is not None and seq.stop_requested:
            self._cancel.set()
            self.stop_latched.emit(True)
        self.connection_changed.emit(ConnectionState(
            connected=bool(seq is not None and seq.is_connected),
            motion_enabled=bool(seq is not None and seq.motion_enabled),
            has_session=seq is not None,
            limits=seq.limits if seq is not None else None,
            communication_uncertain=bool(seq is not None and seq.communication_uncertain),
            polling_paused=self._poll_faulted,
        ))

    def _execute(self, operation: str, args: tuple) -> bool:
        """Perform one serialized operation; True means safe thread shutdown."""
        self.activity.emit(operation.replace("_", " ").capitalize())
        if operation == "connect":
            if self._sequencer is not None:
                raise RuntimeError("Disconnect the existing session before connecting another.")
            seq = self._factory(args[0])
            self._connection_settings = args[0]
            with self._condition:
                self._sequencer = seq
                if self._cancel.is_set():
                    seq.request_stop()
            self.info_ready.emit(seq.connect())
            self.status_ready.emit(seq.snapshot())
            self._poll_faulted = False
            if args[0].mode == "Physical":
                settings = getattr(seq, "inspection_settings", {})
                if settings:
                    self.activity.emit("Inspected native controller settings: " + repr(settings))
        elif operation == "disconnect":
            released_unverified = False
            if self._sequencer is not None:
                released_unverified = self._sequencer.disconnect()
                self._sequencer = None
                self._connection_settings = None
            self._poll_faulted = False
            self._publish_connection()
            self.cleanup_done.emit(True, (
                "Inspection port released after serial fault; current standstill was not reconfirmed."
                if released_unverified else "Stopped and disconnected."
            ))
            return self._shutdown_on_disconnect
        elif operation == "release_after_manual_verification":
            if self._sequencer is None:
                raise MotorError("No motor session is open for manual release.")
            self._sequencer.release_after_manual_verification()
            self._sequencer = None
            self._connection_settings = None
            self._poll_faulted = False
            self._publish_connection()
            self.cleanup_done.emit(True, "COM port released after your physical standstill confirmation; "
                                   "software STOP remains unverified.")
        elif operation == "stop":
            if self._sequencer is not None and self._sequencer.is_connected:
                self.status_ready.emit(self._sequencer.stop())
            self.activity.emit("STOP latched. Resume explicitly before another move.")
        elif operation == "acknowledge_stop":
            if self._sequencer is None:
                raise RuntimeError("Connect before resuming motion.")
            # A concurrent STOP must win over an in-progress acknowledgement.
            with self._condition:
                generation = self._stop_generation
            self._sequencer.acknowledge_stop()
            with self._condition:
                if generation == self._stop_generation:
                    self._cancel.clear()
                else:
                    self._sequencer.request_stop()
            self.stop_latched.emit(self._cancel.is_set())
            self.activity.emit("STOP remains latched." if self._cancel.is_set() else
                               "STOP acknowledged; a new move may be requested.")
        elif operation == "snapshot":
            self.status_ready.emit(self._sequencer.snapshot())
        elif operation == "save_setup_report":
            from pathlib import Path
            from hhg_control.tc_motor.reporting import save_setup_report

            if self._sequencer is None:
                raise RuntimeError("Connect before saving a setup report.")
            profile_path = self._connection_settings.profile_path
            if profile_path and Path(args[0]).resolve() == Path(profile_path).resolve():
                raise ValueError("Choose a report filename separate from the active safety profile.")
            report = self._sequencer.setup_report()
            report["connection_request"] = asdict(self._connection_settings)
            save_setup_report(report, args[0])
            self.activity.emit("Setup report saved: " + str(args[0]))
        elif operation in {"move_to", "move_relative"}:
            if self._cancel.is_set():
                raise RuntimeError("Move cancelled before execution; STOP is latched.")
            value, timeout_s, settle_s = args
            method = getattr(self._sequencer, operation)
            self.status_ready.emit(method(
                value,
                timeout_s=timeout_s,
                settle_s=settle_s,
                cancel_event=self._cancel,
                on_status=self.status_ready.emit,
            ))
            self.activity.emit("Move complete; motor is stationary.")
        else:
            raise ValueError(f"Unknown operation: {operation}")
        self._publish_connection()
        return False

    def run(self) -> None:
        """Process serialized work until an explicitly requested safe shutdown."""
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending_disconnect or
                                         self._pending_stop or self._pending is not None)
                if self._pending_disconnect:
                    self._pending_disconnect = False
                    self._pending_stop = False
                    item = ("disconnect", ())
                elif self._pending_stop:
                    self._pending_stop = False
                    item = ("stop", ())
                else:
                    item = self._pending
                    self._pending = None
            operation, args = item
            should_exit = False
            try:
                should_exit = self._execute(operation, args)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if operation == "snapshot":
                    self._poll_faulted = True
                if operation == "connect" and self._sequencer is not None and not self._sequencer.is_connected:
                    self._sequencer = None
                    self._connection_settings = None
                self.error.emit(message)
                if operation == "disconnect":
                    self._shutdown_on_disconnect = False
                    self.cleanup_done.emit(False, "Disconnect/stop unconfirmed: " + message)
                self._publish_connection()
            finally:
                with self._condition:
                    self._busy = bool(self._pending_disconnect or self._pending_stop or
                                      self._pending is not None)
                    busy = self._busy
                self.busy_changed.emit(busy)
            if should_exit:
                return
