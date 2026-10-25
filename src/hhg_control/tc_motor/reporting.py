"""Local diagnostic report files; no instrument imports or hardware access."""

import json
import os
from pathlib import Path
import tempfile


def save_setup_report(report: dict, path: str | Path) -> None:
    """Save UTF-8 JSON atomically, preserving an existing file on write failure.

    Numeric units/provenance are supplied by the sequencer; no calibration or
    safety confirmation is inferred here. The parent directory must exist.
    """
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
