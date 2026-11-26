"""Read-only VISA diagnostic transport inferred from the lab's NI I/O Trace."""

import re

from .base import MotorSafetyError
from .smc100 import VisaSerialTransport


TRACE_STATUS_POLLS = 8


class LabviewReferenceTransport(VisaSerialTransport):
    """Try the observed CR-terminated reads and timing while retaining strict CRLF checks."""

    FIRST_POLL_DELAY_S = 0.100
    QUERY_GAP_S = 0.130
    FLUSH_ON_OPEN = False
    ALLOWED_QUERIES = frozenset({"TS", "TP"})

    def _configure_port(self, constants) -> None:
        """Apply the serial attributes in the order recorded in LabVIEW."""
        self._session.timeout = self.timeout_ms
        self._session.baud_rate = 57600
        self._session.data_bits = 8
        self._session.flow_control = constants.ControlFlow.xon_xoff
        self._session.parity = constants.Parity.none
        self._session.stop_bits = constants.StopBits.one

    def _configure_receive(self, constants) -> str:
        """Reproduce the observed CR split without purging the receive buffer."""
        self._session.read_termination = None
        self._session.set_visa_attribute(constants.ResourceAttribute.termchar, 13)
        self._session.end_input = constants.SerialTermination.termination_char
        self._session.end_output = constants.SerialTermination.none
        return "LabVIEW reference: fixed VISA timeout, wait 100 ms, read CR then LF"

    def _read_count(self, count: int, remaining_ms: int) -> bytes:
        """Use LabVIEW's fixed VISA timeout and capture each native return code."""
        if self._session.timeout != self.timeout_ms:
            raise MotorSafetyError("Host VISA receive timeout changed.")
        try:
            with self._session.ignore_warning(0x3FFF0005, 0x3FFF0006):
                chunk, status = self._session.visalib.read(self._session.session, count)
        except Exception as error:
            self._visa_reads.append({"requested": count, "returned": None,
                                     "status": getattr(error, "error_code", None)})
            raise
        self._visa_reads.append({"requested": count, "returned": len(chunk), "status": int(status)})
        return chunk

    def query(self, command: str) -> str:
        """Attach native read counts and completion codes even on a rejected reply."""
        if self._session is None:
            return super().query(command)
        self._visa_reads = []
        try:
            return super().query(command)
        finally:
            self.last_exchange["visa_reads"] = self._visa_reads

    def _valid_read_count(self, chunk: bytes, count: int) -> bool:
        """Allow the observed one-byte CR split; framing still requires the LF."""
        return super()._valid_read_count(chunk, count) or (
            len(chunk) == count - 1 and chunk.endswith(b"\r")
        )

    def write(self, command: str) -> None:
        """Enforce the transport's addressed query allowlist on every write."""
        match = re.fullmatch(r"(?:[1-9]|[12][0-9]|3[01])([A-Z]{2}\??)", command)
        if match is None or match[1] not in self.ALLOWED_QUERIES:
            raise MotorSafetyError("This VISA mode accepts only its documented read queries.")
        super().write(command)
