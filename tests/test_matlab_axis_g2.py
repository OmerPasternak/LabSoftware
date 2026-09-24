"""Exercise folder-wide MATLAB correlations against direct mock calculations."""

from pathlib import Path
import shutil
import subprocess

import h5py
import numpy as np
import pytest


def _write_mock_step(path, frames, step_index=2):
    """Write a completed schema-2 camera step with synthetic ADU frames."""
    with h5py.File(path, "w") as h5:
        h5.create_dataset("images", data=frames)
        h5.attrs["complete"] = True
        h5.attrs["frames_written"] = len(frames)
        h5.attrs["scan_step_index"] = step_index
        h5.attrs["scan_parameter_name"] = "Delay Stage (mm)"
        h5.attrs["scan_parameter_value"] = 1.5
        h5.attrs["roi_bounds"] = [0, 0, frames.shape[2], frames.shape[1]]
        h5.attrs["exposure_time_s"] = 0.01


def test_matlab_folder_pixel_g2_and_x_correlation(tmp_path):
    matlab = shutil.which("matlab")
    if matlab is None:
        pytest.skip("MATLAB is not installed")

    frames = np.array(
        [
            [[1, 2, 0, 4], [5, 6, 0, 8]],
            [[2, 4, 0, 8], [6, 8, 0, 12]],
            [[3, 5, 0, 9], [7, 9, 0, 13]],
        ],
        dtype=np.uint16,
    )
    good = tmp_path / "one_step"
    good.mkdir()
    _write_mock_step(good / "a.h5", frames[:2])
    _write_mock_step(good / "b.h5", frames[2:])
    # Interrupted acquisitions are excluded by the .h5 file selection.
    (good / "ignored.h5.partial").write_text("incomplete")

    mixed = tmp_path / "mixed_steps"
    mixed.mkdir()
    _write_mock_step(mixed / "a.h5", frames[:2])
    _write_mock_step(mixed / "b.h5", frames[2:], step_index=3)

    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_camera_axis_g2.m"
    auto_project = tmp_path / "auto_project"
    auto_script_dir = auto_project / "scripts"
    auto_data_dir = auto_project / "data"
    auto_script_dir.mkdir(parents=True)
    auto_data_dir.mkdir()
    auto_script = auto_script_dir / script.name
    shutil.copyfile(script, auto_script)
    shutil.copyfile(good / "a.h5", auto_data_dir / "a.h5")
    shutil.copyfile(good / "b.h5", auto_data_dir / "b.h5")

    def matlab_string(path):
        return str(path).replace("'", "''")

    cases = (("all_y", 1, []), ("first_y_binned", 2, [1, 1]))
    commands = ["set(0, 'DefaultFigureVisible', 'off')", f"inputFolder='{matlab_string(good)}'", "backgroundADU=0", "batchFrames=2"]
    for label, width, band in cases:
        range_text = "[]" if not band else f"[{band[0]} {band[1]}]"
        commands.extend(
            [
                f"binWidth={width}",
                f"yRange={range_text}",
                f"run('{matlab_string(script)}')",
                f"writematrix(g2Pixel, '{matlab_string(tmp_path / f'{label}_pixel.csv')}')",
                f"writematrix(pearsonMatrix, '{matlab_string(tmp_path / f'{label}_matrix.csv')}')",
                f"writematrix(g2X, '{matlab_string(tmp_path / f'{label}_g2x.csv')}')",
                f"writematrix(xPixels, '{matlab_string(tmp_path / f'{label}_pixels.csv')}')",
                f"assert(totalFrames==3 && numel(filesAnalyzed)==2)",
                "close all",
            ]
        )
    commands.extend(
        [
            f"inputFolder='{matlab_string(mixed)}'",
            "yRange=[]",
            "try",
            f"run('{matlab_string(script)}')",
            "error('Mixed scan steps were accepted')",
            "catch ME",
            "assert(contains(ME.message, 'metadata differs'))",
            "end",
            "clear inputFolder",
            f"run('{matlab_string(auto_script)}')",
            "assert(totalFrames==3 && numel(filesAnalyzed)==2)",
            "inputFolder=0",
            f"run('{matlab_string(auto_script)}')",
            "assert(totalFrames==3 && numel(filesAnalyzed)==2)",
        ]
    )
    result = subprocess.run(
        [matlab, "-batch", ";".join(commands)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    pixel_mean = frames.mean(axis=0, dtype=float)
    pixel_mean_square = np.square(frames.astype(float)).mean(axis=0)
    expected_pixel = np.full(pixel_mean.shape, np.nan)
    np.divide(pixel_mean_square, pixel_mean**2, out=expected_pixel, where=pixel_mean > 0)
    for label, width, band in cases:
        actual_pixel = np.loadtxt(tmp_path / f"{label}_pixel.csv", delimiter=",")
        np.testing.assert_allclose(actual_pixel, expected_pixel.T, rtol=1e-6, equal_nan=True)

        selected = frames if not band else frames[:, band[0] - 1 : band[1], :]
        profiles = selected.sum(axis=1).astype(float)
        expected_pixels = np.arange(1, frames.shape[2] + 1, dtype=float)
        if width > 1:
            profiles = np.stack(
                [profiles[:, start : start + width].mean(axis=1) for start in range(0, profiles.shape[1], width)],
                axis=1,
            )
            expected_pixels = np.array(
                [expected_pixels[start : start + width].mean() for start in range(0, len(expected_pixels), width)]
            )
        means = profiles.mean(axis=0)
        expected_g2x = np.full(len(means), np.nan)
        np.divide(np.square(profiles).mean(axis=0), means**2, out=expected_g2x, where=means > 0)
        centered = profiles - means
        std = np.sqrt(np.square(centered).mean(axis=0))
        expected_matrix = np.full((len(means), len(means)), np.nan)
        denominator = np.outer(std, std)
        np.divide(centered.T @ centered / len(frames), denominator, out=expected_matrix, where=denominator > 0)
        actual_matrix = np.atleast_2d(np.loadtxt(tmp_path / f"{label}_matrix.csv", delimiter=","))
        actual_g2x = np.atleast_1d(np.loadtxt(tmp_path / f"{label}_g2x.csv", delimiter=","))
        actual_pixels = np.atleast_1d(np.loadtxt(tmp_path / f"{label}_pixels.csv", delimiter=","))
        np.testing.assert_allclose(actual_matrix, expected_matrix, rtol=1e-6, equal_nan=True)
        np.testing.assert_allclose(actual_g2x, expected_g2x, rtol=1e-6, equal_nan=True)
        np.testing.assert_array_equal(actual_pixels, expected_pixels)
