"""Independent safety regressions from review of the SMC100 wire protocol."""

import pytest
import threading
from types import SimpleNamespace

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits
from hhg_control.tc_motor.drivers.smc100 import SMC100Motor, SafetyProfile, VisaSerialTransport
from hhg_control.tc_motor.sequencer import MotorSequencer


class ReviewTransport:
    """Offline serial peer; records PA/ST and can inject errors during preflight."""

    def __init__(self):
        self.commands = []
        self.state = "33"
        self.increment = 0.00003
        self.position = 1.0
        self.before_query = None
        self.close_error = False
        self.closed = False

    def open(self):
        """No hardware access."""

    def query(self, command):
        """Return the exact addressed SMC100 reply form."""
        self.commands.append(command)
        if self.before_query is not None:
            self.before_query(command)
        key = command[1:]
        values = {
            "VE": "SMC - Controller-driver version 1.00r",
            "ZX?": "LTA-HS", "TS": "0000" + self.state,
            "TP": str(self.position), "TE": "@", "SU?": str(self.increment),
            "VA?": "0.1", "AC?": "1.0", "SL?": "0", "SR?": "10",
            "BA?": "0", "BH?": "0",
        }
        return "1" + key[:2] + values[key]

    def write(self, command):
        """Record motion requests without moving any equipment."""
        self.commands.append(command)
        if command.startswith("1PA"):
            self.position = float(command[3:])

    def close(self):
        """Optionally simulate failure to release the connection."""
        if self.close_error:
            raise OSError("synthetic close failure")
        self.closed = True


def reviewed_driver(peer):
    """Build a synthetic reviewed configuration, never a real apparatus profile."""
    profile = SafetyProfile(
        limits=MotorLimits(0.1, 9.9, 0.1, 1.0, 2.0),
        expected_controller_version="SMC - Controller-driver version 1.00r",
        expected_stage_reference="LTA-HS",
        expected_encoder_increment_mm=0.00003,
        stop_clearance_mm=0.05,
        units_confirmed_mm=True,
        controller_confirmed_cc=True,
        mounting_clearance_checked=True,
    )
    return SMC100Motor("COM21", profile=profile, transport_factory=lambda: peer)


def test_stop_during_physical_preflight_prevents_pa():
    """STOP during serial checks must prevent a subsequent move from starting."""
    peer = ReviewTransport()
    sequencer = MotorSequencer(reviewed_driver(peer))
    sequencer.connect()
    peer.commands.clear()

    def cancel_on_preflight(command):
        if command == "1ZX?":
            sequencer.request_stop()

    peer.before_query = cancel_on_preflight
    with pytest.raises(MotorError):
        sequencer.move_to(1.5)
    assert not any(command.startswith("1PA") for command in peer.commands)


@pytest.mark.parametrize("cause", ["deadline", "external cancellation"])
def test_physical_preflight_guard_prevents_late_dispatch(monkeypatch, cause):
    """Exercise actual physical preflight with a synthetic peer, never a COM port."""
    from hhg_control.tc_motor import sequencer as seq_module
    peer = ReviewTransport()
    seq = MotorSequencer(reviewed_driver(peer))
    seq.connect()
    peer.commands.clear()
    clock = [0.0]
    cancel = threading.Event()
    monkeypatch.setattr(seq_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    def interrupt(command):
        if command == "1ZX?":
            if cause == "deadline":
                clock[0] = 100.0
            else:
                cancel.set()
    peer.before_query = interrupt
    with pytest.raises(MotorError):
        seq.move_to(1.5, timeout_s=1, cancel_event=cancel)
    assert not any(command.startswith("1PA") for command in peer.commands)
    assert seq.stop_requested


def test_unknown_state_cannot_confirm_standstill_on_close():
    """Unknown firmware state must not be accepted as proof of stopped motion."""
    peer = ReviewTransport()
    driver = SMC100Motor("COM21", transport_factory=lambda: peer)
    driver.connect()
    peer.state = "99"
    with pytest.raises(MotorError):
        driver.close()
    assert not peer.closed


def test_failed_calibration_with_uncertain_cleanup_never_labels_native_units_mm():
    """A surviving failed-connect session must not advertise rejected calibration."""
    peer = ReviewTransport()
    peer.increment = 0.003
    peer.close_error = True
    driver = reviewed_driver(peer)
    with pytest.raises(MotorError):
        driver.connect()
    assert driver.get_status().position_mm is None
    assert driver.info is None or not driver.info.units_verified


def test_failed_serial_close_keeps_shared_manager_open():
    """Failed cleanup must preserve handles and unrelated VISA sessions for retry."""
    transport = VisaSerialTransport("COM21")
    session = ReviewTransport()
    session.close_error = True
    shared_manager = ReviewTransport()
    transport._session = session
    transport._manager = shared_manager
    with pytest.raises(MotorError):
        transport.close()
    assert not shared_manager.closed


def test_serial_close_does_not_close_shared_resource_manager():
    """Disconnecting one motor must not terminate other instruments' VISA handles."""
    transport = VisaSerialTransport("COM21")
    session = ReviewTransport()
    shared_manager = ReviewTransport()
    transport._session = session
    transport._manager = shared_manager
    transport.close()
    assert session.closed
    assert not shared_manager.closed


@pytest.mark.parametrize("unexpected_state", ["0A", "14", "1E", "1F", "3C"])
def test_lost_reference_or_external_state_change_cannot_silently_rearm(unexpected_state):
    """Reset, configuration, homing or disable invalidates the reviewed reference."""
    peer = ReviewTransport()
    driver = reviewed_driver(peer)
    driver.connect()
    peer.state = unexpected_state
    driver.get_status()
    peer.state = "32"
    with pytest.raises(MotorError):
        driver.move_to(1.5)
    assert not any(command.startswith("1PA") for command in peer.commands)
    assert not driver.motion_enabled
