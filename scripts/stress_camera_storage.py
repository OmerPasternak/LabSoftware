"""Stress camera scan storage with synthetic uint16 frames and no hardware.

All rates are throughput measurements, not camera or trigger timing claims.
The replay camera keeps one batch in RAM and reuses it throughout each case.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil
import sys
import time
from typing import Any

import h5py
import numpy as np

# Always benchmark this checkout, even if another hhg_control is installed in
# the interpreter used to launch the script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hhg_control.drivers.base_camera import BaseCamera
from hhg_control.sequencer.scan_manager import CameraScanManager


CASES = ("discard", "application", "hdf5_gzip", "hdf5_plain", "raw_single", "raw_per_frame")


class ReplayCamera(BaseCamera):
    """Replay one prepared frame batch without exposure delays or real hardware.

    Frames have uint16 ADU values and configured sensor dimensions in pixels.
    This removes image synthesis from the timed section but cannot simulate SDK
    transfer, camera buffering, or hardware triggers.
    """

    def __init__(self, width: int, height: int, batch_size: int, pattern: str) -> None:
        super().__init__()
        self.width, self.height = width, height
        rng = np.random.default_rng(42)
        if pattern == "noise":
            self.bank = rng.integers(0, 65536, (batch_size, height, width), dtype=np.uint16)
        else:
            # Lower entropy resembles a dark pedestal; compression cost and
            # output size can differ substantially from the noise case.
            self.bank = rng.integers(96, 105, (batch_size, height, width), dtype=np.uint16)
        self.acquire_s = 0.0
        self.acquire_calls = 0

    def connect(self) -> None:
        """Mark the synthetic source connected; no hardware is contacted."""
        self._is_connected = True

    def close(self) -> None:
        """Disconnect the synthetic source."""
        self._is_connected = False

    def set_exposure_time(self, exposure_s: float) -> None:
        """Set simulated exposure in seconds within base-camera limits."""
        self.validate_exposure_time(exposure_s)
        self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        """Return simulated exposure in seconds."""
        return self._exposure_time_s

    def get_sensor_info(self) -> dict[str, Any]:
        """Return synthetic sensor identity and dimensions in pixels."""
        return {
            "model": "synthetic-storage-replay",
            "serial_number": "NO-HARDWARE",
            "width": self.width,
            "height": self.height,
            "bit_depth": 16,
        }

    def acquire_frames(self, num_frames: int) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Return up to one bank of synthetic frames and minimal frame metadata."""
        started = time.perf_counter()
        if not self.is_connected or not 1 <= num_frames <= len(self.bank):
            raise ValueError("Replay camera is disconnected or batch exceeds its frame bank.")
        frames = self.bank[:num_frames]
        metadata = [{"frame_id": i, "timestamp_source": "synthetic_replay"}
                    for i in range(num_frames)]
        self.acquire_s += time.perf_counter() - started
        self.acquire_calls += 1
        return frames, metadata


def run_case(case: str, camera: ReplayCamera, run_dir: Path, frames: int,
             batch_size: int) -> dict[str, Any]:
    """Run one storage case and return timing and byte counts.

    The application case calls the production scan writer. Other cases isolate
    format and file-creation costs using the same frames and batch boundaries.
    Files are closed before timing ends. OS write caching is still possible.
    """
    camera.acquire_s = 0.0
    camera.acquire_calls = 0
    path = run_dir / f"{case}.dat"
    started = time.perf_counter()
    cpu_started = time.process_time()
    created: list[Path] = []
    if case == "application":
        manager = CameraScanManager(camera, run_dir)
        path, _ = manager.acquire_and_save_step(
            "storage_stress", 0, "synthetic_step", 0.0, frames,
            run_id="benchmark", batch_size=batch_size,
        )
        created.append(path)
    elif case == "discard":
        count = 0
        for images, _ in camera.iter_frames(frames, batch_size=batch_size):
            count += len(images)
        assert count == frames
    elif case in ("hdf5_plain", "hdf5_gzip"):
        path = path.with_suffix(".h5")
        with h5py.File(path, "w") as h5f:
            filters = {"compression": "gzip", "compression_opts": 1, "shuffle": True} \
                if case == "hdf5_gzip" else {}
            dset = h5f.create_dataset(
                "images", shape=(frames, camera.height, camera.width),
                dtype="uint16", chunks=(1, min(512, camera.height), min(512, camera.width)),
                **filters,
            )
            offset = 0
            for images, _ in camera.iter_frames(frames, batch_size=batch_size):
                dset[offset:offset + len(images)] = images
                offset += len(images)
        assert offset == frames
        created.append(path)
    elif case == "raw_single":
        with path.open("wb") as handle:
            for images, _ in camera.iter_frames(frames, batch_size=batch_size):
                handle.write(images.tobytes(order="C"))
        created.append(path)
    elif case == "raw_per_frame":
        index = 0
        for images, _ in camera.iter_frames(frames, batch_size=batch_size):
            for image in images:
                frame_path = run_dir / f"raw_frame_{index:08d}.bin"
                with frame_path.open("wb") as handle:
                    handle.write(image.tobytes(order="C"))
                created.append(frame_path)
                index += 1
        assert index == frames
    else:
        raise ValueError(f"Unknown case: {case}")

    elapsed_s = time.perf_counter() - started
    cpu_s = time.process_time() - cpu_started
    raw_bytes = frames * camera.bank.shape[1] * camera.bank.shape[2] * 2
    output_bytes = sum(item.stat().st_size for item in created)
    if case == "application":
        with h5py.File(path, "r") as h5f:
            assert h5f["images"].shape == (frames, camera.height, camera.width)
            assert bool(h5f.attrs["complete"]) and h5f.attrs["frames_written"] == frames
            assert np.array_equal(h5f["images"][0], camera.bank[0])
            assert np.array_equal(h5f["images"][-1], camera.bank[(frames - 1) % batch_size])
    elif case in ("hdf5_plain", "hdf5_gzip"):
        with h5py.File(path, "r") as h5f:
            assert np.array_equal(h5f["images"][0], camera.bank[0])
            assert np.array_equal(h5f["images"][-1], camera.bank[(frames - 1) % batch_size])
    elif case == "raw_single":
        assert output_bytes == raw_bytes
    elif case == "raw_per_frame":
        assert len(created) == frames and output_bytes == raw_bytes
    result = {
        "case": case,
        "frames": frames,
        "raw_bytes": raw_bytes,
        "output_bytes": output_bytes,
        "file_count": len(created),
        "elapsed_s": elapsed_s,
        "cpu_s": cpu_s,
        "acquire_s": camera.acquire_s,
        "other_s": max(0.0, elapsed_s - camera.acquire_s),
        "acquire_calls": camera.acquire_calls,
        "raw_MB_per_s": raw_bytes / 1_000_000 / elapsed_s,
    }
    # Delete only files created by this case. The user-supplied directory is
    # never recursively removed and no pre-existing files are touched.
    for item in created:
        item.unlink()
    return result


def main() -> None:
    """Parse stress settings, run cases on one destination, and save JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory on the drive intended for camera scans")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=2160)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--pattern", choices=("noise", "low_entropy"), default="noise")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    if min(args.frames, args.width, args.height, args.batch_size) < 1:
        parser.error("frames, width, height, and batch-size must all be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = args.frames * args.width * args.height * 2
    bank_bytes = min(args.batch_size, args.frames) * args.width * args.height * 2
    # Uncompressed HDF5 can allocate padded edge chunks, exceeding raw bytes.
    # Leave additional room for filesystem metadata and the result file.
    free_bytes = shutil.disk_usage(args.output_dir).free
    if free_bytes < int(raw_bytes * 1.5) + 100_000_000:
        parser.error("Less than 1.5x raw output size plus 100 MB is free on the target drive")
    print(f"Synthetic batch RAM: {bank_bytes / 2**20:.1f} MiB; "
          f"raw output per case: {raw_bytes / 2**30:.2f} GiB")
    camera = ReplayCamera(args.width, args.height, min(args.batch_size, args.frames), args.pattern)
    camera.connect()
    # tempfile.mkdtemp can inherit restrictive ACLs in some Windows execution
    # contexts. Use ordinary directory creation within the chosen destination.
    run_dir = args.output_dir / f"camera_storage_stress_{secrets.token_hex(6)}"
    run_dir.mkdir()
    results: list[dict[str, Any]] = []
    try:
        for case in args.cases:
            result = run_case(case, camera, run_dir, args.frames, args.batch_size)
            results.append(result)
            print(f"{case:14} {result['elapsed_s']:8.2f} s  "
                  f"{result['raw_MB_per_s']:9.1f} MB/s raw  "
                  f"{result['file_count']:6} files  "
                  f"{result['output_bytes'] / 1_000_000:9.1f} MB stored")
        report = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(args.output_dir.resolve()),
            "width": args.width, "height": args.height,
            "frames": args.frames, "batch_size": args.batch_size,
            "pattern": args.pattern,
            "note": "Synthetic replay only. File close is timed, OS cache may defer physical writes.",
            "results": results,
        }
        report_path = args.output_dir / f"camera_storage_stress_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report: {report_path}")
    finally:
        camera.close()
        # rmdir removes only an empty directory. Keep incomplete files after
        # an exception and do not mask the original error with cleanup errors.
        try:
            run_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
