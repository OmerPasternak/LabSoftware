"""Raw integrated-intensity statistics, persistent selection and mock-file workflow."""

import hashlib
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import h5py
import numpy as np
import pytest

from hhg_control.camera.analysis.harmonic_intensity import (
    compute_harmonic_intensity, make_intensity_window, reference_frame,
)
from scripts.analyze_harmonic_intensity import main


def write_measurement(path, frames, *, roi=(100, 200, 106, 204), step=0):
    """Write a small completed camera-format fixture without instrument access."""
    with h5py.File(path, "w") as source:
        source.create_dataset("images", data=frames)
        source.attrs.update(complete=True, frames_written=len(frames), roi_bounds=roi,
                            trigger_mode="external exposure start", scan_step_index=step)
    return path


@pytest.mark.parametrize("batch_size", [1, 3])
def test_rectangle_sums_pool_every_frame_and_preserve_sources(tmp_path, batch_size):
    frames = np.full((5, 4, 6), 65535, dtype=np.uint16)
    for index in range(5):
        frames[index, 1:3, 2:5] = index * 10 + 50000
    first = write_measurement(tmp_path / "a.h5", frames[:2])
    last = write_measurement(tmp_path / "b.h5", frames[2:], step=12)
    before = [hashlib.sha256(path.read_bytes()).digest() for path in (first, last)]
    window = make_intensity_window((2, 1, 5, 3), first)
    result = compute_harmonic_intensity([tmp_path], window, batch_size=batch_size)
    expected = frames[:, 1:3, 2:5].sum(axis=(1, 2), dtype=np.float64)
    assert result["total_frames"] == 5
    assert result["average_adu"] == pytest.approx(expected.mean())
    assert result["stdev_adu"] == pytest.approx(expected.std(ddof=1))
    assert [hashlib.sha256(path.read_bytes()).digest() for path in (first, last)] == before
    snapshot, metadata = reference_frame(first)
    np.testing.assert_array_equal(snapshot, frames[0])
    assert metadata["trigger_mode"] == window["reference_trigger_mode"]


def test_cli_saves_window_once_and_reuses_it(tmp_path, monkeypatch, capsys):
    source = write_measurement(tmp_path / "measurement.h5", np.arange(72, dtype=np.uint16).reshape(3, 4, 6))
    window_path = tmp_path / "settings" / "window.json"
    monkeypatch.setenv("HHG_INTENSITY_WINDOW", str(window_path))
    assert main([str(source), "--bounds", "1", "1", "4", "3"]) == 0
    first_output = capsys.readouterr().out
    assert len(first_output.splitlines()) == 2
    assert first_output.startswith("Average:") and "STDEV:" in first_output
    before = window_path.read_bytes()
    report = tmp_path / "result.json"
    assert main([str(source), "--output", str(report)]) == 0
    assert capsys.readouterr().out == first_output
    assert window_path.read_bytes() == before
    assert json.loads(report.read_text())["total_frames"] == 3
    assert main([str(source), "--output", str(report)]) == 1
    assert "exists" in capsys.readouterr().err.lower()


def test_invalid_measurements_raise_without_dropping_images(tmp_path):
    source = write_measurement(tmp_path / "measurement.h5", np.ones((2, 4, 6)))
    window = make_intensity_window((1, 1, 4, 3), source)
    with pytest.raises(ValueError, match="more than once"):
        compute_harmonic_intensity([source, source], window)
    with h5py.File(source, "a") as target:
        target.attrs["roi_bounds"] = [101, 200, 107, 204]
    with pytest.raises(ValueError, match="ROI differs"):
        compute_harmonic_intensity([source], window)
    with h5py.File(source, "a") as target:
        target.attrs["roi_bounds"] = [100, 200, 106, 204]
        target["images"][0, 1, 1] = np.nan
    with pytest.raises(ValueError, match="Nonfinite"):
        compute_harmonic_intensity([source], window)
    one = write_measurement(tmp_path / "one.h5", np.ones((1, 4, 6)))
    with pytest.raises(ValueError, match="At least two"):
        compute_harmonic_intensity([one], window)
    with h5py.File(one, "a") as target:
        target.attrs["complete"] = False
    with pytest.raises(ValueError, match="incomplete"):
        compute_harmonic_intensity([one], window)


def test_single_triggered_reference_can_define_window_before_measurement(tmp_path, monkeypatch, capsys):
    reference = write_measurement(tmp_path / "reference.h5", np.ones((1, 4, 6), dtype=np.uint16))
    target = tmp_path / "window.json"
    monkeypatch.setenv("HHG_INTENSITY_WINDOW", str(target))
    assert main(["--reference", str(reference), "--select-only", "--bounds", "1", "0", "5", "4"]) == 0
    window = json.loads(target.read_text())
    assert window["reference_frame_index"] == 0
    assert window["bounds_xyxy"] == [1, 0, 5, 4]
    assert capsys.readouterr().out == ""


def test_mock_triggered_measurement_end_to_end(tmp_path):
    from hhg_control.camera.drivers.mock_camera import MockPcoCamera
    from hhg_control.camera.drivers.base_camera import TriggerMode
    from hhg_control.camera.sequencer.scan_manager import CameraScanManager

    camera = MockPcoCamera()
    camera.connect()
    try:
        camera.set_roi((1200, 1072, 1264, 1088))
        camera.set_exposure_time(0.0005)
        camera.set_trigger_mode(TriggerMode.EXTERNAL_EXPOSURE_START)
        source, _ = CameraScanManager(camera, tmp_path).acquire_and_save_step(
            "intensity_mock", 0, "fixed", 1.0, 4, run_id="intensity-test", batch_size=2)
    finally:
        camera.close()
    window = make_intensity_window((4, 2, 60, 14), source)
    result = compute_harmonic_intensity([source], window)
    with h5py.File(source, "r") as saved:
        signals = saved["images"][:, 2:14, 4:60].sum(axis=(1, 2), dtype=np.float64)
    assert result["average_adu"] == pytest.approx(signals.mean())
    assert result["stdev_adu"] == pytest.approx(signals.std(ddof=1))


def test_interactive_rectangle_is_saved_as_pixel_edges(monkeypatch, qtbot):
    from matplotlib.backend_bases import MouseEvent
    import matplotlib.pyplot as plt
    from PyQt6.QtWidgets import QPushButton
    from hhg_control.gui.camera.intensity_window import choose_intensity_bounds

    def drag_and_accept():
        figure = plt.gcf()
        figure.canvas.draw()
        axis = figure.axes[0]
        for name, position in (("button_press_event", (2.2, 1.2)),
                               ("motion_notify_event", (4.8, 3.6)),
                               ("button_release_event", (4.8, 3.6))):
            x, y = axis.transData.transform(position)
            event = MouseEvent(name, figure.canvas, x, y, button=1)
            figure.canvas.callbacks.process(name, event)
        button = next(b for b in figure.canvas.manager.window.findChildren(QPushButton)
                      if b.text() == "Use this window")
        assert button.isEnabled()
        button.click()

    monkeypatch.setattr("hhg_control.camera.analysis.plot_windows.show_analysis_figures", drag_and_accept)
    assert choose_intensity_bounds(np.zeros((5, 8))) == (2, 1, 5, 4)
