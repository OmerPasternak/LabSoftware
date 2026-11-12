"""A bounded, optional display handoff that never waits for the GUI."""

from threading import Lock


class MeasurementPreview:
    """Offer an owned raw frame by reference; copy only on the display thread.

    There is at most one pending frame. SDK retrieval never copies pixels,
    renders, waits for acknowledgement, or calls user/Qt code here. Busy or
    contended display offers are skipped; acquisition frames are never skipped.
    """

    def __init__(self):
        self._lock = Lock()
        self._pending = None

    def offer(self, images, metadata):
        """Retain the newest frame of an owned batch if the slot is available."""
        if self._pending is not None or not self._lock.acquire(blocking=False):
            return
        try:
            if self._pending is None:
                self._pending = (images[-1], metadata[-1])
        finally:
            self._lock.release()

    def take(self):
        """Detach display pixels/metadata while keeping new offers suppressed."""
        import copy
        with self._lock:
            pending = self._pending
            if pending is None:
                return None
            try:
                return pending[0].copy(), copy.deepcopy(pending[1])
            finally:
                self._pending = None
