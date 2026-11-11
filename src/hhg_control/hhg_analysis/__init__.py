"""Source-checkout bridge to the user-facing ``src/HHG analysis`` folder.

Wheels map this package directly to that folder via setuptools package-dir.
No camera drivers or Qt widgets are imported until a GUI is requested.
"""

from pathlib import Path

_source = Path(__file__).resolve().parents[2] / "HHG analysis"
if _source.is_dir():
    __path__.append(str(_source))


def __getattr__(name):
    if name in {"FundamentalAnalysisWindow", "main"}:
        from .window import FundamentalAnalysisWindow, main
        return {"FundamentalAnalysisWindow": FundamentalAnalysisWindow, "main": main}[name]
    raise AttributeError(name)
