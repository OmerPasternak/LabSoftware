"""Remember a verified camera identity in a local data file, without SDK access."""

import json
from pathlib import Path


def load_camera_serial(path: Path) -> str | None:
    """Read a saved numeric serial; missing or invalid preferences require selection."""
    try:
        serial = json.loads(path.read_text(encoding="utf-8"))["serial"]
        return str(int(serial)) if isinstance(serial, str) and serial.isdigit() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_camera_serial(path: Path, serial: str) -> None:
    """Save only the verified numeric identity; never modify experimental datasets."""
    if not isinstance(serial, str) or not serial.isdigit():
        raise ValueError("Camera serial must be numeric")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"serial": str(int(serial))}) + "\n", encoding="utf-8")
