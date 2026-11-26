"""Read-only VISA diagnostic transport inferred from the lab's NI I/O Trace."""

import re

from .base import MotorSafetyError
from .smc100 import VisaSerialTransport


class LabviewReferenceTransport(VisaSerialTransport):
    """Try the observed CR-terminated reads and timing while retaining strict CRLF checks."""

    FIRST_POLL_DELAY_S = 0.100
    QUERY_GAP_S = 0.130
    FLUSH_ON_OPEN = False

    def _configure_receive(self, constants) -> str:
        """Reproduce the observed CR split without purging the receive buffer."""
        self._session.read_termination = None
        self._session.set_visa_attribute(constants.ResourceAttribute.termchar, 13)
        self._session.end_input = constants.SerialTermination.termination_char
        self._session.end_output = constants.SerialTermination.none
        return "LabVIEW reference: wait 100 ms, read through CR, then read LF"

    def _read_count(self, count: int, remaining_ms: int) -> bytes:
        """Recognize normal termination while keeping the shared receive deadline."""
        with self._session.ignore_warning(0x3FFF0005):  # VI_SUCCESS_TERM_CHAR
            return super()._read_count(count, remaining_ms)

    def _valid_read_count(self, chunk: bytes, count: int) -> bool:
        """Allow the observed one-byte CR split; framing still requires the LF."""
        return super()._valid_read_count(chunk, count) or (
            len(chunk) == count - 1 and chunk.endswith(b"\r")
        )

    def write(self, command: str) -> None:
        """Allow only addressed status and position queries on this transport."""
        if not re.fullmatch(r"(?:[1-9]|[12][0-9]|3[01])(?:TS|TP)", command):
            raise MotorSafetyError("The LabVIEW reference accepts only addressed TS and TP queries.")
        super().write(command)
