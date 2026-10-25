"""Construct explicit instrument selections for the separate scan worker."""

from dataclasses import dataclass
from pathlib import Path

from hhg_control.camera.drivers.base_camera import ReadoutMode, TriggerMode
from hhg_control.camera.drivers.camera_defaults import DEFAULT_CAMERA_SERIAL
from hhg_control.camera.sequencer.scan_manager import CameraScanManager
from hhg_control.tc_motor.drivers.base import MotorLimits
from hhg_control.tc_motor.sequencer import MotorSequencer
from .sequencer import MotorCameraScan


@dataclass(frozen=True)
class ScanSettings:
    """Visible connection/acquisition choices; exposure seconds and raw sensor ROI pixels."""

    output_directory: str
    mode: str = "Mock"
    motor_resource: str = ""
    motor_axis: int = 1
    motor_profile: str = ""
    camera_serial: str = DEFAULT_CAMERA_SERIAL
    exposure_s: float = .01
    trigger: TriggerMode = TriggerMode.EXTERNAL_EXPOSURE_START
    readout: ReadoutMode = ReadoutMode.ROLLING_SHUTTER
    roi: tuple[int, int, int, int] | None = None
    physical_reviewed: bool = False
    enable_external_input: bool = False


class ScanSession:
    """Own connections for one scan worker, preserving cleanup errors for its close guard."""

    def __init__(self, settings: ScanSettings):
        """Build unconnected drivers; physical access requires explicit reviewed settings."""
        if settings.mode not in ("Mock", "Physical"):
            raise ValueError("Select Mock or Physical explicitly.")
        if not settings.output_directory.strip():
            raise ValueError("Select an output directory.")
        if settings.mode == "Physical":
            if not settings.physical_reviewed or not settings.motor_resource.strip() or not settings.motor_profile.strip():
                raise ValueError("Physical scan requires explicit resource, reviewed safety profile, and apparatus acknowledgement.")
            from hhg_control.tc_motor.drivers.smc100 import SMC100Motor, SafetyProfile
            from hhg_control.camera.drivers.pco_edge import PcoEdgeCamera
            motor = SMC100Motor(settings.motor_resource, axis=settings.motor_axis,
                                profile=SafetyProfile.from_json(settings.motor_profile))
            camera = PcoEdgeCamera(serial=settings.camera_serial)
        else:
            from hhg_control.tc_motor.drivers.mock import MockMotor
            from hhg_control.camera.drivers.mock_camera import MockPcoCamera
            motor = MockMotor(MotorLimits(0, 10, 1, 2, 2))
            camera = MockPcoCamera()
        self.settings = settings
        self.motor = MotorSequencer(motor)
        self.manager = CameraScanManager(camera, Path(settings.output_directory))
        self.scan = MotorCameraScan(self.motor, self.manager)

    def connect(self, cancel_check=lambda: False):
        """Connect on the worker and apply only the explicitly selected camera settings."""
        if cancel_check():
            raise RuntimeError("Stopped before connecting instruments.")
        self.motor.connect()
        if cancel_check():
            raise RuntimeError("Stopped before connecting the camera.")
        if not self.motor.motion_enabled:
            raise RuntimeError("Physical motor inspection did not authorize calibrated motion.")
        self.manager.camera.connect()
        if cancel_check():
            raise RuntimeError("Stopped before applying camera settings.")
        # Reduce exposure first so a selected Global Shutter cannot inherit
        # an incompatible >100 ms exposure from the connection defaults.
        self.manager.set_exposure_time(min(self.settings.exposure_s, .1))
        self.manager.set_readout_mode(self.settings.readout)
        self.manager.set_exposure_time(self.settings.exposure_s)
        if self.settings.roi is not None:
            self.manager.set_roi(self.settings.roi)
        self.manager.set_trigger_mode(self.settings.trigger)
        if self.manager.get_trigger_mode() != self.settings.trigger:
            raise RuntimeError("Camera trigger readback differs from the selected mode.")
        if self.settings.trigger == TriggerMode.EXTERNAL_EXPOSURE_START:
            if self.settings.enable_external_input:
                self.manager.set_external_trigger_input_enabled(True)
            if not self.manager.camera.get_external_trigger_input_enabled():
                raise RuntimeError("External camera trigger input is disabled; configure/review it before scanning.")

    def close(self):
        """Stop/verify motor and safely disconnect camera; preserve both cleanup failures."""
        errors = []
        try:
            self.motor.disconnect()
        except Exception as error:
            errors.append(f"Motor stop/disconnect uncertain: {error}")
        try:
            self.manager.disconnect()
        except Exception as error:
            errors.append(f"Camera disconnect uncertain: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))
