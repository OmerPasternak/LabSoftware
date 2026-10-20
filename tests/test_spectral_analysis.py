"""Tests for spectral covariance and saved harmonic pixel ranges."""

import json
from pathlib import Path
import subprocess
import sys

import h5py
import numpy as np
import pytest

from hhg_control.analysis.spectral import (
    StreamingSpectralCovariance,
    compute_harmonic_g2_from_scan,
    compute_spectral_covariance,
    compute_spectral_covariance_from_scan,
    load_mean_snapshot,
    load_harmonic_regions,
    save_harmonic_regions,
    validate_harmonic_regions,
)


def _write_step(path, frames):
    with h5py.File(path, "w") as h5:
        h5.create_dataset("images", data=frames)
        h5.attrs["complete"] = True
        h5.attrs["frames_written"] = len(frames)
        h5.attrs["scan_step_index"] = 2
        h5.attrs["scan_parameter_name"] = "Delay (mm)"
        h5.attrs["scan_parameter_value"] = 1.5
        h5.attrs["exposure_time_s"] = 0.01
        h5.attrs["roi_bounds"] = [100, 20, 100 + frames.shape[2], 20 + frames.shape[1]]
        h5.attrs["readout_mode"] = "ROLLING_SHUTTER"
        h5.attrs["camera_serial"] = "mock-1"


def test_spectral_covariance_matches_direct_pixel_covariance():
    frames = np.array(
        [
            [[1, 10], [3, 30]],
            [[2, 20], [4, 40]],
            [[4, 10], [2, 20]],
        ],
        dtype=np.uint16,
    )

    covariance, mean = compute_spectral_covariance(frames)
    spectra = frames.sum(axis=1).astype(float)
    np.testing.assert_allclose(covariance, np.cov(spectra, rowvar=False))
    np.testing.assert_allclose(mean, spectra.mean(axis=0))


def test_streaming_covariance_is_independent_of_batch_boundaries():
    spectra = np.array([[4, 40], [6, 60], [6, 30], [3, 50]], dtype=float)
    accumulator = StreamingSpectralCovariance(2)
    accumulator.update(spectra[:2])
    accumulator.update(spectra[2:])
    np.testing.assert_allclose(accumulator.finalize(), np.cov(spectra, rowvar=False))


def test_spectral_covariance_hdf5_directory_streams_matching_step_files(tmp_path):
    first = np.array([[[1, 10], [3, 30]], [[2, 20], [4, 40]]], dtype=np.uint16)
    second = np.array([[[4, 10], [2, 20]]], dtype=np.uint16)
    _write_step(tmp_path / "repeat_a.h5", first)
    _write_step(tmp_path / "repeat_b.h5", second)

    covariance, mean, summary = compute_spectral_covariance_from_scan(tmp_path, batch_size=1)
    spectra = np.concatenate((first, second)).sum(axis=1).astype(float)
    np.testing.assert_allclose(covariance, np.cov(spectra, rowvar=False))
    np.testing.assert_allclose(mean, spectra.mean(axis=0))
    assert summary["total_frames"] == 3
    assert len(summary["source_files"]) == 2
    np.testing.assert_array_equal(summary["x_pixel_indices"], [100, 101])


def test_spectral_covariance_rejects_files_from_different_scan_steps(tmp_path):
    frames = np.ones((2, 2, 3), dtype=np.uint16)
    _write_step(tmp_path / "step_a.h5", frames)
    _write_step(tmp_path / "step_b.h5", frames)
    with h5py.File(tmp_path / "step_b.h5", "a") as h5:
        h5.attrs["scan_step_index"] = 3

    with pytest.raises(ValueError, match="scan_step_index"):
        compute_spectral_covariance_from_scan(tmp_path)


def test_covariance_cli_writes_hdf5_matrix_and_metadata(tmp_path):
    frames = np.array(
        [[[1, 5, 2], [3, 8, 4]], [[2, 6, 3], [5, 9, 7]]],
        dtype=np.uint16,
    )
    source = tmp_path / "step.h5"
    output = tmp_path / "covariance.h5"
    _write_step(source, frames)
    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_spectral_covariance.py"

    result = subprocess.run(
        [sys.executable, str(script), str(source), "--output", str(output), "--no-plot"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with h5py.File(output, "r") as h5:
        covariance = h5["frequency_pixel_covariance_adu2"][:]
        assert covariance.shape == (3, 3)
        assert h5.attrs["total_frames"] == 2
        assert h5["frequency_pixel_covariance_adu2"].attrs["units"] == "ADU^2"


def test_mean_snapshot_and_saved_harmonic_ranges(tmp_path):
    frames = np.array([[[1, 2, 3], [4, 5, 6]], [[3, 4, 5], [6, 7, 8]]], dtype=np.uint16)
    source = tmp_path / "step.h5"
    _write_step(source, frames)

    snapshot, metadata = load_mean_snapshot(source)
    np.testing.assert_allclose(snapshot, frames.mean(axis=0))
    assert metadata["snapshot_frame_count"] == 2

    output = save_harmonic_regions(
        tmp_path / "step_harmonics.json",
        [{"label": "H3", "x_start": 2, "x_stop": 3}, {"label": "H1", "x_start": 0, "x_stop": 1}],
        source,
        snapshot.shape,
        roi_bounds=metadata["roi_bounds"],
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["range_convention"].startswith("zero-based, half-open")
    assert payload["harmonics"] == [
        {"label": "H1", "x_start": 0, "x_stop": 1},
        {"label": "H3", "x_start": 2, "x_stop": 3},
    ]
    loaded = load_harmonic_regions(output, image_shape_yx=snapshot.shape, roi_bounds=metadata["roi_bounds"])
    assert loaded["harmonics"] == payload["harmonics"]
    with pytest.raises(ValueError, match="image shape"):
        load_harmonic_regions(output, image_shape_yx=(3, 3))
    with pytest.raises(FileExistsError):
        save_harmonic_regions(output, [{"label": "H1", "x_start": 0, "x_stop": 1}], source, snapshot.shape)
    empty_existing = tmp_path / "empty_existing.json"
    empty_existing.write_text("", encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_harmonic_regions(
            empty_existing, [{"label": "H1", "x_start": 0, "x_stop": 1}], source, snapshot.shape
        )
    assert empty_existing.exists() and empty_existing.stat().st_size == 0


def test_harmonic_range_validation_rejects_overlaps_and_out_of_bounds():
    with pytest.raises(ValueError, match="overlap"):
        validate_harmonic_regions(
            [
                {"label": "H1", "x_start": 0, "x_stop": 4},
                {"label": "H2", "x_start": 3, "x_stop": 6},
            ],
            width=8,
        )
    with pytest.raises(ValueError, match="0 <= x_start"):
        validate_harmonic_regions([{"label": "H1", "x_start": 0, "x_stop": 9}], width=8)


def test_per_harmonic_g2_integrates_each_marked_band_and_reports_uncertainty(tmp_path):
    frames = np.array(
        [
            [[1, 2, 5], [1, 2, 5]],
            [[2, 4, 5], [2, 4, 5]],
            [[3, 6, 5], [3, 6, 5]],
            [[4, 8, 5], [4, 8, 5]],
        ], dtype=np.uint16,
    )
    source = tmp_path / "step.h5"
    _write_step(source, frames)
    map_path = save_harmonic_regions(
        tmp_path / "reference_harmonics.json",
        [{"label": "H1", "x_start": 0, "x_stop": 1}, {"label": "H2", "x_start": 1, "x_stop": 2}],
        source,
        frames.shape[1:],
        roi_bounds=[100, 20, 103, 22],
    )

    result = compute_harmonic_g2_from_scan(source, map_path, batch_size=2)
    assert result["total_frames"] == 4
    assert result["y_range_local_half_open"] == [0, 2]
    h1, h2 = result["harmonics"]
    assert h1["mean_signal_adu"] == 5.0
    assert h1["g2_zero_delay"] == pytest.approx(np.mean(np.array([2, 4, 6, 8]) ** 2) / 5.0**2)
    assert h2["g2_zero_delay"] == pytest.approx(np.mean(np.array([4, 8, 12, 16]) ** 2) / 10.0**2)
    assert h1["standard_error"] >= 0
    assert h1["ci95"][0] <= h1["g2_zero_delay"] <= h1["ci95"][1]


def test_per_harmonic_g2_rejects_roi_mismatch(tmp_path):
    frames = np.ones((3, 2, 3), dtype=np.uint16)
    source = tmp_path / "step.h5"
    _write_step(source, frames)
    map_path = save_harmonic_regions(
        tmp_path / "map.json", [{"label": "H1", "x_start": 0, "x_stop": 1}],
        source, frames.shape[1:], roi_bounds=[0, 0, 3, 2],
    )
    with pytest.raises(ValueError, match="ROI"):
        compute_harmonic_g2_from_scan(source, map_path)


def test_harmonic_g2_cli_writes_separate_report_table_and_plot(tmp_path):
    frames = np.array([[[1, 4], [1, 4]], [[2, 6], [2, 6]], [[3, 8], [3, 8]]], dtype=np.uint16)
    source = tmp_path / "step.h5"
    _write_step(source, frames)
    map_path = save_harmonic_regions(
        tmp_path / "map_harmonics.json", [{"label": "H1", "x_start": 0, "x_stop": 1}],
        source, frames.shape[1:], roi_bounds=[100, 20, 102, 22],
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_harmonic_g2.py"
    result = subprocess.run(
        [sys.executable, str(script), str(source), str(map_path), "--raw-intensity", "--no-show"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output_dir = tmp_path / "analysis"
    report = next(output_dir.glob("*.json"))
    table = next(output_dir.glob("*.csv"))
    plot = next(output_dir.glob("*.png"))
    assert json.loads(report.read_text(encoding="utf-8"))["results"][0]["total_frames"] == 3
    assert "g2_zero_delay" in table.read_text(encoding="utf-8")
    assert plot.stat().st_size > 0
    assert source.exists() and map_path.exists()
