"""Conservative SMC100CC/LTA-HS adapter, based on Newport EDH0206En2054.

Only PA (absolute move) and addressed ST (controlled stop) change motion.
Connection never resets, homes, enables, zeroes, or programs the controller.
Native units are not called mm until a reviewed calibration profile matches.
"""

from dataclasses import dataclass, replace
import json
from math import isclose
from pathlib import Path
import re
from threading import RLock
import time

from .base import BaseMotor, MotorError, MotorInfo, MotorLimits, MotorSafetyError, MotorStatus, finite


# PyVISA ResourceManager instances can share a backend session. Keep one manager
# per backend for this process; disconnect closes only this motor's serial handle.
_VISA_MANAGERS: dict[str, object] = {}
_MANAGER_LOCK = RLock()


@dataclass(frozen=True)
class SafetyProfile:
    """Human-reviewed physical setup, in the controller's existing mm coordinates.

    The encoder increment must come from this actuator's calibration/control
    report, not a nominal catalog value. Clearance is a reviewed allowance for
    stopping/rounding outside target bounds, not a software guarantee of safety.
    """

    limits: MotorLimits
    expected_controller_version: str
    expected_stage_reference: str
    expected_encoder_increment_mm: float
    stop_clearance_mm: float
    units_confirmed_mm: bool
    controller_confirmed_cc: bool
    mounting_clearance_checked: bool

    def __post_init__(self) -> None:
        """Reject incomplete reviews, unsupported actuators, and invalid scaling."""
        if not isinstance(self.limits, MotorLimits):
            raise MotorSafetyError("A reviewed MotorLimits envelope is required.")
        for name in ("units_confirmed_mm", "controller_confirmed_cc", "mounting_clearance_checked"):
            if getattr(self, name) is not True:
                raise MotorSafetyError(f"Physical motion requires {name}=true after review.")
        if not isinstance(self.expected_controller_version, str) or not self.expected_controller_version.strip():
            raise MotorSafetyError("Exact inspected controller version is required.")
        if self.expected_stage_reference != "LTA-HS":
            raise MotorSafetyError("This driver supports the reviewed LTA-HS only.")
        for name in ("expected_encoder_increment_mm", "stop_clearance_mm"):
            value = finite(getattr(self, name), name)
            if value <= 0:
                raise MotorSafetyError(f"{name} must be positive.")
            object.__setattr__(self, name, value)
        if self.expected_encoder_increment_mm > self.stop_clearance_mm:
            raise MotorSafetyError("Clearance must exceed an encoder increment.")

    @classmethod
    def from_json(cls, path: str | Path) -> "SafetyProfile":
        """Load an explicit schema-1 profile; reject missing/unknown fields."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.pop("schema_version", None) != 1:
                raise ValueError("Expected schema_version 1.")
            data["limits"] = MotorLimits(**data["limits"])
            return cls(**data)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise MotorSafetyError(f"Invalid physical safety profile: {exc}") from exc


class VisaSerialTransport:
    """Lazy PyVISA serial session: 57600 baud, 8N1, XON/XOFF, CRLF.

    A resource is opened only by ``open``. No discovery or auto-reconnection.
    The default backend uses the existing system VISA installation (NI-VISA
    when installed); ``@py`` can be selected explicitly by Python callers.
    """

    def __init__(self, resource: str, *, backend: str = "", timeout_ms: int = 300):
        """Remember an explicit COM/ASRL resource and bounded I/O timeout in ms."""
        if not isinstance(resource, str) or not re.fullmatch(r"(?:COM[1-9]\d*|ASRL[1-9]\d*::INSTR)", resource, re.I):
            raise MotorSafetyError("Specify one serial resource, e.g. COM21 or ASRL21::INSTR.")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 50 <= timeout_ms <= 1000:
            raise MotorSafetyError("Serial timeout must be 50–1000 ms.")
        self.resource = f"ASRL{resource[3:]}::INSTR" if resource.upper().startswith("COM") else resource.upper()
        self.backend = backend
        self.timeout_ms = timeout_ms
        self._manager = None
        self._session = None

    def open(self) -> None:
        """Open and configure only the host serial port; discard stale input bytes."""
        try:
            import pyvisa
            from pyvisa import constants
        except ImportError as exc:
            raise MotorError('Physical mode requires pip install ".[hardware-smc100]" and a VISA backend.') from exc
        try:
            with _MANAGER_LOCK:
                if self.backend not in _VISA_MANAGERS:
                    _VISA_MANAGERS[self.backend] = pyvisa.ResourceManager(self.backend)
                self._manager = _VISA_MANAGERS[self.backend]
            self._session = self._manager.open_resource(
                self.resource, access_mode=constants.AccessModes.exclusive_lock,
                open_timeout=self.timeout_ms,
            )
            self._session.timeout = self.timeout_ms
            self._session.baud_rate = 57600
            self._session.data_bits = 8
            self._session.parity = constants.Parity.none
            self._session.stop_bits = constants.StopBits.one
            self._session.flow_control = constants.ControlFlow.xon_xoff
            self._session.read_termination = "\r\n"
            self._session.write_termination = "\r\n"
            self._session.encoding = "ascii"
            self._session.flush(constants.BufferOperation.discard_read_buffer)
        except Exception:
            self.close()
            raise

    def query(self, command: str) -> str:
        """Perform one bounded request/response, with no automatic retry."""
        return self._session.query(command)

    def write(self, command: str) -> None:
        """Write one command with the configured CRLF terminator."""
        self._session.write(command)

    def close(self) -> None:
        """Close only this serial session; never close a shared resource manager."""
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:
                raise MotorError(f"VISA port cleanup failed: {exc}") from exc
            self._session = None


class SMC100Motor(BaseMotor):
    """Physical adapter with explicit identity, unit, state, and limit checks.

    ``profile=None`` is inspection-only. A valid profile permits bounded PA
    moves only while READY. Configuration is read back before every move and
    is never rewritten. Any observed fault latches movement off until reconnect.
    """

    READY = {"32", "33", "34", "35"}
    MOVING = {"28", "1E", "1F", "46", "47"}

    def __init__(self, resource: str, axis: int = 1, profile: SafetyProfile | None = None,
                 *, transport_factory=None, backend: str = "", timeout_ms: int = 300):
        """Prepare one axis (1–31) without opening hardware; use reviewed mm limits."""
        if isinstance(axis, bool) or not isinstance(axis, int) or not 1 <= axis <= 31:
            raise MotorSafetyError("Axis must be an integer from 1 to 31; broadcasts are forbidden.")
        if profile is not None and not isinstance(profile, SafetyProfile):
            raise MotorSafetyError("profile must be a SafetyProfile or None.")
        super().__init__(profile.limits if profile else None)
        # Validate the serial resource even with an injected offline transport.
        template = VisaSerialTransport(resource, backend=backend, timeout_ms=timeout_ms)
        self._transport_factory = transport_factory or (lambda: template)
        self._transport = None
        self.axis = axis
        self.profile = profile
        self._lock = RLock()
        self._fault = ""
        self._stream_uncertain = False
        self._owned_motion = False
        self._preparing_move = False
        self._units_verified = False
        self.inspection_settings: dict[str, float] = {}

    def _fail(self, message: str, *, stream: bool = False) -> None:
        """Latch faults so clearing controller error registers cannot re-enable motion."""
        self._motion_enabled = False
        self._fault = self._fault or message
        self._stream_uncertain |= stream
        self._units_verified = False
        if self.info is not None:
            self.info = replace(self.info, units_verified=False)

    def _query(self, command: str) -> str:
        """Validate exact axis/command prefix; reject unsynchronized responses."""
        if self._preparing_move:
            self.check_motion_guard()
        if self._preparing_move and self.stop_requested:
            raise MotorSafetyError("STOP requested during motion preparation; no new move is allowed.")
        if self._stream_uncertain:
            raise MotorError("Serial response stream is uncertain; stop cannot be verified through this session.")
        try:
            response = self._transport.query(f"{self.axis}{command}")
            prefix = f"{self.axis}{command[:2]}"
            if not isinstance(response, str) or not response.startswith(prefix):
                raise ValueError(f"Unexpected reply for {prefix}: {response!r}")
            body = response[len(prefix):].strip()
            if not body or "\r" in body or "\n" in body:
                raise ValueError(f"Malformed reply for {prefix}.")
            return body
        except Exception as exc:
            self._fail(str(exc), stream=True)
            raise MotorError(f"SMC100 query {command} failed: {exc}") from exc

    def _number(self, command: str) -> float:
        """Read finite native numeric values without guessing units or stripping prefixes."""
        body = self._query(command)
        try:
            return finite(float(body), command)
        except ValueError as exc:
            self._fail(f"Invalid numeric reply: {body!r}", stream=True)
            raise MotorError(f"Invalid numeric reply to {command}: {body!r}") from exc

    def _command_error(self) -> None:
        """Read and preserve the clearing TE register; @ means no command error."""
        code = self._query("TE")
        if code != "@":
            self._fail(f"Controller command error {code}")
            raise MotorError(self._fault)

    def _read_settings(self) -> dict[str, float]:
        """Inspect scaling, speed, acceleration, limits, and compensation, without writes."""
        settings = {key: self._number(key + "?") for key in ("SU", "VA", "AC", "SL", "SR", "BA", "BH")}
        self._command_error()
        self.inspection_settings = settings
        return settings

    def _validate_settings(self, settings: dict[str, float]) -> None:
        """Require reviewed scaling, adequate clearance, and unchanged safe configuration."""
        profile = self.profile
        if profile is None:
            return
        limits = profile.limits
        if not isclose(settings["SU"], profile.expected_encoder_increment_mm, rel_tol=1e-8, abs_tol=0):
            raise MotorSafetyError("Encoder increment differs from the reviewed mm calibration.")
        if not 0 < settings["VA"] <= limits.max_velocity_mm_s:
            raise MotorSafetyError("Controller velocity exceeds the reviewed maximum or is invalid.")
        if not 0 < settings["AC"] <= limits.max_acceleration_mm_s2:
            raise MotorSafetyError("Controller acceleration exceeds the reviewed maximum or is invalid.")
        if settings["BA"] != 0 or settings["BH"] != 0:
            raise MotorSafetyError("Nonzero backlash/hysteresis compensation requires a separate excursion review.")
        clearance = profile.stop_clearance_mm
        if (settings["SL"] + clearance > limits.min_position_mm
                or settings["SR"] - clearance < limits.max_position_mm):
            raise MotorSafetyError("Reviewed target envelope lacks clearance inside controller limits.")
        if self.info.controller_version != profile.expected_controller_version:
            raise MotorSafetyError("Controller version does not match the reviewed identity.")
        if self.info.stage_reference != profile.expected_stage_reference:
            raise MotorSafetyError("Stage reference does not match the reviewed identity.")

    def connect(self) -> MotorInfo:
        """Open only the requested port and inspect identity/configuration; never move."""
        with self._lock:
            if self._is_connected:
                raise MotorError("Already connected; disconnect before reconnecting.")
            self._fault = ""
            self._stream_uncertain = False
            self._units_verified = False
            self._transport = self._transport_factory()
            # Own the connection attempt until even a failed open is cleaned up.
            # A cleanup failure must remain visible to the GUI close guard.
            self._is_connected = True
            try:
                self._transport.open()
                version = self._query("VE")
                stage = self._query("ZX?")
                if "SMC" not in version.upper() or stage != "LTA-HS":
                    raise MotorSafetyError(f"Wrong instrument: controller={version!r}, stage={stage!r}.")
                self.info = MotorInfo(version, stage, self.axis, False, False)
                # TS errors may clear on read: read/latch them before any other status operation.
                status = self.get_status()
                if status.error_code or status.is_moving:
                    raise MotorSafetyError("Connect requires a stationary, fault-free controller.")
                self._command_error()
                self._validate_settings(self._read_settings())
                if self.profile is not None:
                    self._units_verified = True
                    self.info = replace(self.info, units_verified=True)
                    status = self.get_status()
                    self.profile.limits.validate_position(status.position_mm)
                self._motion_enabled = self.profile is not None and status.is_ready and not self._fault
                return self.info
            except Exception as exc:
                self._motion_enabled = False
                self._units_verified = False
                if self.info is not None:
                    self.info = replace(self.info, units_verified=False)
                try:
                    self._transport.close()
                except Exception as cleanup:
                    raise MotorError(f"Connect failed: {exc}; resource cleanup also failed: {cleanup}") from exc
                self._is_connected = False
                self.info = None
                raise

    def get_status(self) -> MotorStatus:
        """Read TS then TP; mm is withheld when calibration has not been reviewed."""
        with self._lock:
            self.require_connected()
            body = self._query("TS")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", body):
                self._fail("Malformed controller status", stream=True)
                raise MotorError(f"Malformed TS response: {body!r}")
            error = int(body[:4], 16)
            state = body[4:].upper()
            known_states = {f"{code:02X}" for code in range(0x0A, 0x12)} | {"14", "3C", "3D", "3E"} | self.READY | self.MOVING
            if state not in known_states:
                self._fail(f"Unknown controller state {state}", stream=True)
                raise MotorError(f"Unknown controller state {state}; standstill is unconfirmed.")
            if error:
                self._fail(f"Positioner error 0x{error:04X}")
            if self._motion_enabled and state not in self.READY:
                if state != "28" or not self._owned_motion:
                    self._fail(f"Unexpected state/reference change ({state}); reconnect and review coordinates.")
                    if state in self.MOVING:
                        self.stop()
            if state in self.READY:
                self._owned_motion = False
            position = self._number("TP")
            verified = self._units_verified
            label = "READY" if state in self.READY else "MOVING" if state in self.MOVING else "NOT READY"
            return MotorStatus(position if verified else None,
                               (self._fault or label), state, error,
                               state in self.MOVING, state in self.READY and not error and not self._fault,
                               native_position=position)

    def move_to(self, target_mm: float) -> None:
        """Start one PA move after fresh identity/settings/readiness/rounded-target checks."""
        with self._lock:
            target = self.validate_position(target_mm)
            if self._fault:
                raise MotorSafetyError(f"Motion is latched off: {self._fault}")
            if self.stop_requested:
                raise MotorSafetyError("STOP requested; explicitly resume only after standstill.")
            self._preparing_move = True
            try:
                # Detect a changed stage/configuration without silently reprogramming it.
                if self._query("ZX?") != self.info.stage_reference or self._query("VE") != self.info.controller_version:
                    raise MotorSafetyError("Instrument identity changed during this session.")
                settings = self._read_settings()
                self._validate_settings(settings)
                status = self.get_status()
                if not status.is_ready:
                    raise MotorSafetyError(f"Controller is not READY: {status.state} ({status.state_code}).")
                self.limits.validate_move(status.position_mm, target)
                # Native PA rounds to encoder counts; require all possible rounding
                # to remain in the reviewed target envelope and per-move limit.
                half_count = settings["SU"] / 2
                for rounded_bound in (target - half_count, target + half_count):
                    self.limits.validate_move(status.position_mm, rounded_bound)
                self.check_motion_guard()
                if self.stop_requested:
                    raise MotorSafetyError("STOP requested during motion preparation; move cancelled.")
                self._preparing_move = False
                self._owned_motion = True  # A failed write may still have reached the motor.
                self._transport.write(f"{self.axis}PA{target:.12f}")
                self._command_error()
            except Exception as exc:
                self._preparing_move = False
                if not self.stop_requested or self._owned_motion:
                    self._fail(str(exc))
                if self._owned_motion:
                    try:
                        self.stop()
                    except Exception as cleanup:
                        raise MotorError(f"Move failed: {exc}; STOP is unconfirmed: {cleanup}") from exc
                raise
            finally:
                self._preparing_move = False

    def stop(self) -> None:
        """Send addressed ST only; preserves servo holding and does not prove standstill."""
        with self._lock:
            self.require_connected()
            self.request_stop()
            # STOP aborts any preparation. Its TE verification must remain usable
            # even when called from a status check within that preparation.
            self._preparing_move = False
            try:
                self._transport.write(f"{self.axis}ST")
                if self._stream_uncertain:
                    raise MotorError("STOP sent, but communication loss prevents verification.")
                self._command_error()
            except Exception as exc:
                self._fail(f"STOP uncertain: {exc}")
                raise MotorError(f"STOP could not be confirmed: {exc}") from exc

    def close(self) -> None:
        """Confirm standstill before release; leave connection open on uncertain cleanup."""
        with self._lock:
            if not self.is_connected:
                return
            if self._owned_motion or self._stream_uncertain:
                self.stop()
            deadline = time.monotonic() + 3.0
            status = self.get_status()
            if status.is_moving:
                self.stop()
            while status.is_moving:
                if time.monotonic() >= deadline:
                    raise MotorError("Disconnect refused: standstill was not confirmed.")
                time.sleep(0.05)
                status = self.get_status()
            self._transport.close()
            self._is_connected = False
            self._motion_enabled = False
            self._owned_motion = False
            self.info = None
