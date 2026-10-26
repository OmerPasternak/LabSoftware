"""Standalone physical-mode dependency check; never enumerates or opens ports."""

import sys


def main() -> int:
    """Check this Python's PyVISA and load the system VISA runtime, without I/O.

    Run in its own process, not inside an instrument-owning GUI: closing its
    ResourceManager is safe because this process has no instrument sessions.
    A pass establishes backend loading only, not serial-port access or motion.
    """
    print(f"Python: {sys.executable}")
    try:
        import pyvisa
    except ImportError as exc:
        print(f"PyVISA is unavailable: {exc}")
        print('From the project root, run .venv\\Scripts\\python.exe -m pip install ".[hardware-smc100]"')
        return 1
    print(f"PyVISA: {pyvisa.__version__}")
    try:
        manager = pyvisa.ResourceManager("")
    except Exception as exc:
        print(f"The VISA runtime could not be loaded: {exc}")
        print("Install or repair the native VISA runtime (normally NI-VISA on the LabVIEW PC), then retry.")
        print("Installing the Python package alone does not install NI-VISA.")
        return 1
    try:
        print(f"VISA backend: {manager.visalib}")
        print("Backend loaded. Port access and exclusive locking are checked only when you click Connect.")
        return 0
    finally:
        manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
