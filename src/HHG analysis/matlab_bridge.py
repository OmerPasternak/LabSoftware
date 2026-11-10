"""Locate and start the offline MATLAB companion without a shell or hardware."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def matlab_candidates() -> list[Path]:
    """Find installed MATLAB launchers, preferring the newest local release."""
    candidates = list((Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MATLAB").glob("R*/bin/matlab.exe"))
    candidates.sort(reverse=True)
    executable = shutil.which("matlab")
    if executable:
        candidates.append(Path(executable))
    return list(dict.fromkeys(p.resolve() for p in candidates if p.is_file()))


def companion_directory() -> Path:
    """Return packaged MATLAB resources, independent of the working directory."""
    return Path(__file__).resolve().parent / "matlab"


def matlab_arguments(log_path: Path, input_path: Path | None = None, action: str | None = None) -> list[str]:
    """Build literal MATLAB arguments; only a quoted resource path enters code."""
    folder = str(companion_directory()).replace("'", "''")
    if action not in {None, "pearson", "statistical", "tcs"}:
        raise ValueError("Unsupported startup workflow.")
    literal = "" if input_path is None else str(input_path.resolve()).replace("'", "''")
    command = f"addpath('{folder}'); hhg_analysis_app('{literal}','{action or 'statistical'}')"
    return ["-logfile", str(log_path), "-batch", command]
