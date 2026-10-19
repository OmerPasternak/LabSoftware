"""File creation and resource checks that never overwrite existing data."""

from pathlib import Path
import ctypes
import os
import shutil


def filesystem_path(path: Path) -> Path:
    """Return a path for filesystem I/O, including long Windows paths.

    Windows extended paths bypass MAX_PATH without changing the machine's
    registry. Normalize relative components before adding the prefix, and
    support UNC shares and paths that already have an extended prefix.
    Keep ordinary paths in reports so users can recognize their filenames.
    """
    path = Path(path)
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def available_physical_memory_bytes() -> int:
    """Return currently available physical RAM in bytes.

    Uses the Windows memory-status API on the lab PC and ``sysconf`` on
    POSIX test/development hosts. Failure is explicit because callers use the
    value to prevent large image buffers from exhausting system memory.
    """
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total_phys", "avail_phys", "total_page", "avail_page",
                           "total_virtual", "avail_virtual", "avail_extended")]]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Could not verify available physical memory.")
        return int(status.avail_phys)
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError) as exc:
        raise OSError("Could not verify available physical memory.") from exc


def unique_output(path: Path) -> Path:
    """Suggest a numbered filename when an output already exists."""
    path = Path(path)
    candidate, index = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate


def check_disk_space(directory: Path, required_bytes: int = 0) -> None:
    """Reserve 100 MB beyond planned bytes; raise before filling the drive."""
    if shutil.disk_usage(filesystem_path(directory)).free < required_bytes + 100_000_000:
        raise OSError("Insufficient free disk space; acquisition stopped and partial data retained.")


def check_stack_memory(num_frames: int, height: int, width: int) -> None:
    """Check available RAM before a whole-stack allocation (uint16, two copies)."""
    available = available_physical_memory_bytes()
    needed = num_frames * height * width * 4
    if needed > available // 2:
        raise MemoryError("Image stack exceeds half the available RAM; use iter_frames for bounded streaming.")


def benchmark_directory(parent: Path, label: str) -> Path:
    """Create a unique folder clearly marking retained synthetic benchmark data."""
    from uuid import uuid4
    directory = Path(parent) / "benchmark_runs" / f"BENCHMARK_ONLY_{label}_{uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "BENCHMARK_ONLY_README.txt").write_text(
        "Synthetic benchmark data, not experimental measurements.\n"
        "Files are retained. You may manually delete this entire run folder when finished.\n",
        encoding="utf-8",
    )
    return directory
