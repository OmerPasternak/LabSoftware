"""Motor contract: mechanical millimetres, seconds, and explicit apparatus limits."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from threading import Event, get_ident


class MotorError(RuntimeError):
    """Controller, communication, or motion-completion failure."""


class MotorSafetyError(MotorError, ValueError):
    """Operation refused because its safety conditions are not established."""


def finite(value: float, name: str) -> float:
    """Return a finite real number, rejecting booleans and nonnumeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MotorSafetyError(f"{name} must be a finite number.")
    value = float(value)
    if not isfinite(value):
        raise MotorSafetyError(f"{name} must be finite.")
    return value


@dataclass(frozen=True)
class MotorLimits:
    """Reviewed operating envelope in mm, mm/s, and mm/s²; no physical defaults.

    Bounds refer to the existing controller coordinate system, not necessarily
    the actuator's full stroke. ``max_move_mm`` limits each requested move.
    """

    min_position_mm: float
    max_position_mm: float
    max_velocity_mm_s: float
    max_acceleration_mm_s2: float
    max_move_mm: float

    def __post_init__(self) -> None:
        """Reject invalid envelopes before any driver can use them."""
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, finite(getattr(self, name), name))
        if self.min_position_mm >= self.max_position_mm:
            raise MotorSafetyError("Minimum position must be below maximum position.")
        if self.max_position_mm - self.min_position_mm > 50:
            raise MotorSafetyError("LTA-HS operating span cannot exceed its nominal 50 mm stroke.")
        if not 0 < self.max_velocity_mm_s <= 5:
            raise MotorSafetyError("Maximum velocity must be > 0 and <= 5 mm/s.")
        if not 0 < self.max_acceleration_mm_s2 <= 20:
            raise MotorSafetyError("Maximum acceleration must be > 0 and <= 20 mm/s².")
        if not 0 < self.max_move_mm <= self.max_position_mm - self.min_position_mm:
            raise MotorSafetyError("Maximum move must be positive and no wider than the envelope.")

    def validate_position(self, position_mm: float) -> float:
        """Reject positions outside both bounds; never clip or substitute a target."""
        value = finite(position_mm, "Position (mm)")
        if not self.min_position_mm <= value <= self.max_position_mm:
            raise MotorSafetyError(
                f"Position {value:g} mm is outside [{self.min_position_mm:g}, "
                f"{self.max_position_mm:g}] mm."
            )
        return value

    def validate_move(self, current_mm: float, target_mm: float) -> float:
        """Validate readback, target, and maximum displacement in millimetres."""
        current = self.validate_position(current_mm)
        target = self.validate_position(target_mm)
        if abs(target - current) > self.max_move_mm + 1e-12:
            raise MotorSafetyError(f"Move exceeds the {self.max_move_mm:g} mm per-move limit.")
        return target


@dataclass(frozen=True)
class MotorInfo:
    """Identity and connection provenance; simulated identity is always explicit."""

    controller_version: str
    stage_reference: str
    axis: int
    simulation: bool
    units_verified: bool


@dataclass(frozen=True)
class MotorStatus:
    """Measured position in mm and controller state, without GUI dependencies."""

    position_mm: float | None
    state: str
    state_code: str
    error_code: int
    is_moving: bool
    is_ready: bool
    native_position: float | None = None


class BaseMotor(ABC):
    """Nonblocking motion interface shared by the physical driver and mock.

    One sequencer owns the driver. ``move_to`` starts a move and returns;
    the sequencer polls ``get_status`` with bounded waits and cancellation.
    No automatic homing, zeroing, enabling, or configuration download is allowed.
    """

    def __init__(self, limits: MotorLimits | None = None) -> None:
        """Store a reviewed envelope, or None for inspection-only access."""
        self.limits = limits
        self._is_connected = False
        self._motion_enabled = False
        self._cancel = Event()
        self._motion_guard_check: tuple[int, Callable[[], None]] | None = None
        self.info: MotorInfo | None = None

    @contextmanager
    def motion_guard(self, check: Callable[[], None]):
        """Temporarily install the I/O owner's cancellation/deadline check.

        Drivers call ``check_motion_guard`` during read-only motion preflight
        and immediately before dispatch. The hook performs no hardware I/O and
        is always restored, including when validation or communication fails.
        """
        if not callable(check):
            raise TypeError("Motion guard must be callable.")
        previous = self._motion_guard_check
        owner = get_ident()
        if previous is not None and previous[0] != owner:
            raise MotorSafetyError("The motion guard belongs to another I/O owner.")
        self._motion_guard_check = (owner, check)
        try:
            yield
        finally:
            self._motion_guard_check = previous

    def check_motion_guard(self) -> None:
        """Refuse latched STOP and run the owner-only preflight guard without I/O."""
        if self.stop_requested:
            raise MotorSafetyError("STOP is latched; acknowledge it before a new move.")
        guard = self._motion_guard_check
        if guard is not None:
            if guard[0] != get_ident():
                raise MotorSafetyError("Motion preflight was called outside its I/O owner.")
            guard[1]()

    @property
    def stop_requested(self) -> bool:
        """Thread-safe abort latch, including pending motion preflight."""
        return self._cancel.is_set()

    def request_stop(self) -> None:
        """Set the abort latch without I/O, safe from the GUI or another thread."""
        self._cancel.set()

    def acknowledge_stop(self) -> None:
        """Clear abort only when the owning sequencer has confirmed idle standstill."""
        self._cancel.clear()

    @property
    def is_connected(self) -> bool:
        """Whether this object owns an open controller/mock session."""
        return self._is_connected

    @property
    def motion_enabled(self) -> bool:
        """Whether this connection has passed its motion prerequisites."""
        return self._is_connected and self._motion_enabled

    @property
    def communication_uncertain(self) -> bool:
        """Whether status replies can no longer be trusted in this session."""
        return False

    @property
    def can_release_inspection_session(self) -> bool:
        """Whether read-only access can be released after a lost status stream."""
        return False

    def release_after_manual_verification(self) -> None:
        """Release a failed session only after the operator confirms standstill."""
        raise MotorSafetyError("Manual recovery is unavailable for this motor session.")

    def require_connected(self) -> None:
        """Refuse reads or commands after disconnect."""
        if not self.is_connected:
            raise MotorError("Motor is disconnected.")

    def validate_position(self, target_mm: float) -> float:
        """Require motion permission and validate both position bounds in mm."""
        self.require_connected()
        if not self.motion_enabled or self.limits is None:
            raise MotorSafetyError("Physical motion is locked; a reviewed safety profile is required.")
        return self.limits.validate_position(target_mm)

    def move_relative(self, delta_mm: float) -> None:
        """Start a relative move in mm using fresh readback and absolute validation."""
        delta = finite(delta_mm, "Relative displacement (mm)")
        self.move_to(self.get_position() + delta)

    def get_position(self) -> float:
        """Read current mechanical position in millimetres."""
        position = self.get_status().position_mm
        if position is None:
            raise MotorSafetyError("Controller position units have not been verified as mm.")
        return position

    def is_moving(self) -> bool:
        """Read whether the controller reports motion or homing."""
        return self.get_status().is_moving

    def home(self) -> None:
        """Refuse homing until the apparatus-specific procedure is reviewed."""
        raise MotorSafetyError("Homing is not implemented: it may sweep outside the safe apparatus range.")

    @abstractmethod
    def connect(self) -> MotorInfo:
        """Open an explicit session; never auto-home or change controller settings."""

    @abstractmethod
    def close(self) -> None:
        """Stop owned motion and release resources; report uncertain stop/cleanup."""

    @abstractmethod
    def get_status(self) -> MotorStatus:
        """Read measured position and motion/error state with bounded I/O."""

    @abstractmethod
    def move_to(self, target_mm: float) -> None:
        """Start an absolute move in mm after limit and readiness validation."""

    @abstractmethod
    def stop(self) -> None:
        """Request a controlled stop; never disable the servo or reset coordinates."""

    def __enter__(self):
        """Connect for a measurement loop with deterministic cleanup."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Close on normal completion and exceptions, propagating cleanup failures."""
        self.close()
