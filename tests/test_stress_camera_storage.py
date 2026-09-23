"""Check that the offline storage benchmark exercises and verifies each format."""

from scripts.stress_camera_storage import CASES, ReplayCamera, run_case


def test_storage_stress_cases_write_expected_frames_and_clean_up(tmp_path):
    camera = ReplayCamera(width=16, height=8, batch_size=3, pattern="noise")
    camera.connect()
    try:
        for case in CASES:
            result = run_case(case, camera, tmp_path, frames=5, batch_size=3)
            assert result["frames"] == 5
            assert result["raw_bytes"] == 5 * 8 * 16 * 2
            assert result["acquire_calls"] == 2
            assert result["elapsed_s"] >= result["acquire_s"] >= 0
            assert result["file_count"] == (5 if case == "raw_per_frame" else 0 if case == "discard" else 1)
            assert not list(tmp_path.iterdir())
    finally:
        camera.close()
