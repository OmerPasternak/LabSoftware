"""Offline checks of the project-owned actuator simulator, never physical motion."""

import pytest

from hhg_control.tc_motor.drivers.base import MotorError, MotorLimits, MotorSafetyError
from hhg_control.tc_motor.drivers.mock import MockMotor


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def limits():
    return MotorLimits(0, 10, 2, 4, 5)


def test_mock_requires_explicit_synthetic_limits(limits):
    with pytest.raises(MotorSafetyError):
        MockMotor(None)
    with pytest.raises(MotorSafetyError):
        MockMotor(limits, initial_position_mm=-1)
    for field in ("velocity_mm_s", "acceleration_mm_s2"):
        for value in (0, -1, float("nan"), float("inf"), True, 100):
            with pytest.raises(MotorSafetyError):
                MockMotor(limits, **{field: value})


def test_connect_identifies_simulation_and_never_zeros(limits):
    motor = MockMotor(limits, initial_position_mm=1.25)
    with pytest.raises(MotorError, match="disconnected"):
        motor.get_status()
    info = motor.connect()
    assert info.simulation and info.units_verified
    assert "MOCK" in info.stage_reference
    assert motor.get_position() == 1.25
    assert motor.connect() is info
    with pytest.raises(MotorSafetyError, match="Homing"):
        motor.home()
    assert motor.get_position() == 1.25


def test_mock_trapezoidal_motion_has_continuous_acceleration_and_exact_target(limits):
    clock = Clock()
    motor = MockMotor(limits, initial_position_mm=1, clock=clock)
    motor.connect()
    motor.move_to(5)
    assert motor.get_status().is_moving
    for when, expected in ((0.25, 1.125), (0.5, 1.5), (1.5, 3.5), (2.25, 4.875)):
        clock.now = when
        assert motor.get_position() == pytest.approx(expected)
        assert motor.is_moving()
    clock.now = 2.5
    status = motor.get_status()
    assert status.position_mm == 5
    assert status.is_ready and not status.is_moving


def test_short_reverse_move_uses_triangular_profile(limits):
    clock = Clock()
    motor = MockMotor(limits, initial_position_mm=1, clock=clock)
    motor.connect()
    motor.move_to(0.75)
    clock.now = 0.25
    assert motor.get_position() == pytest.approx(0.875)
    clock.now = 0.5
    assert motor.get_position() == 0.75
    assert not motor.is_moving()


@pytest.mark.parametrize("target", [-0.001, 10.001, 6, float("nan"), float("inf"), True])
def test_invalid_targets_never_start_motion(limits, target):
    motor = MockMotor(limits)
    motor.connect()
    with pytest.raises(MotorSafetyError):
        motor.move_to(target)
    assert motor.get_position() == 0
    assert not motor.is_moving()


def test_relative_moves_enforce_absolute_and_per_move_limits(limits):
    motor = MockMotor(limits, initial_position_mm=8)
    motor.connect()
    for delta in (3, -6, float("nan")):
        with pytest.raises(MotorSafetyError):
            motor.move_relative(delta)
    assert motor.get_position() == 8


def test_stop_freezes_and_close_preserves_existing_coordinate(limits):
    clock = Clock()
    motor = MockMotor(limits, initial_position_mm=1, clock=clock)
    motor.connect()
    motor.move_to(5)
    with pytest.raises(MotorSafetyError, match="moving"):
        motor.move_to(2)
    clock.now = 1
    motor.stop()
    stopped = motor.get_position()
    clock.now = 100
    assert motor.get_position() == stopped
    assert not motor.is_moving()
    with pytest.raises(MotorSafetyError, match="STOP"):
        motor.move_to(3)
    motor.acknowledge_stop()
    motor.move_to(3)
    motor.close()
    assert not motor.is_connected and not motor.motion_enabled
    motor.connect()
    assert motor.get_position() == stopped
    assert motor.get_status().is_ready


def test_zero_distance_is_immediately_ready(limits):
    motor = MockMotor(limits)
    motor.connect()
    motor.move_to(0)
    assert motor.get_status().is_ready


def test_mock_refuses_nonmonotonic_simulation_clock(limits):
    clock = Clock()
    motor = MockMotor(limits, clock=clock)
    motor.connect()
    motor.move_to(1)
    clock.now = -1
    with pytest.raises(MotorError, match="backwards"):
        motor.get_status()
