"""Standalone physical-mode dependency check; never enumerates or opens ports."""

import sys


def main(transport="visa") -> int:
    """Check this Python's PyVISA and load the system VISA runtime, without I/O.

    Run in its own process, not inside an instrument-owning GUI: closing its
    ResourceManager is safe because this process has no instrument sessions.
    A pass establishes backend loading only, not serial-port access or motion.
    """
    print(f"Python: {sys.executable}")
    if transport in ("serial", "both"):
        try:
            import serial
            print(f"pySerial: {serial.VERSION}; no port opened.")
        except ImportError as exc:
            print(f"pySerial is unavailable: {exc}. Run setup_physical_mode.vbs.")
            return 1
        if transport == "serial":
            return 0
    try:
        import pyvisa
    except ImportError as exc:
        print(f"PyVISA is unavailable: {exc}")
        print('From the project root, run .venv\\Scripts\\python.exe -m pip install ".[hardware-smc100]"')
        return 0 if transport == "both" else 1
    print(f"PyVISA: {pyvisa.__version__}")
    try:
        manager = pyvisa.ResourceManager("")
    except Exception as exc:
        print(f"The VISA runtime could not be loaded: {exc}")
        print("Install or repair the native VISA runtime (normally NI-VISA on the LabVIEW PC), then retry.")
        print("Installing the Python package alone does not install NI-VISA.")
        if transport == "both":
            print("Direct serial is available; select it explicitly in the GUI. VISA is unavailable.")
            return 0
        return 1
    try:
        print(f"VISA backend: {manager.visalib}")
        print("Backend loaded. Port access and exclusive locking are checked only when you click Connect.")
        return 0
    finally:
        manager.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("visa", "serial", "both"), default="visa")
    raise SystemExit(main(parser.parse_args().transport))
