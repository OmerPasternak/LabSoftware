"""Immutable identity and deadlines for one isolated actuator measurement."""

from dataclasses import dataclass, field
from collections.abc import Callable
import json
import math


@dataclass(frozen=True)
class RecordingBoundary:
    """Bind a finite recorder session to JSON context frozen before its first exposure.

    Durations are positive seconds. A timeout cancels retrieval through its
    owner and drains returned frames. Uncertain cleanup prohibits another move.
    """

    context_json: str
    capture_timeout_s: float
    storage_timeout_s: float
    cleanup_timeout_s: float = 10.0
    health_check: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        """Require an identified recording and finite positive deadline durations."""
        context = json.loads(self.context_json)
        if not isinstance(context, dict) or not context.get("recording_id"):
            raise ValueError("Recording context requires a recording_id.")
        for name in ("capture_timeout_s", "storage_timeout_s", "cleanup_timeout_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite positive seconds.")

    @classmethod
    def create(cls, context, *, capture_timeout_s, storage_timeout_s, cleanup_timeout_s=10.0, health_check=None):
        """Snapshot plain metadata; later edits to GUI/run state cannot relabel frames."""
        return cls(json.dumps(context, sort_keys=True, allow_nan=False),
                   capture_timeout_s, storage_timeout_s, cleanup_timeout_s, health_check)

    @property
    def context(self):
        """Decode a fresh copy of immutable recording metadata."""
        return json.loads(self.context_json)
