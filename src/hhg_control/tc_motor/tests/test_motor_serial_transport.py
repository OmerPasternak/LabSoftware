"""Exercise real transport logic with byte-level VISA peers, never hardware."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hhg_control.tc_motor.drivers.base import MotorError, MotorSafetyError
from hhg_control.tc_motor.drivers.labview_reference import LabviewReferenceTransport
from hhg_control.tc_motor.drivers.smc100 import SMC100Motor, VisaSerialTransport


class Clock:
    """Deterministic seconds; every receive wait advances the deadline clock."""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.waits.append(seconds)
        self.now += seconds


class BufferedVisa:
    """ATEN-style receive chunks become available after each polling interval.

    A text read/query is deliberately unavailable. The implementation must wait
    for input and read exactly the reported count, including split CR/LF bytes.
    """

    def __init__(self, clock, chunks, *, delay=0.01):
        self.clock = clock
        self.chunks = chunks
        self.delay = delay
        self.timeout = 300
        self.writes = []
        self.read_counts = []
        self.closed = False
        self.pending = []
        self.buffer = bytearray()
        self.write_times = []
        self.session = 1
        self.visalib = SimpleNamespace(read=self.read_available)
        self.count_warning_ignored = False

    @contextmanager
    def ignore_warning(self, *statuses):
        assert 0x3FFF0006 in statuses and set(statuses) <= {0x3FFF0005, 0x3FFF0006}
        self.count_warning_ignored = True
        try:
            yield
        finally:
            self.count_warning_ignored = False

    def write(self, command):
        assert not self.pending and not self.buffer
        self.writes.append(command)
        self.write_times.append(self.clock.now)
        chunks = self.chunks(command) if callable(self.chunks) else self.chunks
        self.pending = [(self.clock.now + self.delay * (i + 1), chunk)
                        for i, chunk in enumerate(chunks)]
        return len((command + "\r\n").encode("ascii"))

    @property
    def bytes_in_buffer(self):
        while self.pending and self.pending[0][0] <= self.clock.now + 1e-12:
            _, chunk = self.pending.pop(0)
            self.buffer.extend(chunk)
        return len(self.buffer)

    def read_available(self, session, count):
        assert session == self.session
        assert self.count_warning_ignored
        assert self.clock.now > self.write_times[-1]
        assert 0 < count <= len(self.buffer)
        assert 1 <= self.timeout <= 300
        self.read_counts.append(count)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result, 0x3FFF0006

    def close(self):
        self.closed = True


@pytest.fixture
def serial_peer(monkeypatch):
    from hhg_control.tc_motor.drivers import smc100
    from hhg_control.tc_motor.drivers import serial_framing
    clock = Clock()
    timer = SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    monkeypatch.setattr(smc100, "time", timer)
    monkeypatch.setattr(serial_framing, "time", timer)

    def make(chunks, *, delay=0.01):
        session = BufferedVisa(clock, chunks, delay=delay)
        transport = VisaSerialTransport("COM21")
        transport._session = session
        return transport, session, clock

    return make


def test_labview_reference_waits_and_assembles_cr_split_without_allowing_moves(serial_peer):
    _, peer, clock = serial_peer([b"1TS000033\r\n"])
    transport = LabviewReferenceTransport("COM21")
    transport._session = peer
    read_times = []
    read = peer.read_available

    def terminate_at_cr(session, count):
        read_times.append(clock.now)
        assert peer.timeout == 300  # The reference retains the fixed VISA timeout.
        stop = peer.buffer.find(b"\r")
        if stop >= 0:
            return read(session, min(count, stop + 1))[0], 0
        return read(session, count)

    peer.visalib.read = terminate_at_cr
    for command in ("1PA20", "1ST", "1OR", "1VA?"):
        with pytest.raises(MotorSafetyError):
            transport.write(command)
    assert peer.writes == []
    assert transport.query("1TS") == "1TS000033"
    assert read_times == pytest.approx([0.10, 0.11])
    assert transport.last_exchange["rx_chunks_hex"] == [
        "31 54 53 30 30 30 30 33 33 0d", "0a",
    ]
    assert transport.last_exchange["visa_reads"] == [
        {"requested": 11, "returned": 10, "status": 0},
        {"requested": 1, "returned": 1, "status": 0x3FFF0006},
    ]


@pytest.mark.parametrize('chunks', [[bytes([byte]) for byte in b"1TS000032\r\n"]])
def test_buffered_receive_assembles_one_complete_frame(serial_peer, chunks):
    transport, session, clock = serial_peer(chunks)
    assert transport.query("1TS") == "1TS000032"
    assert session.writes == ["1TS"]
    assert sum(session.read_counts) == len(b"1TS000032\r\n")
    assert clock.waits and clock.now < 0.3
    assert session.timeout == 300


@pytest.mark.parametrize('chunks,reason,hex_bytes', [([b"\xb1TS000032\r\n"], "non-ASCII", "b1 54 53"), ([b"1TS\x0000032\r\n"], "control bytes", "31 54 53 00"), ([b"1TP2\r\n1TS000032\r\n"], "extra frame", "31 54 50 32"), ([b"1TP2\n\r\n"], "malformed CRLF", "31 54 50 32 0a")])
def test_bad_bytes_are_reported_exactly_and_never_retried(serial_peer, chunks, reason, hex_bytes):
    transport, session, _ = serial_peer(chunks)
    with pytest.raises(MotorError, match=reason) as error:
        transport.query("1TS")
    assert hex_bytes in str(error.value)
    assert "ASRL21::INSTR" in str(error.value)
    assert session.writes == ["1TS"]
    assert session.timeout == 300


@pytest.mark.parametrize('chunks', [[b"1TP2\r"]])
def test_silent_or_partial_reply_has_total_deadline_and_preserves_bytes(serial_peer, chunks):
    transport, session, clock = serial_peer(chunks)
    with pytest.raises(MotorError, match="No complete CRLF reply") as error:
        transport.query("1TP")
    assert "RX hex=" in str(error.value)
    assert clock.now == pytest.approx(0.3)
    assert len(clock.waits) <= 31
    assert session.writes == ["1TP"] and session.timeout == 300


def test_stale_reply_prevents_a_new_request_without_flushing_it(serial_peer):
    transport, session, _ = serial_peer([])
    session.buffer.extend(b"1TP2\r\n")
    with pytest.raises(MotorError, match="Unexpected input before request"):
        transport.query("1TS")
    assert session.writes == []
    assert session.buffer == b"1TP2\r\n"


def test_oversize_input_is_bounded(serial_peer):
    transport, session, _ = serial_peer([b"x" * 257])
    with pytest.raises(MotorError, match="exceeds 256 bytes"):
        transport.query("1VE")
    assert session.read_counts == []


def test_counted_read_cannot_return_less_than_announced(serial_peer):
    transport, session, _ = serial_peer([b"1TP2\r\n"])
    session.visalib.read = lambda *a: (b"1TP", 0)
    with pytest.raises(MotorError, match="incomplete counted read"):
        transport.query("1TP")


def test_late_backend_return_is_not_accepted_as_a_current_reply(serial_peer):
    transport, session, clock = serial_peer([b"1TP2\r\n"])
    read = session.visalib.read

    def late_read(*args):
        result = read(*args)
        clock.now += 0.4
        return result

    session.visalib.read = late_read
    with pytest.raises(MotorError, match="arrived after"):
        transport.query("1TP")
    assert session.writes == ["1TP"] and session.timeout == 300


def test_real_transport_with_driver_survives_repeated_fragmented_snapshots(serial_peer):
    values = {"VE": "SMC_CC - synthetic", "ZX?": "LTA-HS", "TS": "000032",
              "TP": "2", "TE": "@", "SU?": "0.0000353932", "VA?": "5",
              "AC?": "20", "SL?": "0", "SR?": "50", "BA?": "0", "BH?": "0"}

    def replies(command):
        text = ("1" + command[1:3] + values[command[1:]] + "\r\n").encode("ascii")
        return [text[:2], text[2:-2], b"\r", b"\n"]

    transport, session, _ = serial_peer(replies)
    transport.open = lambda: None  # VISA initialization is separately tested.
    driver = SMC100Motor("COM21", transport_factory=lambda: transport)
    driver.connect()
    for _ in range(1000):
        status = driver.get_status()
        assert status.native_position == 2 and status.is_ready
        assert status.position_mm is None and not driver.motion_enabled
    driver.close()
    assert session.closed
    assert not any(text.startswith(("1PA", "1ST", "1OR", "1RS", "1MM")) for text in session.writes)




def test_visa_query_pacing_is_independent_of_receive_wait(serial_peer):
    transport, session, clock = serial_peer([b"1TP2\r\n"])
    transport.query("1TP")
    transport.query("1TP")
    assert session.write_times[1] - session.write_times[0] >= .06 - 1e-12
    before = clock.now
    transport.write("1ST")
    assert clock.now == before and session.writes[-1] == "1ST"


def test_visa_setting_drift_is_reported_before_any_new_command(serial_peer):
    transport, session, _ = serial_peer([])
    session.baud_rate = 9600
    session.data_bits = 8
    session.parity = 0
    session.stop_bits = 10
    session.flow_control = 1
    transport._expected_host = {"baud_rate": 57600, "data_bits": 8, "parity": 0,
                                "stop_bits": 10, "flow_control": 1}
    with pytest.raises(MotorError, match="Host serial settings changed"):
        transport.query("1TS")
    assert not session.writes
    assert transport.last_exchange["host_after_failure"]["baud_rate"] == 9600



def test_minimum_receive_timeout_survives_sequential_query_pacing(serial_peer):
    transport, peer, clock = serial_peer([b"1TP2\r\n"])
    transport.timeout_ms = 50
    assert transport.query("1TP") == "1TP2"
    assert transport.query("1TP") == "1TP2"
    assert peer.write_times[1]-peer.write_times[0] >= .06-1e-12
    assert clock.now < .12
