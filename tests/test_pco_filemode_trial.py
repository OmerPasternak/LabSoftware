"""Offline checks for the opt-in PCO native-file trial; no camera is opened."""

from pathlib import Path

import numpy as np
import pytest

from scripts.pco_filemode_trial import _record_one, run
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
