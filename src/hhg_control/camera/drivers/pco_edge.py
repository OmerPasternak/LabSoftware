"""
Concrete driver for the Excelitas pco.edge 5.5 USB camera.
Wraps the official `pco` Python SDK (pco.Camera).
"""

import time
import math
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Tuple, List, Dict, Any
import numpy as np
from .base_camera import BaseCamera, CameraSafetyError, ReadoutMode, TriggerMode
from .camera_defaults import DEFAULT_CAMERA_SERIAL, DISCONNECT_EXPOSURE_S
from ...safe_io import check_stack_memory
from ..payload import CameraPayload


PCO_FIFO_BUFFER_BYTES = 256 * 2**20
PCO_FIFO_MAX_FRAMES = 1024


def _fifo_buffer_frames(
    num_frames: int, height: int, width: int,
    byte_budget: int = PCO_FIFO_BUFFER_BYTES,
) -> int:
    """Return a bounded PCO recorder FIFO size for uint16 camera frames.

    The vendor FIFO requires at least four buffers. The allocation is capped
    by bytes so a large ROI cannot silently reserve several gigabytes.
    """
    frame_bytes = height * width * np.dtype("uint16").itemsize
    frames_by_bytes = max(4, byte_budget // frame_bytes)
    desired = min(PCO_FIFO_MAX_FRAMES, frames_by_bytes, max(4, num_frames))
    return max(4, int(desired))

try:
    import pco as _pco
    pco = _pco
    PCO_AVAILABLE = True
except (ImportError, RuntimeError) as err:
    pco = None
    PCO_AVAILABLE = False
    PCO_IMPORT_ERROR = str(err)


def _frame_time_fields(metadata: Dict[str, Any]) -> tuple[Any, str, str]:
    """Preserve SDK frame time when present; otherwise label host receipt time."""
    timestamp = metadata.get("timestamp")
    if timestamp is not None:
        return timestamp, str(timestamp), "pco_sdk"
    received = datetime.now()
    return received.timestamp(), received.strftime("%H:%M:%S.%f")[:-3], "host_fallback"


class PcoEdgeCamera(BaseCamera):
    """Driver for pco.edge 5.5 sCMOS using the official Excelitas `pco` SDK."""

    def __init__(
        self, interface: str = "USB 3.0", *, serial: str | None = DEFAULT_CAMERA_SERIAL
    ) -> None:
        super().__init__()
        if not PCO_AVAILABLE:
            raise ImportError(
                f"The official 'pco' package or its required C runtime DLLs are missing: {PCO_IMPORT_ERROR}. "
                "Ensure 'pip install pco' is executed and PCO CamWare / USB drivers are installed."
            )
        self._interface_name = interface
        # Treat an omitted or legacy None value as the lab's fixed camera ID.
        self._serial = str(serial or DEFAULT_CAMERA_SERIAL)
        self.state_unknown = False
        self._cam: Any = None
        self._info: Dict[str, Any] = {}
        self._description: Dict[str, Any] = {}

    @property
    def yields_owned_batches(self) -> bool:
        """Return true because ``iter_frames`` yields a new ``np.stack`` buffer."""
        return True

    def connect(self) -> None:
        if self._is_connected:
            return
        if self._cam is not None:
            raise RuntimeError("Camera handle retained after failed cleanup; close it before reconnecting.")
        if not self._serial or not str(self._serial).isdigit():
            raise ValueError(f"Configured camera serial must be numeric, got {self._serial!r}.")
        try:
            # The GUI owns configuration; the vendor's default reset is intentional.
            self._cam = pco.Camera(interface=self._interface_name, serial=int(self._serial))
            description = getattr(self._cam, "description", {})
            self._description = description
            self._info = {
                "model": getattr(self._cam, "camera_name", "pco.edge"),
                "serial_number": str(getattr(self._cam, "camera_serial", "UNKNOWN")),
                "interface": self._interface_name,
                "width": description.get("max_width", 2560),
                "height": description.get("max_height", 2160),
                "bit_depth": 16
            }
            if self._info["serial_number"] != str(int(self._serial)):
                raise RuntimeError("Connected camera serial does not match the requested camera.")
            if "edge 5.5" not in self._info["model"].lower():
                raise RuntimeError("This driver requires a pco.edge 5.5 camera.")
            self.get_readout_mode()
            self.get_trigger_mode()
            self.set_exposure_time(self._exposure_time_s)
            self._roi = self.get_roi()
            self._is_connected = True
            self.state_unknown = False
        except Exception as exc:
            if self._cam is not None:
                try:
                    self._cam.close()
                except Exception as cleanup:
                    self.state_unknown = True
                    raise ConnectionError(f"Connection failed: {exc}; close failed, camera state unknown: {cleanup}") from exc
            self._cam = None
            self._is_connected = False
            raise ConnectionError(f"Failed to connect to PCO camera: {exc}") from exc

    def close(self) -> None:
        """Reset to Rolling Shutter at 100 ms, then release the camera handle.

        Exposure is in seconds. The mode change may reboot the camera for about
        3–5 seconds; failures still attempt to close the SDK handle and report
        an unknown state instead of claiming the reset succeeded.
        """
        if self._cam is not None:
            errors = []
            stopped = False
            try:
                self._cam.stop()
                if getattr(self._cam, "is_recording", False):
                    raise RuntimeError("Camera still reports recording after stop.")
                stopped = True
            except Exception as exc:
                errors.append(f"stop: {exc}")
            if stopped and self._is_connected:
                try:
                    # Apply the mode first so exposure validation uses the
                    # Rolling Shutter limit, including any SDK-specific cap.
                    self.set_readout_mode(ReadoutMode.ROLLING_SHUTTER)
                    self.set_exposure_time(DISCONNECT_EXPOSURE_S)
                    if self.get_readout_mode() != ReadoutMode.ROLLING_SHUTTER:
                        raise RuntimeError("Rolling Shutter readback did not match.")
                    if not math.isclose(
                        self.get_exposure_time(), DISCONNECT_EXPOSURE_S,
                        rel_tol=1e-5, abs_tol=1e-9,
                    ):
                        raise RuntimeError("100 ms exposure readback did not match.")
                except Exception as exc:
                    errors.append(f"disconnect defaults: {exc}")
            try:
                if self._cam is not None:
                    self._cam.close()
            except Exception as exc:
                errors.append(f"close: {exc}")
            else:
                self._cam = None
                self._is_connected = False
                self._live_active = False
            self.state_unknown = bool(errors)
            if errors:
                raise RuntimeError("Camera state unknown after cleanup: " + "; ".join(errors))

    def get_exposure_limits(self) -> tuple[float, float]:
        """Intersect documented model/mode limits with SDK description seconds."""
        minimum, maximum = super().get_exposure_limits()
        if self._cam is not None:
            desc = self._description
            minimum = max(minimum, float(desc.get("min exposure time", minimum)))
            maximum = min(maximum, float(desc.get("max exposure time", maximum)))
        return minimum, maximum

    def set_exposure_time(self, exposure_s: float) -> None:
        """Set and verify seconds within model/mode/SDK limits while idle."""
        if self._readout_mode == ReadoutMode.GLOBAL_SHUTTER and exposure_s > 0.1:
            raise CameraSafetyError("Global Shutter exposure must be at most 0.1 s (100 ms).")
        self.validate_exposure_time(exposure_s)
        if self._cam is not None:
            if getattr(self._cam, "is_recording", False):
                raise RuntimeError("Stop recording before changing exposure.")
            try:
                self._cam.exposure_time = float(exposure_s)
                actual = float(self._cam.exposure_time)
                if not math.isclose(actual, exposure_s, rel_tol=1e-5, abs_tol=1e-9):
                    raise RuntimeError(f"Exposure readback {actual} s differs from request {exposure_s} s.")
            except Exception:
                self.state_unknown = True
                raise
            self._exposure_time_s = actual
        else:
            self._exposure_time_s = float(exposure_s)

    def get_exposure_time(self) -> float:
        if self._cam is not None:
            return float(self._cam.exposure_time)
        return self._exposure_time_s

    def get_trigger_mode(self) -> TriggerMode:
        """Read PCO trigger mode from the SDK; reject modes outside this GUI's contract."""
        if self._cam is None:
            raise RuntimeError("Camera is not connected; trigger mode cannot be verified.")
        raw_mode = self._cam.sdk.get_trigger_mode()["trigger mode"]
        try:
            mode = TriggerMode(raw_mode)
        except ValueError as exc:
            raise RuntimeError(
                f"Unsupported PCO trigger mode {raw_mode!r}; select Auto Sequence or "
                "External Exposure Start in camera settings before using this GUI."
            ) from exc
        self._trigger_mode = mode
        return mode

    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Select Auto Sequence or fixed-duration external exposure starts.

        The PCO SDK's External Exposure Start mode accepts external frame
        pulses; exposure duration remains the separately configured seconds.
        Recording must be idle, and hardware readback must match the request.
        """
        if not isinstance(mode, TriggerMode):
            raise ValueError("mode must be a TriggerMode value.")
        if self._cam is None or not self._is_connected:
            raise RuntimeError("Camera is not connected; cannot change trigger mode.")
        if getattr(self._cam, "is_recording", False) or getattr(self, "_live_active", False):
            raise RuntimeError("Stop camera recording before changing trigger mode.")
        try:
            if self.get_trigger_mode() != mode:
                self._cam.sdk.set_trigger_mode(mode.value)
                self._cam.sdk.arm_camera()
            if self.get_trigger_mode() != mode:
                raise RuntimeError(f"PCO trigger mode readback did not match {mode.value!r}.")
        except Exception:
            self.state_unknown = True
            raise

    def get_sensor_info(self) -> Dict[str, Any]:
        return self._info

    def get_roi_limits(self) -> Dict[str, Any]:
        """Read hardware ROI steps, minimum dimensions, and symmetry from pco SDK."""
        if self._cam is not None:
            desc = self._description
            return {
                "steps": desc.get("roi steps", (4, 1)),
                "minimum": (desc.get("min width", 64), desc.get("min height", 16)),
                "symmetric": (desc.get("roi is horz symmetric", False), desc.get("roi is vert symmetric", False))
            }
        return {"steps": (4, 1), "minimum": (64, 16), "symmetric": (False, False)}

    def get_roi(self) -> tuple[int, int, int, int]:
        """Translate SDK one-based inclusive ROI (x0, y0, x1, y1) to zero-based exclusive bounds."""
        if self._cam is not None:
            cfg = self._cam.configuration
            if "roi" in cfg and cfg["roi"]:
                x0, y0, x1, y1 = cfg["roi"]
                return (x0 - 1, y0 - 1, x1, y1)
        return getattr(self, "_roi", (0, 0, 2560, 2160))

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        """Set hardware readout ROI on camera sensor."""
        self.validate_roi(roi)
        if self._cam is not None:
            if getattr(self._cam, "is_recording", False):
                raise RuntimeError("Stop recording before changing ROI.")
            try:
                x0, y0, x1, y1 = roi
                self._cam.configuration = {"roi": (x0 + 1, y0 + 1, x1, y1)}
                if self.get_roi() != tuple(roi):
                    raise RuntimeError("Camera ROI readback differs from the requested bounds.")
            except Exception:
                self.state_unknown = True
                raise
        self._roi = tuple(roi)

    def get_readout_mode(self) -> ReadoutMode:
        """Query the current shutter mode from the PCO SDK.

        Returns:
            ReadoutMode enum value. Before connection, return the cached mode.
            Once connected, fail if hardware readback cannot be verified.
        """
        if self._cam is None:
            return self._readout_mode
        try:
            result = self._cam.sdk.get_camera_setup()
            if isinstance(result, dict):
                setup_values = result.get("setup", ())
                setup_type = setup_values[0] if setup_values else result.get("type")
            elif isinstance(result, (tuple, list)):
                setup_type = result[0]
            else:
                setup_type = result
            self._readout_mode = ReadoutMode(int(setup_type))
        except Exception as exc:
            raise RuntimeError(f"Could not verify PCO shutter mode: {exc}") from exc
        return self._readout_mode

    def set_readout_mode(self, mode: ReadoutMode) -> None:
        """Switch the sensor readout mode via the PCO SDK.

        This operation requires a full camera firmware reboot.  The method:
          1. Stops any active recording.
          2. Calls ``sdk.set_camera_setup(mode.value)`` (PCO constant 1/2/4).
          3. Closes the camera handle to trigger the internal reset.
          4. Waits ~4 s for the camera to reboot.
          5. Calls ``connect()`` to reopen and reconfigure the camera.

        After this returns the camera is fully operational in the new mode.

        Args:
            mode: ROLLING_SHUTTER (1), GLOBAL_SHUTTER (2), or GLOBAL_RESET (4).

        Raises:
            RuntimeError: Camera not connected, mode switch SDK call failed,
                          or reconnect after reboot failed.
            ValueError:   If ``mode`` is not a ReadoutMode value.
        """
        if not isinstance(mode, ReadoutMode):
            raise ValueError("mode must be a ReadoutMode value.")
        if self._cam is None:
            raise RuntimeError("Camera is not connected — cannot change readout mode.")
        if mode == ReadoutMode.GLOBAL_SHUTTER and self._exposure_time_s > 0.1:
            raise CameraSafetyError("Reduce exposure to at most 0.1 s before Global Shutter.")

        current = self.get_readout_mode()
        if current == mode:
            return   # Already in requested mode; skip reboot

        try:
            # ---- Step 1: stop any active recording ----------------------------
            if getattr(self._cam, "is_recording", False):
                self._cam.stop()

            previous_roi = self.get_roi()
            previous_trigger = self.get_trigger_mode()

            # ---- Step 2: write the new shutter mode and request firmware reboot
            setup_name = {
                ReadoutMode.ROLLING_SHUTTER: "rolling shutter",
                ReadoutMode.GLOBAL_SHUTTER: "global shutter",
                ReadoutMode.GLOBAL_RESET: "global reset",
            }[mode]
            if hasattr(self._cam.sdk, "set_timeouts"):
                self._cam.sdk.set_timeouts(command_timeout=2000)
            self._cam.sdk.set_camera_setup(setup_name)
            self._cam.sdk.reboot_camera()

            # ---- Step 3: close the camera handle to trigger internal reboot ---
            self._cam.close()
            self._cam = None
            self._is_connected = False

        except AttributeError as exc:
            self.state_unknown = True
            raise RuntimeError(
                f"PCO SDK method 'set_camera_setup' not found — verify pco package version ≥ 0.1.3: {exc}"
            ) from exc
        except Exception as exc:
            self.state_unknown = True
            raise RuntimeError(
                f"Failed to configure readout mode {mode.name} (SDK value {mode.value}): {exc}"
            ) from exc

        # ---- Step 4: wait for firmware reboot (~3–5 s typical) ---------------
        time.sleep(4.5)

        # ---- Step 5: reconnect and restore previous settings ------------------
        try:
            self.connect()
            self.set_roi(previous_roi)
            self.set_trigger_mode(previous_trigger)
            actual_setup = self._cam.sdk.get_camera_setup()["setup"][0]
            if int(actual_setup) != mode.value:
                raise RuntimeError(
                    f"Camera reported setup {actual_setup} after reboot; expected {mode.value}."
                )
        except Exception as exc:
            self.state_unknown = True
            raise RuntimeError(
                f"Camera mode switch to {mode.name} was not verified after reboot: {exc}"
            ) from exc

        self._readout_mode = mode

    def acquire_frames(self, num_frames: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Acquire a RAM-checked uint16 stack; long saved runs use iter_frames."""
        if self.state_unknown:
            raise RuntimeError("Camera state unknown; close and reconnect before acquisition.")
        if not self._is_connected or self._cam is None:
            raise RuntimeError("Camera is not connected.")
        if num_frames < 1:
            raise ValueError("num_frames must be >= 1.")
        if getattr(self, "_live_active", False):
            raise RuntimeError("Stop live acquisition before recording a measurement sequence.")

        if self.pending_acquisition_payloads:
            raise RuntimeError("Unpersisted acquisition payloads remain; recover them before recording.")
        raw_images = metadata_list = None
        prepared = None
        roi = self.get_roi()
        check_stack_memory(num_frames, roi[3] - roi[1], roi[2] - roi[0])
        try:
            self._cam.record(number_of_images=num_frames, mode="sequence")
            raw_images, metadata_list = self._cam.images()
            if len(raw_images) != num_frames or len(metadata_list) != num_frames:
                raise RuntimeError("PCO image/metadata count differs from requested frame count.")
            frames, metas, previous = [], [], None
            for frame, raw_meta in zip(raw_images, metadata_list):
                frame, meta = self._validate_frame(frame, raw_meta, roi, previous)
                frames.append(frame)
                metas.append(meta)
                previous = meta["frame_id"]
            stack = np.stack(frames)
            prepared = CameraPayload.snapshot(stack, metas, owned=True, origin="SDK whole stack", frames=len(stack), recording_start=0)
            return stack, metas
        finally:
            # Validation failure: preserve the exact SDK arrays before stop can
            # release them. A stop failure can also prevent a valid return.
            failed = sys.exc_info()[1] is not None
            try:
                if failed and raw_images is not None:
                    self._retain_sdk_stack(raw_images, metadata_list)
            finally:
                try:
                    self._stop_recording()
                except BaseException:
                    if not failed and prepared is not None:
                        self._pending_payloads.retain(prepared)
                    raise

    def _retain_sdk_stack(self, raw_images, metadata):
        """Preserve malformed whole-stack arrays/metadata without casting or repair."""
        if not isinstance(raw_images, (list, tuple)):
            self._pending_payloads.retain(CameraPayload.snapshot(raw_images, metadata,
                origin="SDK rejected whole-stack return", frames=len(raw_images), recording_start=0))
            return
        indexed_metadata = isinstance(metadata, (list, tuple))
        for index, frame in enumerate(raw_images):
            item = metadata[index] if indexed_metadata and index < len(metadata) else None
            self._pending_payloads.retain(CameraPayload.snapshot(frame, item,
                origin="SDK whole-stack validation failure", frames=1, recording_start=index))
        if not indexed_metadata or len(metadata) > len(raw_images):
            extra = metadata[len(raw_images):] if indexed_metadata else metadata
            self._pending_payloads.retain(CameraPayload.snapshot(None, extra,
                origin="SDK unmatched metadata", frames=0))

    def _validate_frame(self, frame, raw_meta, roi, previous_id):
        """Validate uint16 ROI pixels and recorder identity without conversion."""
        frame = np.asarray(frame)
        expected_shape = (roi[3] - roi[1], roi[2] - roi[0])
        if frame.dtype != np.uint16 or frame.shape != expected_shape:
            raise RuntimeError(f"PCO FIFO frame has shape {frame.shape} and dtype {frame.dtype}; "
                               f"expected {expected_shape} and uint16 for ROI {roi}.")
        if not isinstance(raw_meta, dict) or "recorder image number" not in raw_meta:
            raise RuntimeError("PCO FIFO image lacks a recorder frame number.")
        identifier = raw_meta["recorder image number"]
        if isinstance(identifier, (bool, np.bool_)) or not isinstance(identifier, (int, np.integer)):
            raise RuntimeError("PCO recorder frame number must be an integer.")
        frame_id = int(identifier)
        if not 0 <= frame_id < 2**32:
            raise RuntimeError("PCO recorder frame number is outside the uint32 domain.")
        if previous_id is not None and frame_id != (previous_id + 1) % 2**32:
            raise RuntimeError(f"PCO FIFO frame gap: expected {(previous_id + 1) % 2**32}, got {frame_id}.")
        cam_time, time_text, timestamp_source = _frame_time_fields(raw_meta)
        return frame, {
            "frame_id": frame_id, "camera_timestamp": cam_time, "camera_time_str": time_text,
            "timestamp_source": timestamp_source, "host_frame_read_monotonic_ns": time.perf_counter_ns(),
            "raw_meta": str(raw_meta), "exposure_s": self._exposure_time_s, "roi": roi,
        }

    def _stop_recording(self):
        """Verify recorder stop; latch lockout and retain any active acquisition error."""
        acquisition_error = sys.exc_info()[1]
        try:
            self._cam.stop()
            if getattr(self._cam, "is_recording", False):
                raise RuntimeError("Camera still reports recording after stop.")
        except BaseException as cleanup:
            self.state_unknown = True
            raise RuntimeError(f"Acquisition error: {acquisition_error}; camera state unknown after stop: {cleanup}") from cleanup

    def iter_frames(
        self,
        num_frames: int,
        batch_size: int = 4,
        stop_check: Callable[[], bool] | None = None,
    ) -> Iterator[Tuple[np.ndarray, List[Dict[str, Any]]]]:
        """Read one continuous PCO FIFO recording in bounded uint16 batches.

        Frame IDs come from the SDK recorder. A FIFO overflow, recorder error,
        or skipped frame ID fails the step instead of silently saving a gap.
        The FIFO is stopped when iteration ends or its generator is closed.
        """
        if self.state_unknown:
            raise RuntimeError("Camera state unknown; close and reconnect before acquisition.")
        if not self._is_connected or self._cam is None:
            raise RuntimeError("Camera is not connected.")
        if getattr(self, "_live_active", False):
            raise RuntimeError("Stop live acquisition before recording a measurement sequence.")
        if num_frames < 1 or batch_size < 1:
            raise ValueError("num_frames and batch_size must be >= 1.")

        if self.pending_acquisition_payloads:
            raise RuntimeError("Unpersisted acquisition payloads remain; recover them before recording.")
        returned = 0
        unvalidated = None
        previous_id: int | None = None
        # Explicit 4C profiling only; ordinary acquisitions add no timing calls.
        diagnostics = getattr(self, "acquisition_diagnostics", None)
        images: list[np.ndarray] = []
        metas: list[Dict[str, Any]] = []
        try:
            # Size the SDK recorder independently of the Python yield batch.
            # A four-frame batch previously created only 16 FIFO slots, which
            # is about 35 ms at 455 fps and can overflow during a brief host
            # scheduling or memory-copy stall.
            roi = self.get_roi()
            height, width = roi[3] - roi[1], roi[2] - roi[0]
            fifo_size = _fifo_buffer_frames(num_frames, height, width)
            self.last_fifo_capacity_frames = fifo_size
            self.last_fifo_capacity_bytes = fifo_size * height * width * 2
            self._cam.record(number_of_images=fifo_size, mode="fifo")
            # pco.Camera.configuration queries several SDK settings. Read the
            # ROI once for the sequence rather than doing those calls per frame.
            for _ in range(num_frames):
                if stop_check is not None and stop_check():
                    return
                self._cam.wait_for_new_image(
                    delay=True, timeout=max(1.0, self._exposure_time_s + 1.0)
                )
                status = self._cam.rec.get_status()
                if status.get("bFIFOOverflow") or status.get("dwLastError"):
                    raise RuntimeError(
                        "PCO FIFO overflow or recorder error with "
                        f"{fifo_size} allocated frames "
                        f"({self.last_fifo_capacity_bytes / 2**20:.1f} MiB): {status}"
                    )
                frame, raw_meta = self._cam.image(image_index=0)
                unvalidated = (frame, raw_meta, returned)
                returned += 1
                host_frame_read_ns = time.perf_counter_ns()
                handling_started = time.perf_counter() if diagnostics is not None else 0.0
                frame, meta = self._validate_frame(frame, raw_meta, roi, previous_id)
                previous_id = meta["frame_id"]
                meta["host_frame_read_monotonic_ns"] = host_frame_read_ns
                images.append(np.ascontiguousarray(frame))
                metas.append(meta)
                unvalidated = None
                if diagnostics is not None:
                    diagnostics.stage("python_frame_handling", time.perf_counter() - handling_started)
                if len(images) == batch_size:
                    stack_started = time.perf_counter() if diagnostics is not None else 0.0
                    batch = np.stack(images, axis=0)
                    if diagnostics is not None:
                        diagnostics.stage("batch_stack", time.perf_counter() - stack_started)
                    outgoing = metas
                    images, metas = [], []
                    yield batch, outgoing
            if images:
                stack_started = time.perf_counter() if diagnostics is not None else 0.0
                batch = np.stack(images, axis=0)
                if diagnostics is not None:
                    diagnostics.stage("batch_stack", time.perf_counter() - stack_started)
                outgoing = metas
                images, metas = [], []
                yield batch, outgoing
            status = self._cam.rec.get_status()
            if status.get("bFIFOOverflow") or status.get("dwLastError"):
                raise RuntimeError(
                    "PCO FIFO overflow or recorder error with "
                    f"{fifo_size} allocated frames "
                    f"({self.last_fifo_capacity_bytes / 2**20:.1f} MiB): {status}"
                )
        finally:
            # Do not yield from finally: generator.close() must stop immediately.
            # Unreturned local frames instead transfer to the sequencer's
            # explicit incomplete-evidence path after the SDK owner has exited.
            try:
                if images:
                    self._pending_payloads.retain(CameraPayload.snapshot(np.stack(images), metas, owned=True,
                        origin="SDK FIFO unreturned local batch", frames=len(images),
                        recording_start=returned-len(images)-(1 if unvalidated is not None else 0)))
                if unvalidated is not None:
                    frame, raw_meta, ordinal = unvalidated
                    self._pending_payloads.retain(CameraPayload.snapshot(frame, raw_meta,
                        origin="SDK FIFO rejected payload", frames=1, recording_start=ordinal))
            finally:
                self._stop_recording()

    def start_live(self, buffer_size: int = 4) -> None:
        """Start a persistent PCO ring buffer for efficient live display."""
        if self.state_unknown:
            raise RuntimeError("Camera state unknown; close and reconnect before acquisition.")
        if self.pending_acquisition_payloads:
            raise RuntimeError("Unpersisted acquisition payloads remain; recover them before recording.")
        if not self._is_connected or self._cam is None:
            raise RuntimeError("Camera is not connected.")
        if buffer_size < 4:
            raise ValueError("The PCO ring buffer requires at least 4 images.")
        if getattr(self, "_live_active", False):
            return
        self._cam.record(number_of_images=buffer_size, mode="ring buffer")
        self._live_active = True

    def acquire_live_frame(self, timeout_s: float | None = None) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Wait for and copy the newest frame from the PCO ring buffer."""
        if not getattr(self, "_live_active", False) or self._cam is None:
            raise RuntimeError("Live acquisition is not active.")
        timeout = timeout_s if timeout_s is not None else self._exposure_time_s + 1.0
        self._cam.wait_for_new_image(delay=True, timeout=timeout)
        frame, raw_meta = self._cam.image(image_index=0xFFFFFFFF)
        meta = raw_meta if isinstance(raw_meta, dict) else {"raw_meta": str(raw_meta)}
        camera_timestamp, time_text, timestamp_source = _frame_time_fields(meta)
        return np.ascontiguousarray(frame, dtype=np.uint16), {
            "frame_id": meta.get("recorder image number", 0),
            "camera_timestamp": camera_timestamp,
            "camera_time_str": time_text,
            "timestamp_source": timestamp_source,
            "raw_meta": str(meta),
            "exposure_s": self._exposure_time_s,
            "roi": self.get_roi(),
        }

    def stop_live(self) -> None:
        """Stop the PCO recorder if a live ring buffer is active."""
        if self._cam is not None and getattr(self, "_live_active", False):
            self._stop_recording()
        self._live_active = False


