"""4G: bounded-disk rolling stress test of the unchanged 4F acquisition path.

Only files created and registered inside this run's new BENCHMARK_ONLY folder
may be recycled, and only after sync plus full metadata/pixel verification.
No camera or filesystem work occurs at import.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

from hhg_control.safe_io import filesystem_path


class _RollingFiles:
    """Reserve byte-bounded file slots and recycle verified benchmark files only."""

    def __init__(self, folder, budget_bytes, slot_bytes):
        self.folder = Path(folder)
        if not self.folder.name.startswith("BENCHMARK_ONLY_section_4g_"):
            raise ValueError("Rolling cleanup requires a new section_4g benchmark folder")
        self.root = filesystem_path(self.folder).resolve()
        self.capacity = int(budget_bytes // slot_bytes)
        if self.capacity < 2:
            raise ValueError("Rolling disk budget must fit at least two reserved file slots")
        self.budget_bytes, self.slot_bytes = budget_bytes, slot_bytes
        self.live = {}
        self.recycled = set()
        self.deleted_bytes = self.written_bytes = self.peak_bytes = 0
        self.peak_reserved_bytes = 0
        self.slot_wait_s = self.recycle_s = 0.0
        self.journal = self.root / "rolling_verification.jsonl"

    def reserve(self, index):
        """Reserve one file's full pixel/metadata allowance before its writer starts."""
        if index in self.live or index in self.recycled:
            raise ValueError("Rolling file index was reused")
        if len(self.live) >= self.capacity:
            return False
        self.live[index] = None
        self.peak_reserved_bytes = max(self.peak_reserved_bytes, len(self.live) * self.slot_bytes)
        return True

    def register(self, index, path, manifest):
        """Register exact files created by this run; retain them until verified."""
        if index not in self.live or self.live[index] is not None:
            raise ValueError("Rolling file has no unused reservation")
        paths = self._checked_paths(path, manifest)
        size = sum(p.stat().st_size for p in paths)
        if size > self.slot_bytes:
            raise OSError("Rolling file exceeded its reserved pixel/metadata slot")
        self.live[index] = paths
        self.written_bytes += size
        self.measure()

    def _checked_paths(self, path, manifest):
        """Validate both direct children before any deletion; reject links/escapes."""
        path, manifest = filesystem_path(path), filesystem_path(manifest)
        if (path.suffix != ".h5" or not path.name.startswith("BENCHMARK_ONLY_4g_") or
                manifest != path.with_suffix(".pixels.sha256.json")):
            raise ValueError("Rolling cleanup target is not a registered 4G data/manifest pair")
        for item in (path, manifest):
            if item.is_symlink() or item.resolve().parent != self.root or not item.is_file():
                raise ValueError("Rolling cleanup target escapes its run folder or is not a regular file")
        return path, manifest

    def recycle(self, checked, row):
        """Journal verification, then unlink this run's exact verified file pair."""
        index = checked["index"]
        if index in self.recycled:
            return
        if (not checked.get("pixels_equal_source") or not checked.get("consecutive_ids") or
                not checked.get("complete") or "synced_at" not in checked or
                checked.get("frames_verified") != row["frames"]):
            raise AssertionError("Rolling recycling requires sync and full successful verification")
        registered = self.live.get(index)
        if registered is None:
            raise ValueError("Rolling cleanup target was never registered")
        paths = self._checked_paths(*registered)
        began = time.perf_counter()
        record = {"index": index, "file": paths[0].name, "frames": row["frames"],
                  "first_frame_id": row["first_frame_id"], "last_frame_id": row["last_frame_id"],
                  "pixels_equal_source": True, "synced_at": checked["synced_at"],
                  "verified_at": checked["verified_at"],
                  "manifest_sha256": hashlib.sha256(paths[1].read_bytes()).hexdigest()}
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # No enumeration, recursive deletion, user-supplied glob or directory removal.
        for item in paths:
            size = item.stat().st_size
            item.unlink()
            self.deleted_bytes += size
        self.live.pop(index)
        self.recycled.add(index)
        row.update(recycled=True, recycled_at=time.perf_counter())
        self.recycle_s += time.perf_counter() - began
        self.measure()

    def measure(self):
        """Measure live data, partials and the retained verification journal."""
        size = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
        self.peak_bytes = max(self.peak_bytes, size)
        if size > self.budget_bytes:
            raise OSError("Rolling benchmark exceeded its live-disk budget")
        return size

    def summary(self):
        """Separate cumulative writes from retained disk space and recycling costs."""
        retained = self.measure()
        return {"live_disk_budget_GB": self.budget_bytes / 1e9, "file_slots": self.capacity,
                "peak_live_GB": self.peak_bytes / 1e9, "retained_GB": retained / 1e9,
                "peak_reserved_live_GB": self.peak_reserved_bytes / 1e9,
                "peak_measurement": "Live bytes sampled; reserved slots include in-flight pixels and metadata allowance",
                "cumulative_file_bytes_written": self.written_bytes,
                "cumulative_file_GB_written": self.written_bytes / 1e9,
                "recycled_files": len(self.recycled), "recycled_GB": self.deleted_bytes / 1e9,
                "live_file_slots": len(self.live), "disk_slot_wait_s": self.slot_wait_s,
                "recycling_and_journal_s": self.recycle_s, "verification_journal": str(self.folder / self.journal.name)}


def run_rolling_pipeline_trial(cfg, *, output_dirs=None, window=None, duration_s=600.0,
                               disk_budget_gb=12.0, file_raw_gb=2.0,
                               verifier_timeout_s=120.0, pixel_read_mib=64,
                               frames=None, frames_per_file=None):
    """Run 4G for at least duration_s of capture, ending at a file boundary.

    Seconds and decimal GB are explicit. Defaults recycle verified 2-GB files
    within 12 GB live storage for ten minutes. Cumulative writes can be much
    larger than the live budget. The camera is not paced for recycling: RAM
    growth, slot waits, FIFO/ID errors and verification failures remain visible.
    Defaults preserve batch size, hashing, preview copies and all 4F checks.
    Tiny frame overrides are mock-only so offline tests never write GB payloads.
    """
    from scripts.complete_pipeline_trial import _run_pipeline_trial
    if not math.isfinite(duration_s) or not 0 < duration_s <= 7200:
        raise ValueError("Rolling capture duration must be positive and at most 7200 seconds")
    if not math.isfinite(disk_budget_gb) or not 0 < disk_budget_gb <= 30:
        raise ValueError("Rolling live-disk budget must be positive and at most 30 GB")
    if frames is not None and cfg.source != "mock":
        raise ValueError("4G frame overrides are mock-only; physical runs use the duration")
    return _run_pipeline_trial(cfg, output_dirs=output_dirs, window=window,
        stage_output_gb=disk_budget_gb, file_raw_gb=file_raw_gb,
        verifier_timeout_s=verifier_timeout_s, pixel_read_mib=pixel_read_mib,
        frames=frames, frames_per_file=frames_per_file,
        rolling_duration_s=duration_s)
