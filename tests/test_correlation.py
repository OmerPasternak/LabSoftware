"""
Unit tests for quantum optics g^(2) intensity autocorrelation analysis.
"""

import numpy as np
import pytest

from hhg_control.analysis.correlation import compute_g2_map, acquire_and_compute_g2
from hhg_control.drivers.mock_camera import MockPcoCamera


def test_compute_g2_map_constant_intensity():
    """A perfectly constant beam without noise has Var(I) = 0, so g^(2) == 1.0 everywhere."""
    num_frames = 10
    height, width = 50, 60
    frames = np.full((num_frames, height, width), 500, dtype=np.uint16)

    g2_map = compute_g2_map(frames, epsilon=0.0)
    assert g2_map.shape == (height, width)
    assert np.allclose(g2_map, 1.0)


def test_compute_g2_map_zero_intensity_handling():
    """Zero intensity pixels with epsilon=0 should yield NaN without raising an unhandled exception."""
    num_frames = 5
    height, width = 10, 10
    frames = np.zeros((num_frames, height, width), dtype=np.uint16)

    g2_map = compute_g2_map(frames, epsilon=0.0)
    assert np.all(np.isnan(g2_map))

    # With epsilon > 0, denominator is epsilon and numerator is 0, so result is 0.0
    g2_map_eps = compute_g2_map(frames, epsilon=1e-6)
    assert np.allclose(g2_map_eps, 0.0)


def test_compute_g2_map_shape_and_validation():
    """Ensure invalid inputs raise ValueError."""
    with pytest.raises(ValueError, match="Expected a 3D array"):
        compute_g2_map(np.ones((10, 10), dtype=np.uint16))

    with pytest.raises(ValueError, match="At least 2 frames are required"):
        compute_g2_map(np.ones((1, 10, 10), dtype=np.uint16))


def test_acquire_and_compute_g2_mock_camera():
    """Verify acquire_and_compute_g2 works seamlessly with MockPcoCamera."""
    cam = MockPcoCamera(fast_simulation=True)
    cam.connect()

    g2_map, stats = acquire_and_compute_g2(cam, num_frames=10, epsilon=0.0)

    assert g2_map.shape == (MockPcoCamera.HEIGHT, MockPcoCamera.WIDTH)
    assert stats["num_frames"] == 10
    assert stats["peak_intensity"] > 0
    # For a high-intensity Poissonian laser, g^(2) at the beam center should be near 1.0 (within statistical shot noise)
    assert stats["g2_center"] is not None
    assert 0.95 <= stats["g2_center"] <= 1.05

    cam.close()

