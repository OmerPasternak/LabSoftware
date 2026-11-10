"""Direct COM transport regressions using a synthetic pySerial module only."""

import sys
from types import SimpleNamespace

import pytest

from hhg_control.tc_motor.drivers import direct_serial, serial_framing, smc100
from hhg_control.tc_motor.drivers.base import MotorError
from hhg_control.tc_motor.drivers.direct_serial import DirectSerialTransport
from test_motor_serial_transport import BufferedVisa, Clock


class SerialPeer(BufferedVisa):
    """Byte-level fake exposing pySerial's API, without any OS port calls."""

    def __init__(self, clock, chunks, kwargs):
        super().__init__(clock, chunks)
        self.__dict__.update(kwargs)
        self.port = kwargs["port"]
        self.is_open = False
        self.resets = 0

    def open(self):
        assert self.port == "COM21"
        self.is_open = True

    def reset_input_buffer(self):
        self.resets += 1
        self.buffer.clear()

    @property
    def in_waiting(self):
        return self.bytes_in_buffer

    def write(self, payload):
        assert isinstance(payload, bytes) and payload.endswith(b"\r\n")
        return super().write(payload[:-2].decode("ascii"))

    def read(self, count):
        # A direct read is nonblocking: no repeated timeout reconfiguration.
        assert self.timeout == 0
        assert 0 < count <= len(self.buffer)
        self.read_counts.append(count)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result


@pytest.fixture
def direct_peer(monkeypatch):
    clock = Clock()
    timer = SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)
    for module in (direct_serial, serial_framing, smc100):
        monkeypatch.setattr(module, "time", timer)
    monkeypatch.setattr(direct_serial, "sys", SimpleNamespace(platform="win32"))

    def make(chunks):
        peers = []

        def factory(**kwargs):
            assert kwargs["port"] is None
            peer = SerialPeer(clock, chunks, kwargs)
            peers.append(peer)
            return peer

        monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(
            Serial=factory, EIGHTBITS=8, PARITY_NONE="N", STOPBITS_ONE=1, VERSION="synthetic"))
        transport = DirectSerialTransport("ASRL21::INSTR")
        assert peers == []  # Construction must not open or enumerate ports.
        transport.open()
        return transport, peers[0], clock

    return make


def test_direct_port_is_explicit_and_nonblocking_with_finite_writes(direct_peer):
    transport, peer, clock = direct_peer([b"1TP", b"29.99999\r", b"\n"])
    assert peer.is_open and peer.resets == 1
    assert peer.baudrate == 57600 and peer.bytesize == 8
    assert peer.parity == "N" and peer.stopbits == 1 and peer.xonxoff
    assert peer.timeout == 0 and peer.write_timeout == .3
    assert not peer.rtscts and not peer.dsrdtr
    assert transport.query("1TP") == "1TP29.99999"
    assert transport.query("1TP") == "1TP29.99999"
    assert peer.write_times[1] - peer.write_times[0] >= .06 - 1e-12
    before = clock.now
    transport.write("1ST")
    assert clock.now == before
    peer.baudrate = 9600
    with pytest.raises(MotorError, match="Host serial settings changed"):
        transport.query("1TP")
    assert peer.writes == ["1TP", "1TP", "1ST"]
    assert transport.last_exchange["host_after_failure"]["baud_rate"] == 9600
    assert transport.session_details["transport"] == "serial"
    transport.close()
    assert peer.closed and transport._session is None


@pytest.mark.parametrize('payload', [bytes.fromhex("4c 15 25 ca 72 ca ca ca ca ca 6a 0a")])
def test_exact_lab_corruption_is_rejected_without_a_retry(direct_peer, payload):
    transport, peer, _ = direct_peer([payload])
    with pytest.raises(MotorError, match="non-ASCII") as error:
        transport.query("1TP")
    assert payload.hex(" ") in str(error.value)
    assert peer.writes == ["1TP"] and peer.resets == 1
    assert transport.last_exchange["host_after_failure"]["baud_rate"] == 57600
    transport.close()








def test_partial_write_is_a_fault_and_is_not_repeated(direct_peer):
    transport, peer, _ = direct_peer([])
    peer.write = lambda data: 1
    with pytest.raises(MotorError, match="Incomplete serial command write"):
        transport.query("1TP")


def test_missing_pyserial_has_the_one_click_setup_instruction(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    with pytest.raises(MotorError, match="setup_physical_mode.vbs"):
        DirectSerialTransport("COM21").open()


def test_driver_keeps_motion_locked_with_explicit_direct_transport():
    from hhg_control.tc_motor.gui.workers import ConnectionSettings, create_sequencer
    seq = create_sequencer(ConnectionSettings("Physical", "COM21", transport="serial"))
    assert isinstance(seq.driver._transport_factory(), DirectSerialTransport)
    assert not seq.motion_enabled and not seq.is_connected and seq.driver.profile is None


def test_direct_open_failure_does_not_attempt_visa(monkeypatch):
    peer = SimpleNamespace(close=lambda: None)
    peer.open = lambda: (_ for _ in ()).throw(PermissionError("Port in use"))
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(
        Serial=lambda **kwargs: peer, EIGHTBITS=8, PARITY_NONE="N", STOPBITS_ONE=1))
    monkeypatch.setitem(sys.modules, "pyvisa", None)
    with pytest.raises(PermissionError, match="Port in use"):
        DirectSerialTransport("COM21").open()


def test_direct_transport_uses_existing_inspection_driver_and_mm_lock(direct_peer):
    values = {"VE": "SMC_CC synthetic", "ZX?": "LTA-HS", "TS": "000033", "TP": "29.99999",
              "TE": "@", "SU?": "3.53932e-05", "VA?": "5", "AC?": "20", "SL?": "0",
              "SR?": "50", "BA?": "0", "BH?": "0"}
    def replies(command):
        frame = (command[:3] + values[command[1:]] + "\r\n").encode("ascii")
        return [frame[:-1], frame[-1:]]
    transport, peer, _ = direct_peer(replies)
    transport.open = lambda: None  # Already opened by the offline fixture.
    driver = smc100.SMC100Motor("COM21", transport_factory=lambda: transport, transport="serial")
    driver.connect()
    for _ in range(100):
        status = driver.get_status()
        assert status.native_position == 29.99999 and status.position_mm is None
        assert status.is_ready and not driver.motion_enabled
    driver.close()
    assert peer.closed and not any(c.startswith(("1ST", "1PA", "1OR", "1RS", "1MM")) for c in peer.writes)
