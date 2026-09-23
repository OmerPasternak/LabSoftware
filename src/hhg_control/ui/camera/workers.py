"""Background Qt workers for serialized camera operations."""

from pathlib import Path
from threading import Event
import time

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.drivers.base_camera import BaseCamera, ReadoutMode
from hhg_control.drivers.mock_camera import MockPcoCamera


class CameraConnectTask(QThread):
    """Connect an explicitly selected camera source without blocking the GUI."""

    connected = pyqtSignal(object, bool, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, source: str, exposure_s: float) -> None:
        super().__init__()
        self.source = source
        self.exposure_s = exposure_s

    def run(self) -> None:
        started = time.perf_counter()
        camera: BaseCamera | None = None
        try:
            if self.source == "simulated":
                camera = MockPcoCamera()
                simulated = True
            elif self.source == "physical":
                from hhg_control.drivers.pco_edge import PcoEdgeCamera
                camera = PcoEdgeCamera()
                simulated = False
            else:
                raise ValueError(f"Unknown camera source: {self.source}")
            camera.connect()
            camera.set_exposure_time(self.exposure_s)
            self.connected.emit(camera, simulated, time.perf_counter() - started)
        except Exception as exc:
            if camera is not None:
                try:
                    camera.close()
                except Exception:
                    pass
            self.error_occurred.emit(str(exc))


class CameraModeTask(QThread):
    """Apply a camera readout mode, including any required firmware reboot."""

    mode_applied = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, camera: BaseCamera, mode: ReadoutMode) -> None:
        super().__init__()
        self.camera = camera
        self.mode = mode

    def run(self) -> None:
        try:
            self.camera.set_readout_mode(self.mode)
            self.mode_applied.emit(self.mode)
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
        self._running = False
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
        self._running = True
        min_interval = 1.0 / self.target_fps
        try:
            self.scan_manager.start_live(buffer_size=4)
            while self._running:
                if not self._gui_ready.wait(timeout=0.1):
                    continue
                if not self._running:
                    break
                self._gui_ready.clear()
                started = time.perf_counter()
                timeout_s = self.scan_manager.camera.get_exposure_time() + 1.0
                frame, metadata = self.scan_manager.acquire_live_frame(timeout_s=timeout_s)
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
                if self._running:
                    self.error_occurred.emit(f"Failed to stop live acquisition: {exc}")


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
