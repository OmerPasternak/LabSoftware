"""Deterministic mock-only motion, cancellation, and scan integration checks."""

from dataclasses import replace
import threading

import pytest

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits, MotorSafetyError
from hhg_control.tc_motor.drivers.mock import MockMotor
from hhg_control.tc_motor.sequencer import MotionAborted, MotionTimeout, MotorSequencer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class AdvancingEvent(threading.Event):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.on_wait = None

    def wait(self, timeout=None):
        if not self.is_set():
            self.clock.now += timeout or 0
            if self.on_wait:
                self.on_wait()
        return self.is_set()


class RecordingMotor(MockMotor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.moves = []
        self.stops = 0

    def move_to(self, target_mm):
        super().move_to(target_mm)
        self.moves.append(target_mm)

    def stop(self):
        self.stops += 1
        super().stop()


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("hhg_control.tc_motor.sequencer.time.monotonic", clock)
    motor = RecordingMotor(MotorLimits(0, 10, 2, 4, 2), initial_position_mm=1, clock=clock)
    seq = MotorSequencer(motor, poll_interval_s=0.05, stop_timeout_s=0.2)
    seq._stop_event = AdvancingEvent(clock)
    seq._pause_event = AdvancingEvent(clock)
    seq.connect()
    return clock, motor, seq


def test_move_arrival_then_stable_settle_and_relative_mm(rig):
    clock, motor, seq = rig
    updates = []
    status = seq.move_to(2, settle_s=0.15, on_status=updates.append)
    assert status.position_mm == 2 and status.is_ready
    assert any(update.is_moving for update in updates)
    assert sum(update.is_ready for update in updates) >= 3
    assert clock.now >= 1.15
    assert seq.move_relative(-0.5).position_mm == 1.5
    assert motor.moves == [2, 1.5]


@pytest.mark.parametrize("timeout, settle", [(0, 0), (-1, 0), (float("nan"), 0), (1, -1), (1, float("inf"))])
def test_invalid_timing_rejected_before_movement(rig, timeout, settle):
    _, motor, seq = rig
    with pytest.raises(MotorSafetyError):
        seq.move_to(2, timeout_s=timeout, settle_s=settle)
    assert motor.moves == []


@pytest.mark.parametrize("target", [float("nan"), float("inf"), -1, 11, True])
def test_invalid_target_does_not_dispatch(rig, target):
    _, motor, seq = rig
    with pytest.raises(MotorSafetyError):
        seq.move_to(target)
    assert motor.moves == []


def test_stop_request_before_pending_move_is_never_cleared(rig):
    _, motor, seq = rig
    seq.request_stop()
    with pytest.raises(MotionAborted):
        seq.move_to(2)
    assert not motor.moves
    assert motor.stops == 1 and seq.stop_requested
    with pytest.raises(MotionAborted):
        seq.move_to(2)
    seq.acknowledge_stop()
    assert seq.move_to(2).position_mm == 2


def test_stop_request_during_delayed_connect_is_preserved(rig):
    _, motor, seq = rig
    seq.disconnect()
    seq.request_stop()
    seq.connect()
    with pytest.raises(MotionAborted):
        seq.move_to(2)
    assert motor.moves == []


def test_external_cancellation_stops_and_latches(rig):
    _, motor, seq = rig
    cancel = threading.Event()
    seq._stop_event.on_wait = cancel.set
    with pytest.raises(MotionAborted):
        seq.move_to(2, cancel_event=cancel)
    assert motor.stops == 1
    assert not motor.is_moving() and seq.stop_requested


def test_settling_is_abortable(rig):
    _, motor, seq = rig
    def progress(status):
        if status.is_ready:
            seq.request_stop()
    with pytest.raises(MotionAborted):
        seq.move_to(2, settle_s=1, on_status=progress)
    assert motor.stops == 1 and not motor.is_moving()


def test_timeout_stops_motion_and_does_not_claim_arrival(rig):
    _, motor, seq = rig
    with pytest.raises(MotionTimeout):
        seq.move_to(2, timeout_s=0.1)
    assert motor.get_position() < 2
    assert motor.stops == 1 and not motor.is_moving()
    assert seq.stop_requested


def test_fault_readback_stops_and_retains_fault(rig, monkeypatch):
    _, motor, seq = rig
    original = motor.get_status
    def status():
        result = original()
        return replace(result, error_code=0x20) if motor.moves else result
    monkeypatch.setattr(motor, "get_status", status)
    with pytest.raises(MotorError, match="0x20"):
        seq.move_to(2)
    assert motor.stops == 1 and not original().is_moving


def test_callback_failure_retains_original_and_reports_stop_uncertainty(rig, monkeypatch):
    _, motor, seq = rig
    problem = ValueError("measurement display failed")
    def broken_stop():
        raise MotorError("serial link lost")
    def progress(_):
        raise problem
    monkeypatch.setattr(motor, "stop", broken_stop)
    with pytest.raises(ValueError, match="measurement display failed.*",) as caught:
        seq.move_to(2, on_status=progress)
    assert caught.value is problem
    assert "STOP/cleanup uncertain" in str(caught.value)
    assert "serial link lost" in str(caught.value)
    assert seq.is_connected


def test_stop_verification_is_bounded_when_controller_stays_moving(rig, monkeypatch):
    _, motor, seq = rig
    motor.move_to(2)
    original = motor.get_status
    monkeypatch.setattr(motor, "stop", lambda: None)
    monkeypatch.setattr(motor, "get_status", lambda: replace(original(), is_moving=True, is_ready=False))
    with pytest.raises(MotionTimeout, match="STOP verification"):
        seq.stop()
    assert seq.is_connected and seq.stop_requested


def test_stop_acknowledgement_requires_idle_controller(rig):
    _, motor, seq = rig
    motor.move_to(2)
    seq.request_stop()
    with pytest.raises(MotorSafetyError, match="moving"):
        seq.acknowledge_stop()
    assert seq.stop_requested


def test_concurrent_operation_is_rejected_and_request_stop_only_sets_flag(rig):
    _, motor, seq = rig
    observed = []
    def progress(_):
        with pytest.raises(MotorError, match="Another motor operation"):
            seq.snapshot()
        before = motor.stops
        seq.request_stop()
        observed.append((before, motor.stops))
    with pytest.raises(MotionAborted):
        seq.move_to(2, on_status=progress)
    assert observed == [(0, 0)]
    assert motor.stops == 1


def test_scan_prevalidates_all_targets_and_all_steps_before_first_move(rig):
    _, motor, seq = rig
    measured = []
    with pytest.raises(MotorSafetyError):
        seq.scan([2, 11], measured.append)
    assert motor.moves == [] and measured == []
    with pytest.raises(MotorSafetyError, match="per-move"):
        seq.scan([2, 5], measured.append)
    assert motor.moves == [] and measured == []


def test_scan_preserves_order_readbacks_and_unchanged_results(rig):
    _, motor, seq = rig
    observed = []
    results = [object(), object(), object()]
    def measure(status):
        assert status.is_ready and not status.is_moving
        observed.append(status.position_mm)
        return results[len(observed) - 1]
    points = seq.scan([1.1, 1.2, 1.0], measure, settle_s=0.1)
    assert motor.moves == [1.1, 1.2, 1.0]
    assert observed == [1.1, 1.2, 1.0]
    assert [point.measurement for point in points] == results
    assert [point.status.position_mm for point in points] == observed


def test_scan_callback_abort_prevents_later_moves(rig):
    _, motor, seq = rig
    def measure(status):
        seq.request_stop()
        return status.position_mm
    with pytest.raises(MotionAborted) as caught:
        seq.scan([1.1, 1.2], measure)
    assert motor.moves == [1.1]
    assert motor.stops == 1
    assert len(caught.value.partial_results) == 1
    assert caught.value.partial_results[0].measurement == 1.1


def test_disconnect_stop_failure_leaves_connection_owned(rig, monkeypatch):
    _, motor, seq = rig
    def broken_stop():
        raise MotorError("unverified physical stop")
    monkeypatch.setattr(motor, "stop", broken_stop)
    with pytest.raises(MotorError, match="unverified physical stop"):
        seq.disconnect()
    assert seq.is_connected and motor.is_connected


def test_successful_disconnect_closes_and_retains_stop_latch(rig):
    _, motor, seq = rig
    seq.disconnect()
    assert not seq.is_connected and not motor.motion_enabled
    assert seq.stop_requested


def test_inspection_without_mm_units_can_be_read_but_never_moved(rig, monkeypatch):
    _, motor, seq = rig
    original = motor.get_status
    monkeypatch.setattr(motor, "get_status", lambda: replace(original(), position_mm=None))
    assert seq.snapshot().position_mm is None
    with pytest.raises(MotorSafetyError, match="millimetre"):
        seq.move_to(2)
    assert motor.moves == []


def test_inspection_settings_are_a_copy_and_do_not_query(rig, monkeypatch):
    _, motor, seq = rig
    motor.inspection_settings = {"velocity_mm_s": 1.0}
    def forbidden():
        raise AssertionError("Cached inspection must not query hardware")
    monkeypatch.setattr(motor, "get_status", forbidden)
    settings = seq.inspection_settings
    assert settings == {"velocity_mm_s": 1.0}
    settings["velocity_mm_s"] = 99
    assert motor.inspection_settings == {"velocity_mm_s": 1.0}


def test_new_stop_during_acknowledgement_is_not_lost(rig, monkeypatch):
    _, motor, seq = rig
    seq.request_stop()
    entered = threading.Event()
    release = threading.Event()
    original = motor.get_status
    def gated_status():
        entered.set()
        if not release.wait(2):
            raise AssertionError("Test did not release status query")
        return original()
    monkeypatch.setattr(motor, "get_status", gated_status)
    errors = []
    def acknowledge():
        try:
            seq.acknowledge_stop()
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=acknowledge)
    thread.start()
    try:
        assert entered.wait(2)
        seq.request_stop()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MotionAborted)
    assert seq.stop_requested and motor.stop_requested


def test_stop_while_driver_is_preparing_move_prevents_dispatch(rig, monkeypatch):
    _, motor, seq = rig
    entered = threading.Event()
    release = threading.Event()
    original = motor.move_to
    def gated_move(target):
        entered.set()
        if not release.wait(2):
            raise AssertionError("Test did not release motion preflight")
        original(target)
    monkeypatch.setattr(motor, "move_to", gated_move)
    errors = []
    def move():
        try:
            seq.move_to(2)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=move)
    thread.start()
    try:
        assert entered.wait(2)
        seq.request_stop()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MotorSafetyError)
    assert motor.moves == []
    assert motor.stops == 1
    assert seq.stop_requested and motor.stop_requested


def test_deadline_expiring_during_motion_preflight_prevents_dispatch(rig, monkeypatch):
    clock, motor, seq = rig
    original = motor.move_to
    def slow_preflight(target):
        clock.now += 1.0
        original(target)
    monkeypatch.setattr(motor, "move_to", slow_preflight)
    with pytest.raises(MotionTimeout, match="before motion dispatch"):
        seq.move_to(2, timeout_s=0.5)
    assert motor.moves == []
    assert motor.get_position() == 1
    assert motor.stops == 1
    assert motor._motion_guard_check is None


def test_external_cancel_during_motion_preflight_prevents_dispatch(rig, monkeypatch):
    _, motor, seq = rig
    cancel = threading.Event()
    original = motor.move_to
    def cancelled_preflight(target):
        cancel.set()
        original(target)
    monkeypatch.setattr(motor, "move_to", cancelled_preflight)
    with pytest.raises(MotionAborted):
        seq.move_to(2, cancel_event=cancel)
    assert motor.moves == []
    assert motor.get_position() == 1
    assert motor.stops == 1
    assert motor._motion_guard_check is None


def test_deadline_counts_initial_status_queries_before_preflight(rig, monkeypatch):
    clock, motor, seq = rig
    original = motor.get_status
    reads = []
    def slow_status():
        if not reads:
            clock.now += 1
        reads.append(True)
        return original()
    monkeypatch.setattr(motor, "get_status", slow_status)
    with pytest.raises(MotionTimeout):
        seq.move_to(2, timeout_s=0.5)
    assert motor.moves == []


def test_motion_guard_restores_nested_check_even_after_exception(rig):
    _, motor, _ = rig
    checks = []
    with motor.motion_guard(lambda: checks.append("outer")):
        motor.check_motion_guard()
        with pytest.raises(ValueError):
            with motor.motion_guard(lambda: checks.append("inner")):
                motor.check_motion_guard()
                raise ValueError("preflight failed")
        motor.check_motion_guard()
    motor.check_motion_guard()
    assert checks == ["outer", "inner", "outer"]
    assert motor._motion_guard_check is None


def test_motion_guard_rejects_another_io_thread(rig):
    _, motor, _ = rig
    errors = []
    def check_from_another_thread():
        try:
            motor.check_motion_guard()
        except Exception as error:
            errors.append(error)
    with motor.motion_guard(lambda: None):
        thread = threading.Thread(target=check_from_another_thread)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MotorSafetyError)
