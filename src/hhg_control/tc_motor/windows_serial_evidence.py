"""Windows COM evidence collected by the standalone query-only diagnostic."""

import ctypes
from importlib.metadata import PackageNotFoundError, version
import struct
import sys

from .drivers.base import MotorError
from .drivers.direct_serial import DirectSerialTransport


COMM_ERRORS = {1: "rx_overflow", 2: "overrun", 4: "parity", 8: "framing", 16: "break"}
DCB_FIELDS = ("BaudRate", "ByteSize", "Parity", "StopBits", "fInX", "fOutX",
              "fOutxCtsFlow", "fOutxDsrFlow", "fDtrControl", "fRtsControl",
              "fNull", "fErrorChar", "fAbortOnError")


def host_evidence(port_number: int) -> dict:
    """Identify the selected port's Windows driver without opening a port."""
    packages = {}
    for package in ("pyvisa", "pyserial"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    evidence = {"process_bits": struct.calcsize("P") * 8, "packages": packages}
    if sys.platform != "win32":
        return {**evidence, "port_driver_error": "Windows driver inventory is unavailable."}
    name = f"COM{int(port_number)}"
    try:
        from serial.tools.list_ports import comports

        port = next((item for item in comports() if item.device.upper() == name), None)
        if port is None:
            evidence["port_driver_error"] = "Selected COM port is absent from Windows inventory."
            return evidence
        evidence["port_device"] = {field: getattr(port, field, None)
                                   for field in ("device", "description", "vid", "pid", "manufacturer")}
        evidence["port_driver"] = _usb_driver(name, port.vid, port.pid)
        if evidence["port_driver"] is None:
            evidence["port_driver_error"] = "No matching USB driver registry entry for the selected port."
    except (ImportError, OSError, ValueError) as exc:
        evidence["port_driver_error"] = f"{type(exc).__name__}: {exc}"
    return evidence


def _usb_driver(name: str, vid: int | None, pid: int | None) -> dict | None:
    """Read driver metadata only for an exact VID/PID and COM port match."""
    if vid is None or pid is None:
        return None
    import winreg

    root = winreg.HKEY_LOCAL_MACHINE
    prefix = rf"SYSTEM\CurrentControlSet\Enum\USB\VID_{vid:04X}&PID_{pid:04X}"
    try:
        with winreg.OpenKey(root, prefix) as devices:
            for index in range(winreg.QueryInfoKey(devices)[0]):
                instance = winreg.EnumKey(devices, index)
                with winreg.OpenKey(devices, instance) as device:
                    try:
                        with winreg.OpenKey(device, "Device Parameters") as params:
                            if winreg.QueryValueEx(params, "PortName")[0].upper() != name:
                                continue
                        driver = winreg.QueryValueEx(device, "Driver")[0]
                    except FileNotFoundError:
                        continue
                    with winreg.OpenKey(root, "SYSTEM\\CurrentControlSet\\Control\\Class\\" + driver) as key:
                        details = {}
                        for field in ("DriverVersion", "DriverDate", "ProviderName", "InfPath"):
                            try:
                                details[field] = winreg.QueryValueEx(key, field)[0]
                            except FileNotFoundError:
                                pass
                        return details or None
    except FileNotFoundError:
        pass
    return None


class WindowsEvidenceSerial(DirectSerialTransport):
    """Sample Windows receive errors on the same exclusive handle as pySerial."""

    _win32 = None

    def open(self) -> None:
        """Open direct COM and record the actual Windows port settings."""
        super().open()
        try:
            from serial import win32

            # pySerial exposes no public Windows handle. Keep this dependency
            # in the diagnostic only, and report if it is unavailable.
            if getattr(self._session, "_port_handle", None) is None:
                raise AttributeError("pySerial did not expose the Windows COM handle.")
            self._win32 = win32
        except (ImportError, AttributeError) as exc:
            self._win32 = None
            self.session_details["windows_evidence_error"] = str(exc)
        else:
            self.session_details["windows_dcb_at_open"] = self._dcb()

    def _dcb(self) -> dict:
        """Read active host settings via GetCommState without setting them."""
        try:
            dcb = self._win32.DCB()
            dcb.DCBlength = ctypes.sizeof(dcb)
            if not self._win32.GetCommState(self._session._port_handle, ctypes.byref(dcb)):
                raise OSError(f"GetCommState status {self._win32.GetLastError()}")
            return {field: int(getattr(dcb, field)) for field in DCB_FIELDS}
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            return {"error": str(exc)}

    def _available(self) -> int:
        """Read byte count and retain the flags pySerial normally discards."""
        if self._win32 is None:
            return super()._available()
        flags = self._win32.DWORD()
        status = self._win32.COMSTAT()
        if not self._win32.ClearCommError(self._session._port_handle, ctypes.byref(flags), ctypes.byref(status)):
            raise MotorError(f"Windows COM status query failed: {self._win32.GetLastError()}")
        self._error_mask |= int(flags.value)
        return int(status.cbInQue)

    def query(self, command: str) -> str:
        """Keep OS flags and a failed exchange's live settings in its wire record."""
        self._error_mask = 0
        self.last_exchange = {}
        try:
            return super().query(command)
        finally:
            if self._win32 is not None and self.last_exchange:
                self.last_exchange["windows_serial_errors"] = {
                    "mask": self._error_mask,
                    "flags": [name for bit, name in COMM_ERRORS.items() if self._error_mask & bit],
                }
                if self.last_exchange.get("error"):
                    self.last_exchange["windows_dcb_after_error"] = self._dcb()
