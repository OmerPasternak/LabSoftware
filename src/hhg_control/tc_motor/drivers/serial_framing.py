"""Shared bounded SMC100 ASCII framing; no optional imports or hardware access."""

import re
import time

from .base import MotorError, MotorSafetyError


class InvalidSerialBytes(MotorError):
    """The backend returned bytes outside the SMC100 ASCII frame alphabet."""


class FramedSerialTransport:
    """Common request/reply contract for explicit VISA and direct COM sessions."""

    POLL_INTERVAL_S = 0.01
    FIRST_POLL_DELAY_S = POLL_INTERVAL_S
    QUERY_GAP_S = 0.06
    MAX_REPLY_BYTES = 256

    def __init__(self, resource: str, *, timeout_ms: int = 300):
        """Validate a single COM address and bounded timeout without opening it."""
        if not isinstance(resource, str) or not re.fullmatch(r"(?:COM[1-9]\d*|ASRL[1-9]\d*::INSTR)", resource, re.I):
            raise MotorSafetyError("Specify one serial resource, e.g. COM21 or ASRL21::INSTR.")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 50 <= timeout_ms <= 1000:
            raise MotorSafetyError("Serial timeout must be 50–1000 ms.")
        number = resource[3:] if resource.upper().startswith("COM") else resource[4:].split("::")[0]
        self.resource = f"ASRL{number}::INSTR"
        self.com_port = f"COM{number}"
        self.timeout_ms = timeout_ms
        self._session = None
        self._last_write_at = None
        self._expected_host = None
        self.session_details = {}
        self.last_exchange = {}

    def _host_settings(self) -> dict:
        """Return host serial attributes; implementations must not query the motor."""
        raise NotImplementedError

    def _available(self) -> int:
        """Return buffered input count without waiting for a reply."""
        raise NotImplementedError

    def _read_count(self, count: int, remaining_ms: int) -> bytes:
        """Read at most one available chunk, without a hidden retry loop."""
        raise NotImplementedError

    def _valid_read_count(self, chunk: bytes, count: int) -> bool:
        """Require the complete counted chunk unless the transport uses CR termination."""
        return len(chunk) == count

    def query(self, command: str) -> str:
        """Validate one ASCII CRLF reply within a receive deadline after bounded pacing.

        Queries honor the transport's minimum send spacing, including after a
        preceding write. This is a project compatibility guard. STOP writes
        bypass it. Corrupt frames and late replies are rejected without retries.
        """
        if self._session is None:
            raise MotorError("Serial port is not open.")
        received = bytearray()
        chunks = []
        started = time.monotonic()
        failure = None
        before = {}
        try:
            if self._last_write_at is not None:
                wait = self._last_write_at + self.QUERY_GAP_S - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            # Pacing is at most QUERY_GAP_S; the configured receive budget
            # starts afterwards, so supported 50-ms peers retain their full budget.
            deadline = time.monotonic() + self.timeout_ms / 1000
            if self._expected_host is not None:
                before = self._host_settings()
                if before != self._expected_host:
                    raise MotorError(f"Host serial settings changed: expected {self._expected_host}, got {before}.")
            if self._available():
                raise MotorError("Unexpected input before request; response stream is not synchronized.")
            self.write(command)
            poll_delay = self.FIRST_POLL_DELAY_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"No complete CRLF reply within {self.timeout_ms} ms.")
                time.sleep(min(poll_delay, remaining))
                poll_delay = self.POLL_INTERVAL_S
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"No complete CRLF reply within {self.timeout_ms} ms.")
                count = self._available()
                if not count:
                    continue
                if count > self.MAX_REPLY_BYTES - len(received):
                    raise MotorError(f"Reply exceeds {self.MAX_REPLY_BYTES} bytes.")
                chunk = self._read_count(count, max(1, int(remaining * 1000)))
                chunks.append(chunk.hex(" "))
                received.extend(chunk)
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Reply arrived after the {self.timeout_ms} ms deadline.")
                if not self._valid_read_count(chunk, count):
                    raise MotorError("Serial backend returned an incomplete counted read.")
                if any(byte >= 127 or (byte < 32 and byte not in (10, 13)) for byte in chunk):
                    raise InvalidSerialBytes("Reply contains non-ASCII or unexpected control bytes.")
                end = received.find(b"\r\n")
                if end < 0:
                    continue
                if end != len(received) - 2 or b"\r" in received[:end] or b"\n" in received[:end]:
                    raise MotorError("Unexpected extra frame or malformed CRLF reply.")
                if self._available():
                    raise MotorError("Unexpected trailing input after CRLF reply.")
                return bytes(received[:-2]).decode("ascii")
        except Exception as exc:
            failure = str(exc)
            error_type = InvalidSerialBytes if isinstance(exc, InvalidSerialBytes) else MotorError
            raise error_type(f"Serial exchange {command!r} failed: {exc} "
                             f"[resource={self.resource}; RX hex={received.hex(' ') or '<none>'}]") from exc
        finally:
            after = {}
            if failure and self._expected_host is not None:
                try:
                    after = self._host_settings()
                except Exception as exc:
                    after = {"unavailable": str(exc)}
            self.last_exchange = {"command": command, "rx_hex": received.hex(" "),
                                  "rx_chunks_hex": chunks,
                                  "elapsed_s": time.monotonic() - started, "error": failure,
                                  "host_before": before, "host_after_failure": after}

    def write(self, command: str) -> None:
        """Send one command immediately; subclasses record its send time."""
        raise NotImplementedError
