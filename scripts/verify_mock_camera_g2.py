"""
Demonstration script to verify Mock Camera data generation and extraction
via quantum optics normalized intensity autocorrelation g^(2) analysis.
"""

import sys
import numpy as np

# Ensure UTF-8 output on Windows consoles with Hebrew or international paths
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hhg_control.drivers.mock_camera import MockPcoCamera
from hhg_control.analysis import acquire_and_compute_g2, compute_g2_map


def main():
    print("=" * 60)
    print("Mock Camera Data Extraction & g^(2) Autocorrelation Analysis")
    print("=" * 60)

    # 1. Initialize and connect to the mock camera
    cam = MockPcoCamera()
    cam.connect()
    cam.set_exposure_time(0.020)  # 20 ms
    info = cam.get_sensor_info()
    print(f"Connected to: {info['model']} (S/N: {info['serial_number']})")
    print(f"Sensor resolution: {info['width']} x {info['height']}")
    print(f"Exposure time: {cam.get_exposure_time() * 1e3:.1f} ms\n")

    # 2. Acquire frames and compute g^(2) map
    num_frames = 30
    print(f"Acquiring {num_frames} frames from mock camera...")
    # Background in mock camera is ~100 ADU pedestal
    g2_map, stats = acquire_and_compute_g2(
        camera=cam,
        num_frames=num_frames,
        epsilon=0.0,
        background=100.0,
    )
    cam.close()
    print("Acquisition complete & camera closed.\n")

    # 3. Display diagnostic summary
    center_y, center_x = cam.HEIGHT // 2, cam.WIDTH // 2
    print("-" * 40)
    print("Analysis Results:")
    print("-" * 40)
    print(f"Peak intensity above pedestal : {stats['peak_intensity'] - 100.0:.1f} ADU")
    print(f"Center pixel coordinates      : (x={center_x}, y={center_y})")
    print(f"g^(2) at beam center (0 delay): {stats['g2_center']:.5f}")
    print(f"Average g^(2) over beam ROI   : {stats['g2_mean_active']:.5f}")
    print(f"Epsilon smoothing parameter   : 0.0")
    print("-" * 40)
    print("Physical interpretation:")
    print("For Poissonian laser shot noise (coherent state), g^(2) ≈ 1.0.")
    print("Values close to 1.0 verify proper intensity scaling and shot-noise statistics.")
    print("=" * 60)


if __name__ == "__main__":
    main()

