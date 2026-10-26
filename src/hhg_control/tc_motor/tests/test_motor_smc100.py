"""Offline protocol/safety tests. All values and responses below are synthetic."""

from dataclasses import replace
import json
from types import SimpleNamespace
import sys

import pytest

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits, MotorSafetyError
from hhg_control.tc_motor.drivers.smc100 import SafetyProfile, SMC100Motor, VisaSerialTransport
from hhg_control.tc_motor.sequencer import MotorSequencer


VERSION = "SMC - Controller-driver version SYNTHETIC"


def profile():
    """Synthetic profile for a fictitious apparatus; never a physical calibration."""
    return SafetyProfile(MotorLimits(1, 9, 1, 2, 2), VERSION, "LTA-HS", 0.0001,
                         0.1, True, True, True)


class FakeTransport:
    """Vendor-protocol response fixture with a strict no-unknown-command map."""

    def __init__(self):
        self.commands = []
        self.opened = False
        self.closed = False
        self.responses = {"VE": VERSION, "ZX?": "LTA-HS", "TS": "000032", "TP": "2",
                          "TE": "@", "SU?": "0.0001", "VA?": "1", "AC?": "2",
                          "SL?": "0", "SR?": "10", "BA?": "0", "BH?": "0"}

    def open(self):
        self.opened = True

    def query(self, text):
        self.commands.append(text)
        assert text.startswith("1")
        command = text[1:]
        value = self.responses[command]
        if isinstance(value, Exception):
            raise value
        return "1" + command[:2] + value

    def write(self, text):
        self.commands.append(text)
        if text.startswith("1PA"):
            self.responses["TP"] = text[3:]

    def close(self):
        self.closed = True


def make_driver(*, reviewed=True):
    transport = FakeTransport()
    driver = SMC100Motor("ASRL21::INSTR", profile=profile() if reviewed else None,
                         transport_factory=lambda: transport)
    return driver, transport


def test_setup_report_only_queries_state_position_and_preserves_motion_lock():
    driver, io = make_driver(reviewed=False)
    seq = MotorSequencer(driver)
    seq.connect()
    try:
        io.commands.clear()
        report = seq.setup_report()
        assert io.commands == ["1TS", "1TP"]
        assert report["native_settings"] == {"SU": .0001, "VA": 1, "AC": 2,
                                             "SL": 0, "SR": 10, "BA": 0, "BH": 0}
        assert report["status"]["position_mm"] is None
        assert report["status"]["native_position"] == 2
        assert report["reviewed_limits"] is None
        assert not report["move_permitted_now"]
        assert not driver.motion_enabled
    finally:
        seq.disconnect()


def test_connect_is_inspection_only_and_does_not_guess_mm():
    driver, io = make_driver(reviewed=False)
    assert not io.opened
    info = driver.connect()
    assert not info.simulation and not info.units_verified
    status = driver.get_status()
    assert status.position_mm is None and status.native_position == 2
    with pytest.raises(MotorSafetyError):
        driver.get_position()
    with pytest.raises(MotorSafetyError):
        driver.move_to(3)
    assert all(not command.startswith(("1PA", "1OR", "1RS", "1MM")) for command in io.commands)
    driver.close()
    assert io.closed


def test_inspection_only_serial_corruption_releases_port_without_motor_command():
    driver, io = make_driver(reviewed=False)
    seq = MotorSequencer(driver)
    seq.connect()
    io.responses["TP"] = UnicodeDecodeError("ascii", b"\xca", 0, 1, "invalid byte")
    with pytest.raises(MotorError, match="SMC100 query TP failed"):
        seq.snapshot()
    assert driver.communication_uncertain
    assert seq.disconnect() is True
    assert io.closed and not driver.is_connected
    assert not any(command.startswith(("1ST", "1PA")) for command in io.commands)


def test_motion_capable_serial_corruption_requires_external_verification_to_release():
    driver, io = make_driver(reviewed=True)
    seq = MotorSequencer(driver)
    seq.connect()
    io.responses["TP"] = UnicodeDecodeError("ascii", b"\xca", 0, 1, "invalid byte")
    with pytest.raises(MotorError, match="SMC100 query TP failed"):
        seq.snapshot()
    with pytest.raises(MotorError, match="STOP could not be confirmed"):
        seq.disconnect()
    assert driver.is_connected and not io.closed
    assert "1ST" in io.commands
    commands_before_release = list(io.commands)
    seq.release_after_manual_verification()
    assert io.closed and not driver.is_connected
    assert io.commands == commands_before_release  # Recovery closes the port; it sends no command.


def test_manual_release_requires_fault_and_attempted_stop():
    driver, io = make_driver(reviewed=True)
    seq = MotorSequencer(driver)
    seq.connect()
    with pytest.raises(MotorSafetyError, match="communication loss and an attempted STOP"):
        seq.release_after_manual_verification()
    assert driver.is_connected and not io.closed
    seq.disconnect()


def test_reviewed_move_uses_documented_protocol_and_preserves_configuration():
    driver, io = make_driver()
    driver.connect()
    driver.move_to(3)
    assert "1PA3.000000000000" in io.commands
    assert "1TP" in io.commands and "1TS" in io.commands
    assert "1TP?" not in io.commands and "1TS?" not in io.commands
    driver.stop()
    driver.close()
    assert "1ST" in io.commands
    assert not any(command.startswith(("1RS", "1OR", "1MM", "1DH")) for command in io.commands)
    for command in io.commands:
        assert command in {"1VE", "1ZX?", "1TS", "1TP", "1TE", "1ST", "1PA3.000000000000"} or command.endswith("?")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 10, True, "3"])
def test_invalid_targets_never_generate_pa(value):
    driver, io = make_driver()
    driver.connect()
    with pytest.raises(MotorSafetyError):
        driver.move_to(value)
    assert not any(c.startswith("1PA") for c in io.commands)
    driver.close()


@pytest.mark.parametrize("key,value", [("SU?", "0.1"), ("VA?", "2"), ("AC?", "3"),
                                      ("BA?", "0.01"), ("BH?", "0.01"),
                                      ("SL?", "1"), ("SR?", "9"), ("ZX?", "OTHER")])
def test_configuration_changes_are_rejected_before_motion(key, value):
    driver, io = make_driver()
    driver.connect()
    io.responses[key] = value
    with pytest.raises(MotorSafetyError):
        driver.move_to(3)
    assert not driver.motion_enabled
    assert not any(c.startswith("1PA") for c in io.commands)
    driver.close()


def test_controller_fault_is_latched_even_when_ts_clears_it():
    driver, io = make_driver()
    driver.connect()
    io.responses["TS"] = "008032"  # Wrong ESP stage: boot-only fault must not disappear.
    assert driver.get_status().error_code == 128
    io.responses["TS"] = "000032"
    assert not driver.get_status().is_ready
    with pytest.raises(MotorSafetyError):
        driver.move_to(3)
    driver.close()


@pytest.mark.parametrize("state", ["0A", "14", "1E", "28", "3C", "46"])
def test_only_ready_states_allow_moves(state):
    driver, io = make_driver()
    driver.connect()
    io.responses["TS"] = "0000" + state
    with pytest.raises(MotorSafetyError):
        driver.move_to(3)
    assert not any(c.startswith("1PA") for c in io.commands)
    io.responses["TS"] = "000032"
    driver.close()


def test_timed_out_pa_error_check_attempts_stop_and_retains_uncertainty():
    driver, io = make_driver()
    driver.connect()
    original_write = io.write
    def fail_after_pa(command):
        original_write(command)
        if command.startswith("1PA"):
            io.responses["TE"] = TimeoutError("synthetic unplug")
    io.write = fail_after_pa
    with pytest.raises(MotorError, match="STOP is unconfirmed"):
        driver.move_to(3)
    assert "1ST" in io.commands
    assert not driver.motion_enabled
    with pytest.raises(MotorError, match="STOP"):
        driver.close()
    assert driver.is_connected and not io.closed


def test_wrong_axis_reply_latches_fault_and_never_retries_motion():
    driver, io = make_driver()
    driver.connect()
    io.query = lambda text: "2TP3"
    with pytest.raises(MotorError):
        driver.move_to(3)
    assert not driver.motion_enabled
    assert not any(c.startswith("1PA") for c in io.commands)


def test_move_rejects_encoder_rounding_outside_reviewed_boundary():
    driver, io = make_driver()
    driver.connect()
    with pytest.raises(MotorSafetyError):
        driver.move_to(1)
    assert not any(c.startswith("1PA") for c in io.commands)
    driver.close()


def test_profile_requires_actual_review_and_strict_json(tmp_path):
    with pytest.raises(MotorSafetyError):
        replace(profile(), units_confirmed_mm=False)
    with pytest.raises(MotorSafetyError):
        replace(profile(), expected_stage_reference="LTA-HL")
    from dataclasses import asdict
    data = asdict(profile())
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps({"schema_version": 1, **data}), encoding="utf-8")
    assert SafetyProfile.from_json(path) == profile()
    data["extra"] = "not accepted"
    path.write_text(json.dumps({"schema_version": 1, **data}), encoding="utf-8")
    with pytest.raises(MotorSafetyError):
        SafetyProfile.from_json(path)


def test_transport_configures_host_only_and_closes_resources(monkeypatch):
    from hhg_control.tc_motor.drivers import smc100
    monkeypatch.setattr(smc100, "_VISA_MANAGERS", {})
    calls = []
    session = SimpleNamespace(flush=lambda mask: calls.append(("flush", mask)),
                              close=lambda: calls.append("close port"))
    manager = SimpleNamespace(open_resource=lambda *a, **kw: session,
                              close=lambda: calls.append("close manager"))
    constants = SimpleNamespace(Parity=SimpleNamespace(none=0), StopBits=SimpleNamespace(one=10),
                                ControlFlow=SimpleNamespace(xon_xoff=1),
                                SerialTermination=SimpleNamespace(none=0),
                                AccessModes=SimpleNamespace(exclusive_lock=1),
                                BufferOperation=SimpleNamespace(discard_read_buffer=1))
    monkeypatch.setitem(sys.modules, "pyvisa", SimpleNamespace(
        ResourceManager=lambda backend: manager, constants=constants))
    transport = VisaSerialTransport("COM21")
    transport.open()
    assert session.baud_rate == 57600 and session.timeout == 300
    assert session.data_bits == 8 and session.flow_control == 1
    assert session.read_termination is None and session.write_termination == "\r\n"
    assert session.end_input == session.end_output == 0
    assert session.xon_char == "\x11" and session.xoff_char == "\x13"
    assert session.discard_null is False
    assert calls == [("flush", 1)]
    transport.close()
    assert calls[-1] == "close port"
    assert "close manager" not in calls


def test_missing_python_dependency_has_exact_setup_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyvisa", None)
    with pytest.raises(MotorError, match="setup_physical_mode.vbs"):
        VisaSerialTransport("COM21").open()


def test_missing_native_runtime_is_distinct_from_missing_python_package(monkeypatch):
    from hhg_control.tc_motor.drivers import smc100

    def unavailable(_backend):
        raise OSError("No VISA library")

    monkeypatch.setattr(smc100, "_VISA_MANAGERS", {})
    monkeypatch.setitem(sys.modules, "pyvisa", SimpleNamespace(
        ResourceManager=unavailable, constants=SimpleNamespace()))
    with pytest.raises(MotorError, match="PyVISA is installed.*NI-VISA"):
        VisaSerialTransport("COM21").open()


@pytest.mark.parametrize("resource,axis", [("", 1), ("TCPIP::anything", 1), ("COM21", 0),
                                         ("COM21", True), ("COM21", 32)])
def test_explicit_serial_resource_and_address_only(resource, axis):
    with pytest.raises(MotorSafetyError):
        SMC100Motor(resource, axis=axis)
