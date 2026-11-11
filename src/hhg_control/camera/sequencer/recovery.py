"""Lossless, explicitly incomplete evidence for stopped/failed acquisitions."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import numpy as np

from ..payload import PayloadLedger
from ...safe_io import filesystem_path


class RecoveryJournal:
    """Persist only unsaved/uncertain payloads after the SDK reader has stopped.

    Each group preserves its original array dtype/shape and JSON metadata.
    The file never becomes a completed acquisition, has no /images dataset,
    and cannot be included by ordinary *.h5 scan discovery. Individual records
    may overlap a partial HDF5 write; context explicitly identifies that fact.
    """

    def __init__(self, origin, reason, context=None):
        self.origin, self.reason = Path(origin), str(reason)
        self.context = context or {}
        self.path = self.origin.with_suffix(".recovery.hdf5.partial")
        if filesystem_path(self.path).exists():
            self.path = self.path.with_name(f"{self.origin.stem[:128]}_{uuid4().hex}.recovery.hdf5.partial")
        self.payloads = PayloadLedger()
        self.records_saved = 0
        self.frames_saved = 0
        self.next_record = 0
        self.created = False

    def _write_payload(self, payload):
        """Acknowledge a record only after HDF5 close and host fsync succeed."""
        import h5py
        from .scan_manager import _json_safe
        metadata = json.dumps(payload.metadata, default=_json_safe, sort_keys=True)
        context = json.dumps(payload.context, default=_json_safe, sort_keys=True)
        with h5py.File(filesystem_path(self.path), "a" if self.created else "x") as h5:
            self.created = True
            h5.attrs.update(schema_version="camera-recovery-1.0", complete=False,
                            origin_file=str(self.origin), reason=self.reason,
                            step_attribution="Unassigned evidence; never an inferred later setpoint",
                            acquisition_context_json=json.dumps(self.context, default=_json_safe, sort_keys=True))
            name = f"payload_{self.next_record:08d}"
            self.next_record += 1  # A failed attempt is retained, never replaced.
            group = h5.create_group(name)
            group.attrs.update(record_complete=False, context_json=context)
            if payload.images is not None:
                if payload.images.dtype.hasobject:
                    raise TypeError("Object-valued SDK payload cannot be stored safely; retained in RAM.")
                group.create_dataset("pixels", data=payload.images)
                pixels = np.ascontiguousarray(payload.images)
                group.attrs["pixels_sha256"] = hashlib.sha256(pixels.tobytes()).hexdigest()
            group.create_dataset("metadata_json", data=metadata, dtype=h5py.string_dtype("utf-8"))
            group.attrs["metadata_sha256"] = hashlib.sha256(metadata.encode("utf-8")).hexdigest()
            group.attrs["record_complete"] = True
            h5.flush()
        with filesystem_path(self.path).open("r+b", buffering=0) as handle:
            os.fsync(handle.fileno())
        self.records_saved += 1
        self.frames_saved += payload.context.get("frames", 0)

    def persist(self):
        """Retain unwritten payloads on any recovery error; allow an explicit retry."""
        self.payloads.drain_to(self._write_payload)

    def summary(self):
        """Describe preserved evidence separately from normal acquisition progress."""
        return {"path": str(self.path), "complete": False,
                "records_saved": self.records_saved, "frames_preserved": self.frames_saved, "pending_payloads": len(self.payloads.pending),
                "reason": self.reason, "step_attribution": "unassigned"}
