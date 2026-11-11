"""Data-only ownership of returned camera payloads, including failed captures."""

from collections import deque
from dataclasses import dataclass, field
import copy
from typing import Any

import numpy as np


@dataclass
class CameraPayload:
    """An unchanged returned array and a complete metadata snapshot.

    Context records original recording ordinals, never replacement frame IDs or
    inferred setpoints. A malformed payload is evidence, not a repaired image.
    """

    images: Any
    metadata: Any
    context: dict = field(default_factory=dict)

    @classmethod
    def snapshot(cls, images, metadata, *, owned=False, **context):
        """Take ownership before another driver read can mutate the payload."""
        pixels = None if images is None else np.array(images, copy=not owned, order="K")
        return cls(pixels, copy.deepcopy(metadata), context)


class PayloadLedger:
    """Retain bounded upstream buffers until a receiving owner accepts them.

    This performs no I/O and is used identically by physical and mock drivers.
    Failed persistence leaves the current and all later entries owned here.
    """

    def __init__(self):
        self._pending = deque()

    def __bool__(self):
        return bool(self._pending)

    @property
    def pending(self):
        """Expose retained payloads for diagnostics/recovery without releasing them."""
        return tuple(self._pending)

    def retain(self, payload):
        """Accept an already owned payload without another pixel copy."""
        self._pending.append(payload)

    def drain_to(self, receive):
        """Release each payload only after its next owner accepted it successfully."""
        while self._pending:
            receive(self._pending[0])
            self._pending.popleft()


def combine_errors(original, cleanup, stage):
    """Keep both errors on Python 3.10 without masking the acquisition failure."""
    if original is None:
        return cleanup
    error = RuntimeError(f"{original}; {stage}: {cleanup}")
    error.acquisition_error, error.cleanup_error = original, cleanup
    error.__cause__ = original
    return error
