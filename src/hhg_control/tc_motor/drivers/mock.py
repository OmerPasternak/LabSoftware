"""Project-owned SMC100/LTA-HS simulator; no Newport software or hardware access."""

from collections.abc import Callable
from math import sqrt
from threading import RLock
import time

from .base import BaseMotor, MotorError, MotorInfo, MotorLimits, MotorSafetyError, MotorStatus, finite


class MockMotor(BaseMotor):
    """Simulate a single already-referenced actuator with mechanical mm units.

    The explicit envelope is synthetic and must never be reused as a physical
    apparatus profile. Motion uses a symmetric acceleration/cruise/deceleration
    model, not an assertion about real servo dynamics or positioning accuracy.
    The injectable monotonic clock permits deterministic tests without sleeping.
    """

    def __init__(
        self,
        limits: MotorLimits,
        *,
        initial_position_mm: float = 0.0,
        velocity_mm_s: float | None = None,
        acceleration_mm_s2: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Set synthetic position, mm/s speed, and mm/s² acceleration within limits."""
        if not isinstance(limits, MotorLimits):
            raise MotorSafetyError("The mock requires explicit synthetic MotorLimits.")
        super().__init__(limits)
        self._position = limits.validate_position(initial_position_mm)
        self._velocity = finite(
            limits.max_velocity_mm_s if velocity_mm_s is None else velocity_mm_s,
            "Mock velocity (mm/s)",
        )
        self._acceleration = finite(
            limits.max_acceleration_mm_s2 if acceleration_mm_s2 is None else acceleration_mm_s2,
            "Mock acceleration (mm/s²)",
        )
        if not 0 < self._velocity <= limits.max_velocity_mm_s:
            raise MotorSafetyError("Mock velocity exceeds the synthetic envelope.")
        if not 0 < self._acceleration <= limits.max_acceleration_mm_s2:
            raise MotorSafetyError("Mock acceleration exceeds the synthetic envelope.")
        self._clock = clock
        self._lock = RLock()
        self._profile: tuple[float, float, float, float, float, float] | None = None
        self._state_code = "32"

    def connect(self) -> MotorInfo:
        """Open the simulated session at its existing position; never home or zero."""
        with self._lock:
            if not self.is_connected:
                self.info = MotorInfo("MOCK SMC100CC (project emulator)", "MOCK LTA-HS", 1, True, True)
                self._is_connected = True
                self._motion_enabled = True
            assert self.info is not None
            return self.info

    def close(self) -> None:
        """Freeze simulated motion and close; retain the existing coordinate."""
        with self._lock:
            if self.is_connected:
                self.stop()
                self._motion_enabled = False
                self._is_connected = False
                self.info = None

    def _update(self) -> None:
        """Advance the synthetic mm readback from the clock without blocking."""
        if self._profile is None:
            return
        started, origin, target, accel_s, cruise_s, peak_velocity = self._profile
        elapsed = finite(self._clock(), "Mock clock (s)") - started
        if elapsed < 0:
            raise MotorError("Mock clock moved backwards.")
        duration = 2 * accel_s + cruise_s
        if elapsed >= duration:
            self._position = target
            self._profile = None
            self._state_code = "33"
            return
        direction = 1 if target > origin else -1
        if elapsed < accel_s:
            distance = 0.5 * self._acceleration * elapsed * elapsed
        elif elapsed < accel_s + cruise_s:
            distance = 0.5 * self._acceleration * accel_s * accel_s
            distance += peak_velocity * (elapsed - accel_s)
        else:
            remaining = duration - elapsed
            distance = abs(target - origin) - 0.5 * self._acceleration * remaining * remaining
        self._position = origin + direction * distance

    def get_status(self) -> MotorStatus:
        """Read synthetic mechanical mm position and READY/MOVING state."""
        with self._lock:
            self.require_connected()
            self._update()
            moving = self._profile is not None
            return MotorStatus(self._position, "MOVING" if moving else "READY", self._state_code, 0, moving, not moving)

    def move_to(self, target_mm: float) -> None:
        """Start an absolute mm move, enforcing current, target, step and ready state."""
        with self._lock:
            target = self.validate_position(target_mm)
            self._update()
            self.check_motion_guard()
            if self._profile is not None:
                raise MotorSafetyError("Mock motor is moving; stop or await completion first.")
            assert self.limits is not None
            self.limits.validate_move(self._position, target)
            distance = abs(target - self._position)
            if distance == 0:
                return
            accel_s = min(self._velocity / self._acceleration, sqrt(distance / self._acceleration))
            peak_velocity = self._acceleration * accel_s
            cruise_s = max(0.0, distance / peak_velocity - accel_s)
            started = finite(self._clock(), "Mock clock (s)")
            self.check_motion_guard()
            self._profile = (started, self._position, target, accel_s, cruise_s, peak_velocity)
            self._state_code = "28"

    def stop(self) -> None:
        """Freeze at the current simulated position, retaining servo and coordinates."""
        with self._lock:
            self.require_connected()
            self.request_stop()
            self._update()
            self._profile = None
            self._state_code = "33"
