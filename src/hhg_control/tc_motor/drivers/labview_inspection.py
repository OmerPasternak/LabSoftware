"""Short VISA sessions for read-only TC motor inspection in the GUI."""

from .base import MotorError, MotorInfo, MotorSafetyError, MotorStatus
from .labview_reference import LabviewReferenceTransport
from .serial_framing import InvalidSerialBytes
from .smc100 import SMC100Motor


LABVIEW_INSPECTION = "labview-inspection"


class LabviewInspectionTransport(LabviewReferenceTransport):
    """Retain the captured receive behavior and allow the GUI's startup queries."""

    ALLOWED_QUERIES = LabviewReferenceTransport.ALLOWED_QUERIES | {
        "VE", "ZX?", "TE", "SU?", "VA?", "AC?", "SL?", "SR?", "BA?", "BH?",
    }


class LabviewInspectionMotor(SMC100Motor):
    """Inspect native positions using short, exclusive VISA windows.

    A single corrupt-byte reply may trigger a fresh session over the same
    backend. Only a stationary session without a preceding controller fault
    can recover. The failed frame is retained and only fresh readback is shown.
    """

    query_only = True
    SNAPSHOTS_PER_WINDOW = 8
    FAULT_QUIET_S = 0.300

    def __init__(self, resource: str, axis: int = 1, *, transport_factory=None):
        """Prepare one explicit port and axis without a motion profile."""
        super().__init__(resource, axis=axis, transport_factory=(
            transport_factory or (lambda: LabviewInspectionTransport(resource))
        ))
        self._monitoring = False
        self._snapshots = 0
        self._renewals = 0
        self._failed_exchange = None
        self.recovered_exchange = None

    @property
    def transport_details(self) -> dict:
        """Expose the session count and any recovered exchange without I/O."""
        return {**super().transport_details, "inspection_window_renewals": self._renewals,
                "inspection_recovery": self.recovered_exchange}

    def connect(self) -> MotorInfo:
        """Inspect identity and settings, with one fresh attempt after corrupt bytes."""
        with self._lock:
            if self.is_connected:
                raise MotorError("Already connected; disconnect before reconnecting.")
            self._monitoring = False
            self._snapshots = self._renewals = 0
            self._failed_exchange = None
            self.recovered_exchange = None
            try:
                info = super().connect()
            except MotorError as original:
                if self.is_connected or not self._can_recover(original):
                    raise
                self._remember_failure(original)
                try:
                    self._wait_for_clean_window()
                    info = super().connect()
                except Exception as recovery:
                    raise MotorError(f"{original}; fresh inspection connection failed: {recovery}") from recovery
                self._renewals += 1
                self.recovered_exchange = self._failed_exchange
            self._monitoring = True
            return info

    def _can_recover(self, error: MotorError) -> bool:
        """Reject repeated faults, cancelled work, and a preceding controller error."""
        cause = error.__cause__
        return (self._failed_exchange is None and isinstance(cause, InvalidSerialBytes)
                and self._fault == str(cause) and not self.stop_requested)

    def _remember_failure(self, error: MotorError) -> None:
        """Keep the original raw exchange before a new query can replace it."""
        self._failed_exchange = {
            "error": str(error), "wire": dict(getattr(self._transport, "last_exchange", {})),
        }

    def _wait_for_clean_window(self) -> None:
        """Wait one receive timeout after a fault, interruptible by disconnect."""
        if self._cancel.wait(self.FAULT_QUIET_S):
            raise MotorError("Inspection recovery cancelled.")

    def _renew_window(self, *, after_fault: bool = False) -> None:
        """Release the old port, reopen the same one and verify identity."""
        self._stream_uncertain = True
        try:
            self._transport.close()
            if after_fault:
                self._wait_for_clean_window()
            if self.stop_requested:
                raise MotorError("Inspection session renewal cancelled.")
            self._transport.open()
            self._stream_uncertain = False
            info = self.info
            if (info is None or self._query("VE") != info.controller_version
                    or self._query("ZX?") != info.stage_reference):
                raise MotorSafetyError("Controller identity changed across inspection sessions.")
        except Exception:
            self._stream_uncertain = True
            raise
        self._renewals += 1
        self._snapshots = 0

    def get_status(self) -> MotorStatus:
        """Return fresh status, with at most one recovered corrupt-byte exchange."""
        with self._lock:
            if not self._monitoring:
                return super().get_status()
            if self.stop_requested:
                raise MotorError("Inspection read cancelled.")
            if (self._snapshots >= self.SNAPSHOTS_PER_WINDOW and not self._fault
                    and not self.communication_uncertain and not self._last_status_moving):
                self._renew_window()
            try:
                status = super().get_status()
            except MotorError as original:
                if self._last_status_moving or not self._can_recover(original):
                    raise
                self._remember_failure(original)
                try:
                    self._renew_window(after_fault=True)
                    self._fault = ""
                    status = super().get_status()
                except Exception as recovery:
                    self._fail(str(recovery), stream=True)
                    raise MotorError(f"{original}; fresh inspection read failed: {recovery}") from recovery
                self.recovered_exchange = self._failed_exchange
            self._snapshots += 1
            return status

    def _query(self, command: str) -> str:
        """Abort between read queries when the user requests disconnect."""
        if self.stop_requested:
            raise MotorError("Inspection read cancelled.")
        return super()._query(command)

    def stop(self) -> None:
        """Refuse STOP in the query-only mode, which cannot own motion."""
        raise MotorSafetyError("The LabVIEW inspection connection cannot send STOP.")

    def close(self) -> None:
        """Release the host port only, even if the current status is unknown."""
        with self._lock:
            if self.is_connected:
                try:
                    self._release_port_unverified()
                except Exception as error:
                    self._fail(str(error), stream=True)
                    raise
            self._monitoring = False
