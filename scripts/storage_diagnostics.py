"""Quiet notebook diagnostics 4C (camera batches) and 4D (synthetic format I/O).

All outputs are retained in new BENCHMARK_ONLY directories. 4C uses the reviewed
camera configuration path; 4D never constructs or connects a physical camera.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import shutil
import time

import h5py
import numpy as np

from scripts import lab_computer_acceptance as lab
from hhg_control.sequencer.scan_manager import CameraScanManager, _frame_chunk_shape


def _volume_key(path: Path) -> str:
    """Identify a Windows drive or POSIX anchor for preflight volume separation."""
    return PureWindowsPath(str(path)).drive.casefold() or path.anchor


def _destinations(cfg: lab.Settings, output_dirs: dict[str, Path] | None) -> dict[str, Path]:
    """Resolve explicit destinations, defaulting to the setup cell's output folder."""
    destinations = {str(key): Path(value).resolve() for key, value in
                    (output_dirs or {"selected": cfg.output_dir}).items()}
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Diagnostic destinations must be different folders")
    if len({_volume_key(path) for path in destinations.values()}) != len(destinations):
        raise ValueError("Use only one destination per drive so the combined space budget is checked correctly")
    return destinations


def _report(section: str, cfg: lab.Settings, destinations: dict[str, Path], operation) -> dict:
    """Save results and a progress log silently, including ordinary failures."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    folder = next(iter(destinations.values())) / f"BENCHMARK_ONLY_section_{section}_{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    report_path, log_path = folder / "report.json", folder / "execution.log"
    report = {"section": section, "utc": datetime.now(timezone.utc).isoformat(),
              "status": "FAILED", "source": cfg.source if section == "4c" else "synthetic",
              "settings": asdict(cfg), "report_file": str(report_path),
              "execution_log": str(log_path), "drives": {}, "physical_acceptance": False}
    interrupted = None
    with log_path.open("w", encoding="utf-8") as log, redirect_stdout(log):
        try:
            operation(report, folder.name)
            report["status"] = "EXECUTED"
        except BaseException as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            print(report["error"])
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = exc
        finally:
            report_path.write_text(json.dumps(report, indent=2, default=lab.json_default),
                                   encoding="utf-8")
    if interrupted is not None:
        raise interrupted
    return report


def _preflight(cfg, destinations, frames_per_drive, files_per_drive, cap_gb):
    """Check the complete retained output budget on every drive before any run."""
    if not math.isfinite(cap_gb) or not 0 < cap_gb <= 30:
        raise ValueError("Output cap must be finite, positive and at most 30 GB per destination")
    frame_bytes = (cfg.roi[2] - cfg.roi[0]) * (cfg.roi[3] - cfg.roi[1]) * 2
    raw = frames_per_drive * frame_bytes
    # JSON/VLEN metadata scales with frame count, especially for very small ROIs.
    # Reserve 4 KiB per frame plus file-level overhead before any payload write.
    reserve = frames_per_drive * 4096 + files_per_drive * 100_000_000
    if raw + reserve > cap_gb * 1e9:
        raise ValueError("Planned data plus metadata reserve exceeds the per-drive cap; reduce run sizes")
    free = {}
    for label, destination in destinations.items():
        lab.prepare(replace(cfg, output_dir=destination), frames_per_drive)
        free[label] = shutil.disk_usage(destination).free
        if free[label] < 1.5 * (raw + reserve) + 100_000_000:
            raise OSError(f"Not enough free space for all retained outputs on {label}")
    return frame_bytes, raw, free


def _measure_output(drive_report, folder: Path, cap_gb: float):
    """Measure only this diagnostic's retained files, never existing research data."""
    size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
    drive_report.update(output_bytes=size, output_GB=size / 1e9,
                        within_output_cap=size <= cap_gb * 1e9)
    if not drive_report["within_output_cap"]:
        raise OSError("Measured diagnostic output exceeded its configured cap")


def run_batch_size_trial(
    cfg: lab.Settings, *, output_dirs: dict[str, Path] | None = None,
    batch_sizes: tuple[int, ...] = (4, 16, 64), frames: int = 2000,
    repeats: int = 1, max_output_gb_per_drive: float = 8.0,
) -> dict:
    """4C: compare capture-only and production HDF5 at each batch size.

    Physical configuration, shutter/exposure and Auto Sequence are unchanged.
    Each batch records exactly the same frame count. All requested drives are
    preflighted together. Defaults retain about 3.93 GB per drive at 2560x128.
    Repetitions alternate batch order; this short trial is not sustained acceptance.
    """
    destinations = _destinations(cfg, output_dirs)

    def operation(report, folder_name):
        if (not isinstance(frames, int) or frames < 1 or
                not isinstance(repeats, int) or repeats < 1 or not batch_sizes or
                any(not isinstance(size, int) or size < 1 for size in batch_sizes)):
            raise ValueError("Frame count, repeats and batch sizes must be positive integers")
        if cfg.source == "physical" and not cfg.expected_serial:
            raise ValueError("Set the reviewed physical camera serial before 4C")
        total_frames = frames * repeats * len(batch_sizes)
        frame_bytes, raw, free = _preflight(
            cfg, destinations, total_frames, repeats * len(batch_sizes), max_output_gb_per_drive,
        )
        report.update(frames_per_run=frames, repeats=repeats, batch_sizes=list(batch_sizes),
                      planned_raw_GB_per_drive=raw / 1e9, max_output_GB_per_drive=max_output_gb_per_drive,
                      limitations=["Runs are sequential and changing system load can affect rankings.",
                                   "Short-run caching does not establish sustained storage throughput.",
                                   "Larger batches also change the time between the writer's 32-batch HDF5 flushes."])
        for label, destination in destinations.items():
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            drive = {"output_directory": str(folder), "free_GB_before": free[label] / 1e9,
                     "runs": []}
            report["drives"][label] = drive
            with lab.configured_camera(replace(cfg, output_dir=folder)) as camera:
                report.setdefault("settings_readback", {})[label] = lab.snapshot(camera)
                for repetition in range(repeats):
                    order = batch_sizes if repetition % 2 == 0 else tuple(reversed(batch_sizes))
                    for batch in order:
                        run_cfg = replace(cfg, output_dir=folder, batch_size=batch,
                                          speed_frames=frames, repeats=1)
                        capture = lab.capture_only(camera, run_cfg, frames)
                        saved = lab.saved_run(camera, run_cfg, f"BENCHMARK_ONLY_batch_{batch}", frames)
                        drive["runs"].append({"repetition": repetition + 1, "batch_size": batch,
                                              "capture_only": capture, "saved": saved,
                                              "durable_raw_MB_per_s": saved["synced_fps"] * frame_bytes / 1e6})
            drive["median_durable_fps_by_batch"] = {
                str(batch): float(np.median([run["saved"]["synced_fps"] for run in drive["runs"]
                                            if run["batch_size"] == batch]))
                for batch in batch_sizes
            }
            drive["fastest_measured_batch"] = max(drive["median_durable_fps_by_batch"],
                                                 key=drive["median_durable_fps_by_batch"].get)
            _measure_output(drive, folder, max_output_gb_per_drive)

    return _report("4c", cfg, destinations, operation)


def _blocks(bank, frames):
    """Yield bounded little-endian uint16 blocks with a unique 32-bit frame marker."""
    for start in range(0, frames, len(bank)):
        block = bank[:min(len(bank), frames - start)].copy()
        ids = np.arange(start, start + len(block), dtype=np.uint32)
        block[:, 0, 0] = ids & 65535
        block[:, 0, 1] = ids >> 16
        yield start, block


def _metadata(start, count):
    """Return identical synthetic per-frame metadata for both format controls."""
    return [{"frame_id": index, "timestamp_source": "synthetic_format_control"}
            for index in range(start, start + count)]


def _sync_paths(paths):
    """Request OS durability for each closed or explicitly flushed diagnostic file."""
    started = time.perf_counter()
    for path in paths:
        with path.open("r+b", buffering=0) as handle:
            os.fsync(handle.fileno())
    return time.perf_counter() - started


def _verify_control(path, metadata_path, kind, bank, frames, expected_digest):
    """Check every saved pixel, marker and metadata ID outside throughput timing."""
    started = time.perf_counter()
    digest = hashlib.sha256()
    if kind == "hdf5":
        with h5py.File(path, "r") as h5:
            if (h5["images"].shape != (frames, *bank.shape[1:]) or
                    h5["images"].dtype != np.dtype("<u2") or not h5.attrs["complete"] or
                    int(h5.attrs["frames_written"]) != frames or
                    h5["frame_metadata/json"].shape != (frames,)):
                raise AssertionError("HDF5 control shape, dtype or completion differs")
            for start, expected in _blocks(bank, frames):
                actual = h5["images"][start:start + len(expected)]
                if not np.array_equal(actual, expected):
                    raise AssertionError(f"HDF5 pixels differ at block {start}")
                digest.update(actual.tobytes(order="C"))
                rows = [json.loads(row) for row in h5["frame_metadata/json"][start:start + len(expected)]]
                if rows != _metadata(start, len(expected)):
                    raise AssertionError("HDF5 metadata differs")
    else:
        if path.stat().st_size != frames * bank.shape[1] * bank.shape[2] * 2:
            raise AssertionError("BIN size differs from its manifest")
        with path.open("rb") as handle, metadata_path.open(encoding="utf-8") as metadata:
            for start, expected in _blocks(bank, frames):
                data = handle.read(expected.nbytes)
                actual = np.frombuffer(data, dtype="<u2").reshape(expected.shape)
                if not np.array_equal(actual, expected):
                    raise AssertionError(f"BIN pixels differ at block {start}")
                digest.update(data)
                rows = [json.loads(metadata.readline()) for _ in range(len(expected))]
                if rows != _metadata(start, len(expected)):
                    raise AssertionError("BIN metadata differs")
            if metadata.readline():
                raise AssertionError("BIN metadata contains extra frames")
    if digest.hexdigest() != expected_digest:
        raise AssertionError("Readback pixel checksum differs from prepared source")
    return {"pixels_equal_source": True, "consecutive_frame_ids": True,
            "pixel_sha256": digest.hexdigest(), "verification_s": time.perf_counter() - started}


def _write_control(folder, kind, bank, frames, expected_digest, sync_every_bytes):
    """Write one matched format control with periodic optional and final mandatory fsync.

    HDF5 uses the production chunk shape and _write_batch implementation; BIN
    carries identical JSON metadata in a sidecar. This isolates storage I/O,
    and does not reproduce the production asynchronous capture/queue pipeline.
    """
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / f"BENCHMARK_ONLY_frames.{'h5' if kind == 'hdf5' else 'bin'}"
    metadata_path = folder / "frame_metadata.jsonl"
    paths = [path] if kind == "hdf5" else [path, metadata_path]
    partial = path.with_suffix(path.suffix + ".partial")
    periodic_sync_s, periodic_count = 0.0, 0
    frame_bytes = bank.shape[1] * bank.shape[2] * 2
    written, next_sync = 0, sync_every_bytes
    started = time.perf_counter()

    def checkpoint(flush):
        nonlocal periodic_sync_s, periodic_count, next_sync
        if sync_every_bytes and written * frame_bytes >= next_sync:
            flush()
            periodic_sync_s += _sync_paths([partial] if kind == "hdf5" else [partial, metadata_path])
            periodic_count += 1
            next_sync = written * frame_bytes + sync_every_bytes

    if kind == "hdf5":
        writer = CameraScanManager(lab.MockPcoCamera(), folder)
        with h5py.File(partial, "x") as h5:
            h5.attrs.update(schema_version="diagnostic-1.0", source="synthetic",
                            complete=False, frames_written=0, storage_compression="none")
            images = h5.create_dataset("images", shape=(frames, *bank.shape[1:]),
                                      dtype="<u2", chunks=_frame_chunk_shape(*bank.shape[1:]))
            images.attrs.update(dimension_order="[frame, y, x]", units="ADU")
            metadata = h5.create_dataset("frame_metadata/json", shape=(frames,),
                                        dtype=h5py.string_dtype("utf-8"), chunks=(min(256, frames),))
            for number, (start, block) in enumerate(_blocks(bank, frames), 1):
                # Retain the production pixel and JSON serialization operations
                # without involving its asynchronous acquisition queue.
                writer._write_batch(images, metadata, start, block, _metadata(start, len(block)))
                written += len(block)
                if number % 32 == 0:
                    h5.attrs["frames_written"] = written
                    h5.flush()
                checkpoint(h5.flush)
            h5.attrs.update(complete=True, frames_written=written)
            h5.flush()
    else:
        with partial.open("xb", buffering=0) as binary, metadata_path.open("x", encoding="utf-8", newline="\n") as metadata:
            for number, (start, block) in enumerate(_blocks(bank, frames), 1):
                data = memoryview(block).cast("B")
                while data:
                    count = binary.write(data)
                    if not count:
                        raise OSError("BIN write made no progress")
                    data = data[count:]
                metadata.writelines(json.dumps(row, sort_keys=True) + "\n" for row in _metadata(start, len(block)))
                written += len(block)
                if number % 32 == 0:
                    metadata.flush()
                checkpoint(metadata.flush)
            metadata.flush()
    partial.rename(path)
    close_s = time.perf_counter() - started
    final_sync_s = _sync_paths(paths)
    durable_s = time.perf_counter() - started
    second_sync_s = _sync_paths(paths)
    checked = _verify_control(path, metadata_path, kind, bank, frames, expected_digest)
    return {"format": kind, "file": str(path), "metadata_file": str(metadata_path) if kind == "bin" else None,
            "frames": frames, "raw_bytes": frames * frame_bytes,
            "output_bytes": sum(item.stat().st_size for item in paths),
            "write_close_s": close_s, "durable_s": durable_s,
            "write_close_raw_MB_per_s": frames * frame_bytes / 1e6 / close_s,
            "durable_raw_MB_per_s": frames * frame_bytes / 1e6 / durable_s,
            "equivalent_durable_fps": frames / durable_s,
            "final_os_fsync_s": final_sync_s, "immediate_second_fsync_s": second_sync_s,
            "periodic_fsync_s": periodic_sync_s, "periodic_fsync_count": periodic_count,
            "sync_policy": "periodic_and_final" if sync_every_bytes else "final_only", **checked}


def run_bin_hdf5_trial(
    cfg: lab.Settings, *, output_dirs: dict[str, Path] | None = None,
    target_raw_gb_per_format: float = 2.0, batch_size: int = 16,
    compare_periodic_sync: bool = False, sync_every_gb: float = 0.25,
    max_output_gb_per_drive: float = 30.0,
) -> dict:
    """4D: compare identical synthetic pixels/metadata in BIN and HDF5.

    Defaults retain about 4 GB per drive, or 8 GB with the optional periodic-sync
    pair. Uses only a bounded prepared frame bank, verifies every pixel, and
    retains all outputs. Sync, source preparation and readback are timed separately.
    No real camera, binning, sensor exposure, USB or trigger is involved.
    """
    destinations = _destinations(cfg, output_dirs)

    def operation(report, folder_name):
        if (not math.isfinite(target_raw_gb_per_format) or target_raw_gb_per_format <= 0 or
                not isinstance(batch_size, int) or batch_size < 1 or
                not math.isfinite(sync_every_gb) or sync_every_gb <= 0):
            raise ValueError("Payload GB, batch size and sync interval GB must be positive and finite")
        width, height = cfg.roi[2] - cfg.roi[0], cfg.roi[3] - cfg.roi[1]
        if width < 2 or height < 1:
            raise ValueError("Synthetic frame markers require at least two columns and one row")
        frame_bytes = width * height * 2
        frames = max(1, int(target_raw_gb_per_format * 1e9) // frame_bytes)
        bank_frames = min(batch_size, frames)
        if bank_frames * frame_bytes > 256 * 2**20:
            raise ValueError("Prepared synthetic bank exceeds 256 MiB; reduce batch size")
        policies = [0, max(1, int(sync_every_gb * 1e9))] if compare_periodic_sync else [0]
        controls = 2 * len(policies)
        synthetic_cfg = replace(cfg, source="mock", expected_serial=None)
        _, raw, free = _preflight(synthetic_cfg, destinations, frames * controls, controls, max_output_gb_per_drive)
        preparation = time.perf_counter()
        bank = np.random.default_rng(42).integers(0, 65536, (bank_frames, height, width), dtype=np.uint16).astype("<u2")
        expected = hashlib.sha256()
        for _, block in _blocks(bank, frames):
            expected.update(block.tobytes(order="C"))
        report.update(frames_per_format=frames, raw_GB_per_format=frames * frame_bytes / 1e9,
                      planned_raw_GB_per_drive=raw / 1e9, max_output_GB_per_drive=max_output_gb_per_drive,
                      batch_size=batch_size, compare_periodic_sync=compare_periodic_sync,
                      source_preparation_s=time.perf_counter() - preparation,
                      expected_pixel_sha256=expected.hexdigest(),
                      image_layout={"shape": [frames, height, width], "dtype": "uint16",
                                    "byte_order": "little-endian", "dimension_order": "[frame, y, x]"},
                      limitations=["Synthetic unpaced format control; not physical camera or production pipeline acceptance.",
                                   "Readback and source-checksum preparation are excluded from write throughput.",
                                   "Sequential runs and caching can affect results; each policy always ends with fsync.",
                                   "HDF5 uses production chunks/serialization, but no asynchronous writer queue."])
        for index, (label, destination) in enumerate(destinations.items()):
            folder = destination / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            drive = {"output_directory": str(folder), "free_GB_before": free[label] / 1e9, "runs": []}
            report["drives"][label] = drive
            order = ("hdf5", "bin") if index % 2 == 0 else ("bin", "hdf5")
            for policy in policies:
                name = "periodic" if policy else "final"
                for kind in order:
                    drive["runs"].append(_write_control(folder / f"{kind}_{name}", kind, bank,
                                                        frames, expected.hexdigest(), policy))
            drive["BIN_over_HDF5_durable_rate_by_policy"] = {
                name: next(run["durable_raw_MB_per_s"] for run in drive["runs"]
                           if run["format"] == "bin" and run["sync_policy"] == name) /
                      next(run["durable_raw_MB_per_s"] for run in drive["runs"]
                           if run["format"] == "hdf5" and run["sync_policy"] == name)
                for name in {run["sync_policy"] for run in drive["runs"]}
            }
            _measure_output(drive, folder, max_output_gb_per_drive)

    return _report("4d", cfg, destinations, operation)
