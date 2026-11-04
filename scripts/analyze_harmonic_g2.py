"""Compatibility launcher for the self-contained HHG analysis tool."""

from pathlib import Path
import sys

# Prefer this checkout even if its old editable install has a stale package map.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hhg_control.hhg_analysis as _package

_resource = next(Path(folder) / "analyze_harmonic_g2.py" for folder in _package.__path__
                 if (Path(folder) / "analyze_harmonic_g2.py").is_file())
exec(compile(_resource.read_text(encoding="utf-8"), str(_resource), "exec"), globals())
