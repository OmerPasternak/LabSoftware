"""Explicit ordered mechanical positions; no implicit optical calibration or homing."""

from dataclasses import asdict, dataclass
import hashlib
import json

from hhg_control.tc_motor.drivers.base import finite


@dataclass(frozen=True)
class Visit:
    """One absolute position in verified mm, optionally labelled reference/background."""

    position_mm: float
    label: str = "signal"

    def __post_init__(self):
        """Freeze a finite mm target and a nonempty descriptive label."""
        object.__setattr__(self, "position_mm", finite(self.position_mm, "Visit position (mm)"))
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Visit label must be nonempty text.")


@dataclass(frozen=True)
class ScanPlan:
    """Repeat the ordered path; times are seconds, mechanical positions mm.

    Optical delay is recorded only when zero_mm and signed fs_per_mm are
    supplied from an explicit calibration. Geometry is never inferred.
    """

    visits: tuple[Visit, ...]
    frames_per_point: int
    settle_s: float
    position_tolerance_mm: float
    repeats: int = 1
    motion_timeout_s: float = 30.0
    capture_timeout_s: float = 60.0
    storage_timeout_s: float = 120.0
    cleanup_timeout_s: float = 10.0
    zero_mm: float | None = None
    fs_per_mm: float | None = None

    def __post_init__(self):
        """Validate the complete immutable plan without instrument I/O."""
        object.__setattr__(self, "visits", tuple(self.visits))
        if not self.visits or not all(isinstance(v, Visit) for v in self.visits):
            raise ValueError("Provide an explicit nonempty list of visits.")
        for name in ("frames_per_point", "repeats"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if len(self.visits) * self.repeats > 100_000:
            raise ValueError("Plan exceeds 100,000 visits; split it into reviewed runs.")
        for name in ("position_tolerance_mm", "motion_timeout_s", "capture_timeout_s",
                     "storage_timeout_s", "cleanup_timeout_s"):
            if finite(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive.")
        if finite(self.settle_s, "Settling time (s)") < 0 or self.settle_s >= self.motion_timeout_s:
            raise ValueError("Settling must be nonnegative and below the motion timeout.")
        if (self.zero_mm is None) != (self.fs_per_mm is None):
            raise ValueError("Supply both optical zero (mm) and signed fs/mm, or neither.")
        if self.zero_mm is not None:
            finite(self.zero_mm, "Optical zero (mm)")
            if finite(self.fs_per_mm, "Optical calibration (fs/mm)") == 0:
                raise ValueError("Optical calibration cannot be zero.")

    def points(self):
        """Yield (global index, repeat index, visit index, visit) in requested order."""
        for repeat in range(self.repeats):
            for visit_index, visit in enumerate(self.visits):
                yield repeat * len(self.visits) + visit_index, repeat, visit_index, visit

    def to_dict(self):
        """Return JSON-compatible plan metadata with explicit units."""
        return {"version": 1, "position_unit": "mm", **asdict(self)}

    @property
    def plan_id(self):
        """Hash the full plan so recovery cannot silently change its meaning."""
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()
