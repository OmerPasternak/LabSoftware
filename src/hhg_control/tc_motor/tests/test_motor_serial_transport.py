"""Exercise real transport logic with byte-level VISA peers, never hardware."""

from types import SimpleNamespace

import pytest

from hhg_control.tc_motor.drivers.base import MotorError
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

    def write(self, command):
        assert not self.pending and not self.buffer
        self.writes.append(command)
        self.write_times.append(self.clock.now)
        chunks = self.chunks(command) if callable(self.chunks) else self.chunks
        self.pending = [(self.clock.now + self.delay * (i + 1), chunk)
                        for i, chunk in enumerate(chunks)]

    @property
    def bytes_in_buffer(self):
        while self.pending and self.pending[0][0] <= self.clock.now + 1e-12:
            _, chunk = self.pending.pop(0)
            self.buffer.extend(chunk)
        return len(self.buffer)

    def read_bytes(self, count, *, break_on_termchar):
        assert not break_on_termchar
        assert self.clock.now > self.write_times[-1]
        assert 0 < count <= len(self.buffer)
        assert 1 <= self.timeout <= 300
        self.read_counts.append(count)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def serial_peer(monkeypatch):
    from hhg_control.tc_motor.drivers import smc100
    clock = Clock()
    monkeypatch.setattr(smc100, "time", SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep))

    def make(chunks, *, delay=0.01):
        session = BufferedVisa(clock, chunks, delay=delay)
        transport = VisaSerialTransport("COM21")
        transport._session = session
        return transport, session, clock

    return make


@pytest.mark.parametrize("chunks", [
    [b"1TS000032\r\n"], [b"1TS", b"000032", b"\r", b"\n"],
    [bytes([byte]) for byte in b"1TS000032\r\n"],
])
def test_buffered_receive_assembles_one_complete_frame(serial_peer, chunks):
    transport, session, clock = serial_peer(chunks)
    assert transport.query("1TS") == "1TS000032"
    assert session.writes == ["1TS"]
    assert sum(session.read_counts) == len(b"1TS000032\r\n")
    assert clock.waits and clock.now < 0.3
    assert session.timeout == 300


@pytest.mark.parametrize("chunks,reason,hex_bytes", [
    ([b"\xb1TS000032\r\n"], "non-ASCII", "b1 54 53"),
    ([b"1TP\xca2\r\n"], "non-ASCII", "31 54 50 ca"),
    ([b"1TS\x0000032\r\n"], "control bytes", "31 54 53 00"),
    ([b"1TP2\r\n1TS000032\r\n"], "extra frame", "31 54 50 32"),
    ([b"1TP2\n\r\n"], "malformed CRLF", "31 54 50 32 0a"),
])
def test_bad_bytes_are_reported_exactly_and_never_retried(serial_peer, chunks, reason, hex_bytes):
    transport, session, _ = serial_peer(chunks)
    with pytest.raises(MotorError, match=reason) as error:
        transport.query("1TS")
    assert hex_bytes in str(error.value)
    assert "ASRL21::INSTR" in str(error.value)
    assert session.writes == ["1TS"]
    assert session.timeout == 300


@pytest.mark.parametrize("chunks", [[], [b"1TP2"], [b"1TP2\r"]])
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
    session.read_bytes = lambda *a, **kw: b"1TP"
    with pytest.raises(MotorError, match="incomplete counted read"):
        transport.query("1TP")


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


def test_stop_write_is_immediate_without_receive_wait_or_query(serial_peer):
    transport, session, clock = serial_peer([])
    transport.write("1ST")
    assert session.writes == ["1ST"] and clock.waits == []
