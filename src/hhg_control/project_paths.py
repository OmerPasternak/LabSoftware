"""Find shared checkout resources without absolute paths or package-depth assumptions."""

from pathlib import Path


def project_root() -> Path:
    """Locate this checkout's root from installed source, including Windows junctions."""
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/hhg_control").is_dir():
            return candidate
    raise RuntimeError("Shared scripts/docs require the LabSoftware source checkout.")
