"""Offline checks for the opt-in PCO native-file trial; no camera is opened."""

from pathlib import Path
from types import SimpleNamespace
import json

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


def test_compact_section_4_reference_remains_readable():
    reference = {"status": "EXECUTED", "important_metrics": {
        "capture_only_median_fps": 450.0, "saved_median_fps": 440.0,
        "saved_over_capture_ratio": 440.0 / 450.0,
        "runs": [{"saved": {"synced_fps": 300.0}}],
    }}
    packet = _decision_input({"status": "EXECUTED"}, Settings(), reference)
    assert packet["hdf5_section_4"]["durable_synced_median_fps"] == 300.0


@pytest.mark.parametrize("fail_d_hdf5", [False, True])
def test_one_cell_drive_test_compares_both_formats_with_per_drive_cap(
    tmp_path, monkeypatch, fail_d_hdf5, capsys,
):
    cfg = Settings(expected_serial="12345")
    observed = {}
    prepared = []
    # pytest's tmp_path can live on any runner drive (GitHub's Windows runner
    # puts the checkout on D:). Treat this temporary folder as the simulated
    # C: destination while leaving the D: fixture as an actual Windows path.
    real_drive_id = filemode._drive_id
    monkeypatch.setattr(
        filemode, "_drive_id",
        lambda path: "c:" if Path(path) == tmp_path else real_drive_id(path),
    )
    monkeypatch.setattr(filemode.shutil, "disk_usage", lambda path: SimpleNamespace(free=100_000_000_000))
    monkeypatch.setattr(filemode.lab, "prepare", lambda test_cfg, frames: prepared.append(
        (test_cfg.output_dir, frames)
    ) or test_cfg.output_dir)

    def fake_run(test_cfg, *, reference_report, frames, emit_output):
        observed.setdefault("runs", []).append((test_cfg.output_dir, frames, emit_output))
        raw_bytes = frames[0] * 2560 * 128 * 2
        return {
            "status": "EXECUTED", "output_directory": str(test_cfg.output_dir),
            "free_GB_before_trial": 100.0,
            "runs": [{
                "frames_requested": frames[0], "file": str(test_cfg.output_dir / "trial.pcoraw"),
                "recorder_status": {"dwProcImgCount": frames[0], "dwLastError": 0,
                                    "bFIFOOverflow": False},
                "capture_fps": 400.0,
                "capture_raw_equivalent_MB_per_s": 262.144,
                "capture_and_sync_fps": 390.0,
                "capture_and_sync_raw_equivalent_MB_per_s": 255.5904,
                "file_bytes": raw_bytes + 30_000_000,
                "pixel_readback_status": "UNAVAILABLE",
            }],
        }

    def fake_execute(number, test_cfg):
        assert number == 4
        observed.setdefault("hdf5", []).append((test_cfg.output_dir, test_cfg.speed_frames))
        if fail_d_hdf5 and len(observed["hdf5"]) == 2:
            raise RuntimeError("simulated D: HDF5 failure")
        hdf5_path = tmp_path / f"hdf5_{len(observed['hdf5'])}.h5"
        hdf5_path.write_bytes(b"mock HDF5 payload")
        return {
            "status": "EXECUTED",
            "result": {"runs": [{
                "capture_only": {"total_fps": 410.0},
                "saved": {
                    "file": str(hdf5_path), "frames": test_cfg.speed_frames,
                    "complete": True, "consecutive_ids": True,
                    "total_fps": 390.0, "synced_fps": 380.0,
                    "final_os_fsync_s": 0.1,
                    "pipeline_stage_timing_s": {"write": 1.0},
                },
            }]},
        }

    monkeypatch.setattr(filemode, "run", fake_run)
    monkeypatch.setattr(filemode.lab, "execute", fake_execute)
    monkeypatch.setattr(
        filemode, "_hdf5_run_summary",
        lambda report, *, frame_bytes: {
            "status": "EXECUTED", "file": report["result"]["runs"][0]["saved"]["file"],
            "file_bytes": report["result"]["runs"][0]["saved"]["frames"] * frame_bytes + 10_000_000,
            "frames": report["result"]["runs"][0]["saved"]["frames"],
            "complete": True, "consecutive_frame_ids": True,
            "durable_raw_equivalent_MB_per_s": 249.0368,
        },
    )
    result = run_hard_drive_decision_test(
        cfg,
        c_output_dir=tmp_path,
        d_output_dir=Path("D:/camera_test"),
        max_output_gb_per_drive=30.0,
    )

    expected_frames = (14_000_000_000 // (2560 * 128 * 2))
    assert [frames for _, frames in prepared] == [expected_frames * 2, expected_frames * 2]
    assert len(observed["runs"]) == 2
    assert all(frames == (expected_frames,) and not emit for _, frames, emit in observed["runs"])
    assert len(observed["hdf5"]) == 2
    assert result["comparison"]["status"] == ("PARTIAL" if fail_d_hdf5 else "EXECUTED")
    assert result["comparison"]["comparison"]["pcoraw_D_over_C_durable_rate_ratio"] == 1.0
    if fail_d_hdf5:
        assert result["paste_this"]["D"]["hdf5"] is None
        assert "simulated D: HDF5 failure" in result["paste_this"]["D"]["error"]
        assert result["comparison"]["comparison"]["hdf5_D_over_C_durable_rate_ratio"] is None
        assert list(tmp_path.glob("section_4b_*.json"))
    else:
        assert result["comparison"]["comparison"]["hdf5_D_over_C_durable_rate_ratio"] == 1.0
    assert cfg.source == "mock"
    assert result["settings_used"].source == "physical"
    assert result["settings_used"].output_dir == cfg.output_dir
    assert result["settings_used"].speed_frames == expected_frames
    assert result["paste_this"]["C"]["pcoraw"]["frames_processed"] == expected_frames
    assert result["paste_this"]["C"]["hdf5"]["frames"] == expected_frames
    assert result["paste_this"]["C"]["combined_output_GB"] < 30.0
    assert capsys.readouterr().out == ""
    packet = result["paste_this"]
    assert packet["status"] == result["paste_this"]["status"]
    log_path = Path(packet["execution_log"])
    assert "Both destinations were preflighted" in log_path.read_text(encoding="utf-8")
    assert Path(packet["report_file"]).is_file()
    persisted = json.loads(Path(packet["report_file"]).read_text(encoding="utf-8"))
    assert persisted == packet
    assert "hard_drive_decision_input" not in persisted
    assert Path(packet["report_file"]).parent == tmp_path


def test_drive_id_distinguishes_windows_drive_letters():
    assert filemode._drive_id(Path(r"C:\camera_test")) == "c:"
    assert filemode._drive_id(Path(r"D:\camera_test")) == "d:"


def test_free_space_probe_accepts_new_destination(tmp_path, monkeypatch):
    """A new nested test folder uses its existing ancestor's free space."""
    probes = []
    monkeypatch.setattr(filemode.shutil, "disk_usage", lambda path: (
        probes.append(path) or SimpleNamespace(free=48_000_000_000)
    ))
    destination = tmp_path / "new" / "camera_test"
    assert filemode._destination_free_bytes(destination) == 48_000_000_000
    assert probes == [tmp_path.resolve()]
    assert not destination.exists()


def test_one_cell_drive_test_fills_default_serial_before_camera_use(tmp_path):
    cfg = Settings(expected_serial=None, roi=(0, 0, 1, 1))
    with pytest.raises(ValueError, match="reviewed 2560 x 128 ROI"):
        run_hard_drive_decision_test(cfg, c_output_dir=tmp_path)
