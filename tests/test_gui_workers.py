"""Qt lifecycle tests, enabled when the pytest-qt development extra is installed."""

import pytest

pytest.importorskip("pytestqt")

from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.sequencer.scan_manager import CameraScanManager
from hhg_control.ui.camera.workers import LiveStreamTask


def test_live_worker_stops_cooperatively(qtbot, tmp_path):
    camera = MockPcoCamera(fast_simulation=True)
    camera.connect()
    camera.set_roi((800, 980, 1056, 1180))
    manager = CameraScanManager(camera, tmp_path)
    worker = LiveStreamTask(manager, target_fps=20.0)

    with qtbot.waitSignal(worker.frame_ready, timeout=3000):
        worker.start()
    with qtbot.waitSignal(worker.finished, timeout=3000):
        worker.stop()

    assert manager.state == "IDLE"
    camera.close()
