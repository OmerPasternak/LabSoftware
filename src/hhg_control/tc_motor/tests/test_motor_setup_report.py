"""Diagnostic reporting preserves unit provenance and never authorizes motion."""

from dataclasses import replace
import json

import pytest

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits
from hhg_control.tc_motor.drivers.mock import MockMotor
from hhg_control.tc_motor.reporting import save_setup_report
from hhg_control.tc_motor.sequencer import MotorSequencer


def test_mock_report_is_explicitly_synthetic_and_stop_remains_latched():
    motor = MockMotor(MotorLimits(0, 10, 1, 2, 2))
    seq = MotorSequencer(motor)
    seq.connect()
    try:
        report = seq.setup_report()
        assert report["identity"]["simulation"] is True
        assert "synthetic" in report["units_note"]
        assert report["move_permitted_now"] is True
        seq.request_stop()
        report = seq.setup_report()
        assert report["stop_latched"] is True
        assert report["move_permitted_now"] is False
        assert report["homing_supported"] is False
        assert motor.get_position() == 0
    finally:
        seq.disconnect()


def test_unreferenced_report_does_not_claim_mm_or_readiness():
    class InspectionMock(MockMotor):
        def connect(self):
            """Provide synthetic, unreviewed physical-like identity without I/O."""
            self.info = replace(super().connect(), simulation=False, units_verified=False)
            self._motion_enabled = False
            self.inspection_settings = {"SU": 0.00003, "SL": 0, "SR": 50}
            return self.info

        def get_status(self):
            """Model a known NOT REFERENCED state with a native position only."""
            return replace(super().get_status(), position_mm=None, native_position=7.25,
                           state="NOT READY", state_code="0A", is_ready=False)

    motor = InspectionMock(MotorLimits(0, 10, 1, 2, 2))
    seq = MotorSequencer(motor)
    seq.connect()
    try:
        report = seq.setup_report()
        assert report["status"]["position_mm"] is None
        assert report["status"]["native_position"] == 7.25
        assert report["native_settings"]["SU"] == 0.00003
        assert report["motion_profile_enabled"] is False
        assert report["move_permitted_now"] is False
        assert "does not report READY" in report["reference_check"]
        assert "does not prove mm" in report["units_note"]
    finally:
        seq.disconnect()


def test_disconnected_report_refused():
    seq = MotorSequencer(MockMotor(MotorLimits(0, 10, 1, 2, 2)))
    with pytest.raises(MotorError):
        seq.setup_report()


def test_report_save_supports_spaces_and_unicode(tmp_path):
    path = tmp_path / "TC motor דוח.json"
    save_setup_report({"report_type": "tc_motor_setup_inspection", "note": "mm unverified"}, path)
    assert json.loads(path.read_text(encoding="utf-8"))["note"] == "mm unverified"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_report_replace_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "existing.json"
    path.write_text("previous report", encoding="utf-8")

    def fail_replace(*_args):
        raise PermissionError("Report destination is locked")

    monkeypatch.setattr("hhg_control.tc_motor.reporting.os.replace", fail_replace)
    with pytest.raises(PermissionError):
        save_setup_report({"status": "new"}, path)
    assert path.read_text(encoding="utf-8") == "previous report"
    assert list(tmp_path.iterdir()) == [path]
