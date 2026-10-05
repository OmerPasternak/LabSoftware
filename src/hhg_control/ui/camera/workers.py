"""Background Qt workers for serialized camera operations."""

from pathlib import Path
from threading import Event
import time

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.drivers.base_camera import BaseCamera, ReadoutMode, TriggerMode
from hhg_control.drivers.mock_camera import MockPcoCamera


class CameraConnectTask(QThread):
    """Connect an explicitly selected camera source without blocking the GUI."""

    connected = pyqtSignal(object, bool, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, source: str, exposure_s: float, *, serial: str | None = None,
                 mode: ReadoutMode | None = None, roi=None,
                 trigger: TriggerMode = TriggerMode.AUTO_SEQUENCE) -> None:
        super().__init__()
        self.source = source
        self.exposure_s = exposure_s
        self.serial, self.mode, self.roi, self.trigger = serial, mode, roi, trigger

    def run(self) -> None:
        started = time.perf_counter()
        camera: BaseCamera | None = None
        try:
            if self.source == "simulated":
                camera = MockPcoCamera()
                simulated = True
            elif self.source == "physical":
                from hhg_control.drivers.pco_edge import PcoEdgeCamera
                camera = PcoEdgeCamera(serial=self.serial)
                simulated = False
            else:
                raise ValueError(f"Unknown camera source: {self.source}")
            camera.connect()
            if self.mode is not None:
                camera.set_readout_mode(self.mode)
            camera.set_exposure_time(self.exposure_s)
            if self.roi is not None:
                camera.set_roi(self.roi)
            camera.set_trigger_mode(self.trigger)
            self.connected.emit(camera, simulated, time.perf_counter() - started)
        except Exception as exc:
            if camera is not None:
                try:
                    camera.close()
                except Exception as cleanup:
                    self.error_occurred.emit(f"{exc}; cleanup failed, camera state unknown: {cleanup}")
                    return
            self.error_occurred.emit(str(exc))


class CameraDisconnectTask(QThread):
    """Close the prior camera off the GUI thread before source selection changes."""

    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, camera: BaseCamera, scan_manager: CameraScanManager | None = None) -> None:
        super().__init__()
        self.camera = camera
        self.scan_manager = scan_manager

    def run(self) -> None:
        try:
            if self.scan_manager is None:
                self.camera.close()
            else:
                self.scan_manager.disconnect()
            if self.camera.is_connected:
                raise RuntimeError("Camera still reports connected after close.")
            self.disconnected.emit()
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CameraModeTask(QThread):
    """Apply a camera readout mode, including any required firmware reboot."""

    mode_applied = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, mode: ReadoutMode) -> None:
        super().__init__()
        self.scan_manager = scan_manager
        self.mode = mode

    def run(self) -> None:
        try:
            self.scan_manager.set_readout_mode(self.mode)
            self.mode_applied.emit(self.mode)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CameraExposureTask(QThread):
    """Apply and read back exposure seconds without blocking the GUI."""

    exposure_applied = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, exposure_s: float) -> None:
        super().__init__()
        self.scan_manager, self.exposure_s = scan_manager, exposure_s

    def run(self) -> None:
        try:
            self.exposure_applied.emit(self.scan_manager.set_exposure_time(self.exposure_s))
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CameraTriggerTask(QThread):
    """Change camera trigger mode after acquisition stops, then verify readback."""

    trigger_applied = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, mode: TriggerMode) -> None:
        super().__init__()
        self.scan_manager = scan_manager
        self.mode = mode

    def run(self) -> None:
        try:
            self.scan_manager.set_trigger_mode(self.mode)
            actual = self.scan_manager.get_trigger_mode()
            if actual != self.mode:
                raise RuntimeError(f"Camera reported trigger mode {actual.value!r} after setting {self.mode.value!r}.")
            self.trigger_applied.emit(actual)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CameraRoiTask(QThread):
    """Apply sensor-pixel ROI after live acquisition has stopped."""

    roi_applied = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, roi: tuple[int, int, int, int]) -> None:
        super().__init__()
        self.scan_manager = scan_manager
        self.roi = roi

    def run(self) -> None:
        try:
            self.scan_manager.set_roi(self.roi)
            self.roi_applied.emit(self.roi)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class PreviewTask(QThread):
    """Acquire one preview frame without blocking the GUI event loop."""

    frame_ready = pyqtSignal(np.ndarray, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager) -> None:
        super().__init__()
        self.scan_manager = scan_manager

    def run(self) -> None:
        try:
            frame, metadata = self.scan_manager.acquire_preview()
            self.frame_ready.emit(frame, metadata)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class LiveStreamTask(QThread):
    """Own a persistent camera ring buffer for the lifetime of live view."""

    frame_ready = pyqtSignal(np.ndarray, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, scan_manager: CameraScanManager, target_fps: float = 20.0) -> None:
        super().__init__()
        self.scan_manager = scan_manager
        self.target_fps = target_fps
        # Set before QThread.start(); STOP may arrive before run() is scheduled.
        self._running = True
        self._gui_ready = Event()
        self._gui_ready.set()

    def acknowledge_frame(self) -> None:
        """Allow the next frame after the GUI handles the current one."""
        self._gui_ready.set()

    def stop(self) -> None:
        """Request a cooperative stop after the active camera read returns."""
        self._running = False
        self._gui_ready.set()

    def run(self) -> None:
        if not self._running:
            return
        min_interval = 1.0 / self.target_fps
        try:
            externally_triggered = (
                self.scan_manager.get_trigger_mode() == TriggerMode.EXTERNAL_EXPOSURE_START
            )
            self.scan_manager.start_live(buffer_size=4)
            while self._running:
                if not self._gui_ready.wait(timeout=0.1):
                    continue
                if not self._running:
                    break
                self._gui_ready.clear()
                started = time.perf_counter()
                timeout_s = 0.25 if externally_triggered else self.scan_manager.camera.get_exposure_time() + 1.0
                try:
                    frame, metadata = self.scan_manager.acquire_live_frame(timeout_s=timeout_s)
                except TimeoutError:
                    if externally_triggered:
                        self._gui_ready.set()
                        continue  # Waiting for a trigger pulse is normal in live view.
                    raise
                if self._running:
                    self.frame_ready.emit(frame, metadata)
                remaining = min_interval - (time.perf_counter() - started)
                if remaining > 0 and self._running:
                    time.sleep(remaining)
        except Exception as exc:
            if self._running:
                self.error_occurred.emit(str(exc))
        finally:
            try:
                self.scan_manager.stop_live()
            except Exception as exc:
                self.error_occurred.emit(f"Failed to stop live acquisition; camera state unknown: {exc}")


class ScanSequenceTask(QThread):
    """Execute and persist a scan in bounded acquisition batches."""

    step_started = pyqtSignal(int, int, float, str)
    step_completed = pyqtSignal(int, int, float, str, np.ndarray, Path)
    scan_finished = pyqtSignal(int)
    scan_aborted = pyqtSignal(int, int, float, str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        scan_mgr: CameraScanManager,
        exp_name: str,
        param_name: str,
        start_val: float,
        step_size: float,
        num_steps: int,
        num_frames: int,
        start_step: int = 0,
        run_id: str | None = None,
        expected_roi=None,
        expected_trigger=None,
    ) -> None:
        super().__init__()
        self.scan_mgr = scan_mgr
        self.exp_name = exp_name
        self.param_name = param_name
        self.start_val = start_val
        self.step_size = step_size
        self.num_steps = num_steps
        self.num_frames = num_frames
        self.start_step = start_step
        self.run_id = run_id
        self.expected_roi, self.expected_trigger = expected_roi, expected_trigger
        self._abort_requested = False
        self._current_step = start_step

    def request_abort(self) -> None:
        """Request a stop at the next safe frame-batch boundary."""
        self._abort_requested = True

    @property
    def is_abort_requested(self) -> bool:
        return self._abort_requested

    def run(self) -> None:
        try:
            if self.expected_roi is not None and self.scan_mgr.get_roi() != self.expected_roi:
                raise RuntimeError("Camera ROI changed since the GUI configuration was verified.")
            if self.expected_trigger is not None and self.scan_mgr.get_trigger_mode() != self.expected_trigger:
                raise RuntimeError("Camera trigger changed since the GUI configuration was verified.")
            def on_start(step: int, value: float) -> None:
                self._current_step = step
                self.step_started.emit(step, self.num_steps, value, self.param_name)

            for step, value, filepath, latest_frame in self.scan_mgr.execute_scan(
                experiment_name=self.exp_name,
                param_name=self.param_name,
                start_value=self.start_val,
                step_size=self.step_size,
                num_steps=self.num_steps,
                num_frames=self.num_frames,
                start_step=self.start_step,
                on_step_start=on_start,
                abort_check=lambda: self._abort_requested,
                run_id=self.run_id,
            ):
                self.step_completed.emit(
                    step, self.num_steps, value, self.param_name, latest_frame, filepath
                )
            if self._abort_requested:
                value = self.start_val + self._current_step * self.step_size
                self.scan_aborted.emit(
                    self._current_step, self.num_steps, value, self.param_name
                )
            else:
                self.scan_finished.emit(self.num_steps)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
