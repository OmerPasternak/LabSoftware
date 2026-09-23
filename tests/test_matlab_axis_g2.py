"""Exercise the MATLAB axis analysis on a small mock camera scan file."""

from pathlib import Path
import shutil
import subprocess

import h5py
import numpy as np
import pytest


def test_matlab_axis_g2_matches_direct_mock_calculation(tmp_path):
    matlab = shutil.which("matlab")
    if matlab is None:
        pytest.skip("MATLAB is not installed")

    frames = np.array(
        [
            [[1, 2, 3, 4], [5, 6, 7, 8]],
            [[2, 4, 6, 8], [6, 8, 10, 12]],
            [[3, 5, 7, 9], [7, 9, 11, 13]],
        ],
        dtype=np.uint16,
    )
    scan_file = tmp_path / "mock_step.h5"
    with h5py.File(scan_file, "w") as h5:
        h5.create_dataset("images", data=frames)
        h5.attrs["complete"] = True
        h5.attrs["frames_written"] = len(frames)

    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_camera_axis_g2.m"

    def matlab_string(path):
        return str(path).replace("'", "''")

    cases = (("x", 1, []), ("y", 1, []), ("x_binned", 2, [1, 1]))
    commands = ["set(0, 'DefaultFigureVisible', 'off')", f"inputFile='{matlab_string(scan_file)}'", "backgroundADU=0"]
    for label, width, band in cases:
        axis = label[0]
        matrix_csv = tmp_path / f"{label}_matrix.csv"
        pixels_csv = tmp_path / f"{label}_pixels.csv"
        range_text = "[]" if not band else f"[{band[0]} {band[1]}]"
        commands.extend(
            [
                f"axisName='{axis}'",
                f"binWidth={width}",
                f"orthogonalRange={range_text}",
                f"run('{matlab_string(script)}')",
                f"writematrix(g2Matrix, '{matlab_string(matrix_csv)}')",
                f"writematrix(axisPixels, '{matlab_string(pixels_csv)}')",
                "close all",
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

    for label, width, band in cases:
        axis = label[0]
        selected = frames if not band else frames[:, band[0] - 1 : band[1], :]
        profiles = selected.mean(axis=1 if axis == "x" else 2).astype(float)
        if width > 1:
            profiles = np.stack(
                [profiles[:, start : start + width].mean(axis=1) for start in range(0, profiles.shape[1], width)],
                axis=1,
            )
        means = profiles.mean(axis=0)
        expected = (profiles.T @ profiles / len(frames)) / np.outer(means, means)
        actual = np.atleast_2d(np.loadtxt(tmp_path / f"{label}_matrix.csv", delimiter=","))
        pixels = np.atleast_1d(np.loadtxt(tmp_path / f"{label}_pixels.csv", delimiter=","))
        np.testing.assert_allclose(actual, expected, rtol=1e-6)
        expected_pixels = np.array(
            [np.arange(start + 1, min(start + width, frames.shape[2 if axis == "x" else 1]) + 1).mean()
             for start in range(0, frames.shape[2 if axis == "x" else 1], width)]
        )
        np.testing.assert_array_equal(pixels, expected_pixels)
