"""Offline checks for the opt-in PCO native-file trial; no camera is opened."""

from pathlib import Path

import numpy as np
import pytest

from scripts.pco_filemode_trial import (
    _decision_input,
    _print_decision_input,
    _record_one,
    run,
)
from scripts.lab_computer_acceptance import Settings


class FakeRecorder:
    def __init__(self, frames: int = 0, *, error: int = 0) -> None:
        self.frames = frames
        self.error = error

    def get_status(self) -> dict:
        return {"is running": False, "dwReqImgCount": self.frames,
                "dwProcImgCount": self.frames, "dwLastError": self.error,
                "bFIFOOverflow": False}


class FakePco:
    def __init__(self, *, gap: bool = False, error: int = 0) -> None:
        self.rec = FakeRecorder(error=error)
        self.gap = gap
        self.stopped = False

    def record(self, *, number_of_images: int, mode: str, file_path: str) -> None:
        assert mode == "pcoraw"
        self.rec.frames = number_of_images
        Path(file_path).write_bytes(b"fake native recorder output")

    def stop(self) -> None:
        self.stopped = True

    def image(self, *, image_index: int, data_format: str):
        assert data_format == "Mono16"
        frame_id = image_index + (1 if self.gap and image_index >= 2 else 0)
        return np.full((4, 8), image_index, dtype=np.uint16), {
            "recorder image number": frame_id,
        }


def test_native_file_trial_checks_counts_pixels_and_ids(tmp_path):
    camera = FakePco()
    result = _record_one(camera, tmp_path / "trial.pcoraw", 4, (4, 8), 455.0)
    assert camera.stopped
    assert result["frames_read_back"] == 4
    assert result["consecutive_frame_ids"]
    assert result["recorder_status"]["dwProcImgCount"] == 4
    assert result["capture_fps"] > 0


def test_native_file_trial_rejects_recorder_error_and_frame_gap(tmp_path):
    errored = FakePco(error=1)
    with pytest.raises(RuntimeError, match="recorder reported an error"):
        _record_one(errored, tmp_path / "error.pcoraw", 4, (4, 8), 455.0)
    assert errored.stopped

    gapped = FakePco(gap=True)
    with pytest.raises(RuntimeError, match="frame-ID gap"):
        _record_one(gapped, tmp_path / "gap.pcoraw", 4, (4, 8), 455.0)
    assert gapped.stopped


def test_native_file_trial_requires_explicit_physical_source(tmp_path):
    cfg = Settings(output_dir=tmp_path)
    with pytest.raises(ValueError, match="physical"):
        run(cfg)


def test_decision_input_is_self_contained_and_copyable(capsys):
    cfg = Settings(source="physical", expected_serial="12345", camware_fps=455.0)
    reference = {
        "section": 4, "source": "physical", "status": "EXECUTED",
        "result": {
            "capture_median_fps": 450.0, "saved_median_fps": 320.0,
            "saved_over_capture": 320.0 / 450.0, "camware_fps": 455.0,
            "runs": [
                {"saved": {"synced_fps": 300.0}},
                {"saved": {"synced_fps": 320.0}},
                {"saved": {"synced_fps": 340.0}},
            ],
        },
    }
    report = {
        "status": "EXECUTED", "output_directory": "D:/CameraTests/trial",
        "free_GB_before_trial": 3000.0, "decision": "SHORT_TRIAL_PASS",
        "section_4_comparison": {"direct_file_over_hdf5_synced": 1.4},
        "runs": [{
            "frames_requested": 2000, "frames_read_back": 2000,
            "consecutive_frame_ids": True,
            "recorder_status": {"dwProcImgCount": 2000, "dwLastError": 0,
                                "bFIFOOverflow": False},
            "capture_fps": 455.0, "capture_raw_equivalent_MB_per_s": 298.1888,
            "capture_and_sync_fps": 448.0,
            "capture_and_sync_raw_equivalent_MB_per_s": 293.60128,
            "final_os_fsync_s": 0.02, "file_bytes": 1_310_720_000,
        }],
    }
    packet = _decision_input(report, cfg, reference)
    assert packet["camera"]["raw_rate_required_MB_per_s"] == pytest.approx(298.1888)
    assert packet["camera"]["writer_rate_with_margin_MB_per_s"] == pytest.approx(387.64544)
    assert packet["hdf5_section_4"]["durable_synced_median_fps"] == 320.0
    assert packet["pco_direct_file"]["durable_capture_and_sync_fps"] == 448.0

    _print_decision_input(packet)
    output = capsys.readouterr().out
    assert "COPY FROM HERE — FINAL HARD-DRIVE BOTTLENECK DECISION" in output
    assert '"request":' in output
    assert "END COPY — FINAL HARD-DRIVE BOTTLENECK DECISION" in output
