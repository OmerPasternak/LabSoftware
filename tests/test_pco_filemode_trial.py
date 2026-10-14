"""Offline checks for the opt-in PCO native-file trial; no camera is opened."""

from pathlib import Path

import numpy as np
import pytest

from scripts import pco_filemode_trial as filemode
from scripts.pco_filemode_trial import (
    _decision_input,
    _print_decision_input,
    _record_one,
    run,
    run_hard_drive_decision_test,
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
        (Path(file_path) / "pco_generated_name.pcoraw").write_bytes(
            b"fake native recorder output"
        )

    def stop(self) -> None:
        self.stopped = True

    def image(self, *, image_index: int, data_format: str):
        assert data_format == "Mono16"
        frame_id = image_index + (1 if self.gap and image_index >= 2 else 0)
        return np.full((4, 8), image_index, dtype=np.uint16), {
            "recorder image number": frame_id,
        }


def test_native_file_trial_checks_counts_pixels_and_ids(tmp_path, monkeypatch):
    # Offline CI deliberately omits the optional physical-camera SDK.
    monkeypatch.setattr(filemode.importlib.metadata, "version", lambda package: "2.2.1-test")
    camera = FakePco()
    result = _record_one(camera, tmp_path / "trial", 4, (4, 8), 455.0)
    assert camera.stopped
    assert result["frames_read_back"] == 4
    assert result["consecutive_frame_ids"]
    assert result["recorder_status"]["dwProcImgCount"] == 4
    assert result["capture_fps"] > 0


def test_native_file_trial_rejects_recorder_error_and_frame_gap(tmp_path):
    errored = FakePco(error=1)
    with pytest.raises(RuntimeError, match="recorder reported an error"):
        _record_one(errored, tmp_path / "error", 4, (4, 8), 455.0)
    assert errored.stopped

    gapped = FakePco(gap=True)
    with pytest.raises(RuntimeError, match="frame-ID gap"):
        _record_one(gapped, tmp_path / "gap", 4, (4, 8), 455.0)
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


def test_one_cell_drive_test_forces_short_physical_c_profile_without_mutating_config(
    tmp_path, monkeypatch,
):
    cfg = Settings(expected_serial="12345")
    observed = {}
    section_4 = {
        "section": 4, "source": "physical", "status": "EXECUTED",
        "settings": {
            "roi": list(cfg.roi), "expected_serial": cfg.expected_serial,
            "readout": cfg.readout, "exposure_s": cfg.exposure_s,
        },
        "result": {},
    }

    def fake_execute(number, test_cfg):
        observed["number"] = number
        observed["section_cfg"] = test_cfg
        return section_4

    def fake_run(test_cfg, *, reference_report, frames):
        observed["direct_cfg"] = test_cfg
        observed["reference"] = reference_report
        observed["frames"] = frames
        return {"hard_drive_decision_input": {"test_status": "EXECUTED"}}

    monkeypatch.setattr(filemode.lab, "execute", fake_execute)
    monkeypatch.setattr(filemode, "run", fake_run)
    result = run_hard_drive_decision_test(cfg, output_dir=tmp_path)

    test_cfg = observed["section_cfg"]
    assert observed["number"] == 4
    assert observed["direct_cfg"] is test_cfg
    assert observed["reference"] is section_4
    assert observed["frames"] == (20, 2000)
    assert test_cfg.source == "physical"
    assert test_cfg.output_dir == tmp_path
    assert (test_cfg.batch_size, test_cfg.speed_frames, test_cfg.repeats) == (16, 2000, 1)
    assert cfg.source == "mock"
    assert cfg.output_dir != tmp_path
    assert result["paste_this"]["test_status"] == "EXECUTED"


def test_one_cell_drive_test_requires_serial_before_camera_use(tmp_path):
    with pytest.raises(ValueError, match="expected_serial"):
        run_hard_drive_decision_test(Settings(), output_dir=tmp_path)
