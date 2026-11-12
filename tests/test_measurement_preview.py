"""Offline checks for optional measurement display and raw-pixel isolation."""

import threading

import h5py
import numpy as np
import pytest

from hhg_control.camera.drivers.mock_camera import MockPcoCamera
from hhg_control.camera.sequencer.capture_pipeline import BufferedCamera
from hhg_control.camera.sequencer.measurement_preview import MeasurementPreview
from hhg_control.camera.sequencer.scan_manager import CameraScanManager


def test_reader_finishes_without_display_acknowledgement(tmp_path):
    """An unconsumed/locked display slot cannot stall the mock SDK reader."""
    camera = MockPcoCamera()
    camera.connect()
    camera.set_roi((800, 800, 864, 816))
    camera.set_exposure_time(.001)
    preview = MeasurementPreview()
    source = BufferedCamera(camera, 12, 4, 2**20, 0, lambda: None, preview=preview)
    # A GUI copy holding the mailbox lock must never block SDK retrieval.
    preview._lock.acquire()
    try:
        source.reader.start()
        assert source.done.wait(5)
        assert source.error is None
        assert source.retrieved == 12
        assert preview._pending is None
    finally:
        preview._lock.release()
        source.close()
        camera.close()
    assert source.pending.qsize() == 3
    payload = source.pending.get_nowait()
    # Support both the established tuple and the pending payload-ledger update.
    images, metadata = ((payload.images, payload.metadata)
                        if hasattr(payload, "images") else payload)
    original = images.copy()
    original_metadata = [dict(item) for item in metadata]
    preview.offer(images, metadata)
    # A second offer is skipped while a preview is pending.
    preview.offer(np.zeros_like(images), metadata)
    frame, meta = preview.take()
    np.testing.assert_array_equal(frame, original[-1])
    frame[:] = 65535
    meta["roi"] = (0, 0, 1, 1)
    np.testing.assert_array_equal(images, original)
    assert metadata == original_metadata
    assert preview.take() is None


def test_unconsumed_preview_preserves_all_saved_pixels_and_frame_order(tmp_path):
    """A slow/absent display leaves complete source-verified HDF5 files."""
    camera = MockPcoCamera()
    camera.connect()
    camera.set_roi((800, 800, 864, 816))
    camera.set_exposure_time(.001)
    expected = []
    acquire = camera.acquire_frames

    def record(*args, **kwargs):
        images, metadata = acquire(*args, **kwargs)
        expected.append(images.copy())
        return images, metadata

    camera.acquire_frames = record
    preview = MeasurementPreview()
    manager = CameraScanManager(camera, tmp_path)
    try:
        rows = list(manager.execute_scan("preview", "delay", 0, 1, 2, 7,
                                         continuous=True, preview=preview))
        actual = []
        for _, _, path, _ in rows:
            with h5py.File(path) as h5:
                actual.append(h5["images"][:])
                assert h5.attrs["complete"]
        np.testing.assert_array_equal(np.concatenate(actual), np.concatenate(expected))
        assert manager.last_scan_pipeline["files_verified"] == 2
        display, _ = preview.take()
        np.testing.assert_array_equal(display, expected[0][-1])
    finally:
        camera.close()


def test_measurement_refuses_incorrect_hardware_roi_readback(tmp_path, monkeypatch):
    """Automatic ROI application must not hide an actual camera readback error."""
    from hhg_control.gui.camera.workers import ScanSequenceTask
    camera = MockPcoCamera()
    camera.connect()
    manager = CameraScanManager(camera, tmp_path)
    monkeypatch.setattr(manager, "set_roi", lambda *_: None)
    monkeypatch.setattr(camera, "iter_frames", lambda *_a, **_k: pytest.fail("Readback failure started capture"))
    task = ScanSequenceTask(manager, "readback", "delay", 0, 1, 1, 1,
                            expected_roi=(800, 800, 864, 816))
    errors = []
    task.error_occurred.connect(errors.append)
    try:
        task.run()
        assert len(errors) == 1 and "readback" in errors[0]
        assert not list(tmp_path.iterdir())
    finally:
        camera.close()


def test_gui_displays_before_step_finishes_and_zoom_cannot_change_scan(qtbot, tmp_path, monkeypatch):
    """Gate the second batch until a preview is painted; keep acquisition ROI frozen."""
    from hhg_control.gui.camera.camera_panel import CameraMainWindow
    from PyQt6.QtWidgets import QMessageBox

    class GatedMock(MockPcoCamera):
        def __init__(self):
            super().__init__()
            self.second_batch = threading.Event()
            self.release = threading.Event()
            self.calls = 0

        def acquire_frames(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self.second_batch.set()
                if not self.release.wait(5):
                    raise RuntimeError("Test did not release second batch")
            return super().acquire_frames(*args, **kwargs)

    camera = GatedMock()
    camera.connect()
    window = CameraMainWindow()
    qtbot.addWidget(window)
    window.camera = camera
    window.scan_manager = CameraScanManager(camera, tmp_path)
    window.txt_storage_dir.setText(str(tmp_path))
    for spin, value in zip((window.spn_roi_x0, window.spn_roi_y0,
                            window.spn_roi_x1, window.spn_roi_y1), (800, 800, 864, 816)):
        spin.setValue(value)
    window.spn_frames.setValue(12)
    window.spn_num_steps.setValue(1)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        window._toggle_measurement_scan()
        qtbot.waitUntil(camera.second_batch.is_set, timeout=10000)
        qtbot.waitUntil(lambda: window._frame_count > 0, timeout=3000)
        assert window.active_scan_task.isRunning()
        assert not list(tmp_path.glob("*.h5"))
        assert window.btn_apply_roi.isEnabled() and window.btn_full_sensor.isEnabled()
        window.spn_roi_x1.setValue(928)
        window.btn_apply_roi.click()
        assert camera.get_roi() == (800, 800, 864, 816)
        assert window.axis.get_xlim() == (800, 928)
        window.btn_full_sensor.click()
        assert window._entered_roi() == (800, 800, 928, 816)
        assert camera.get_roi() == (800, 800, 864, 816)
        camera.release.set()
        qtbot.waitUntil(lambda: window.active_scan_task is None, timeout=15000)
        assert not warnings
        assert not window._scan_preview_timer.isActive()
        with h5py.File(next(tmp_path.glob("*.h5"))) as h5:
            assert h5["images"].shape == (12, 16, 64)
            assert tuple(h5.attrs["roi_bounds"]) == (800, 800, 864, 816)
    finally:
        camera.release.set()
        if window.active_scan_task is not None:
            window.active_scan_task.request_abort()
            qtbot.waitUntil(lambda: window.active_scan_task is None, timeout=15000)
        window.close()
