"""Explicit pySerial COM transport; same guarded SMC100 driver above this layer."""

import sys
import time
from pathlib import Path

from .base import MotorError
from .serial_framing import FramedSerialTransport


class DirectSerialTransport(FramedSerialTransport):
    """Windows-native exclusive COM access through pySerial, with no VISA runtime.

    This explicitly selected alternative bypasses the failing VISA path. It is
    never an automatic fallback. Port settings are 57600/8N1/XON-XOFF; reads are
    nonblocking and writes have a timeout. No ports are discovered or reopened.
    """

    def open(self) -> None:
        """Configure and open only the requested host port; never command motion."""
        if sys.platform != "win32":
            raise MotorError("Direct serial mode currently supports Windows only (exclusive COM ownership).")
        try:
            import serial
        except ImportError as exc:
            raise MotorError("pySerial is missing. Run setup_physical_mode.vbs, then restart TC motor.") from exc
        try:
            self._session = serial.Serial(port=None, baudrate=57600, bytesize=serial.EIGHTBITS,
                                          parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                          xonxoff=True, rtscts=False, dsrdtr=False,
                                          timeout=0, write_timeout=self.timeout_ms / 1000)
            self._session.port = self.com_port
            self._session.open()  # Windows CreateFile uses share mode 0.
            self._session.reset_input_buffer()
            self._expected_host = {"baud_rate": 57600, "data_bits": 8, "parity": "N",
                                   "stop_bits": 1, "xonxoff": True, "rtscts": False, "dsrdtr": False}
            if self._host_settings() != self._expected_host:
                raise MotorError("pySerial did not apply the requested serial settings.")
            self.session_details = {
                "transport": "serial", "resource": self.com_port,
                "driver_file": str(Path(__file__).resolve()),
                "pyserial_version": serial.VERSION,
                "receive_method": "available-byte-count / strict ASCII CRLF",
                "timeout_ms": self.timeout_ms, "query_gap_s": self.QUERY_GAP_S,
                "host_settings_source": "pySerial configured attributes",
                **self._expected_host,
            }
        except Exception:
            self.close()
            raise

    def _host_settings(self) -> dict:
        """Read pySerial attributes without any controller command."""
        return {"baud_rate": self._session.baudrate, "data_bits": self._session.bytesize,
                "parity": self._session.parity, "stop_bits": self._session.stopbits,
                "xonxoff": self._session.xonxoff, "rtscts": self._session.rtscts,
                "dsrdtr": self._session.dsrdtr}

    def _available(self) -> int:
        """Return the native input-buffer count without blocking."""
        return self._session.in_waiting

    def _read_count(self, count: int, remaining_ms: int) -> bytes:
        """Read once with permanent nonblocking timeout; no port reconfiguration."""
        return self._session.read(count)

    def write(self, command: str) -> None:
        """Write exactly one ASCII CRLF command, immediately including STOP."""
        payload = (command + "\r\n").encode("ascii")
        count = self._session.write(payload)
        self._last_write_at = time.monotonic()
        if count != len(payload):
            raise MotorError("Incomplete serial command write; delivery is uncertain.")

    def close(self) -> None:
        """Release only this host COM handle, retaining it on cleanup failure."""
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:
                raise MotorError(f"Direct serial port cleanup failed: {exc}") from exc
            self._session = None
