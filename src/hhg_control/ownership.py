"""Nonblocking reservations shared by instrument sequencers, without hardware I/O."""

from contextlib import contextmanager
from threading import RLock
from functools import wraps


def reserved_access(method):
    """Guard a synchronous public sequencer method with its session reservation."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.reserve():
            return method(self, *args, **kwargs)
    return guarded


class InstrumentOwnership:
    """Reserve an instrument across several operations on its owning thread."""

    def __init__(self):
        """Create a reentrant session gate; individual operations retain their locks."""
        self._gate = RLock()

    @contextmanager
    def reserve(self):
        """Reject competing owners immediately instead of queueing stale commands."""
        if not self._gate.acquire(blocking=False):
            raise RuntimeError("Instrument is reserved by another operation.")
        try:
            yield
        finally:
            self._gate.release()
