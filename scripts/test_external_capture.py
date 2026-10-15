"""Capture accepted external triggers without software pacing or pulse division.

Review before using --physical: connection resets camera defaults; configuration
sets exposure, ROI, trigger and shutter mode (a shutter change can reboot it).
Close CamWare/the camera GUI first. Verify the trigger input's electrical limits
against the installed manual. Leave the continuous 1 kHz source running during
capture; missing pulses cause the existing driver's approximately 1 s timeout.
No commands are sent to the pulse source or other lab instruments.

Default: mock only, which does NOT simulate external pulse acceptance/timing.
Run from the repository root with .venv/Scripts/python.exe -m
scripts.test_external_capture --physical --frames 1000 --output-dir D:/CameraTests
Exposure is seconds; ROI is zero-based, upper-exclusive (x0,y0,x1,y1).
External triggering aligns exposure requests to input edges; verify optical pulse
overlap and trigger latency with a scope before claiming laser-shot isolation.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from scripts import lab_computer_acceptance as lab


def run_capture(cfg: lab.Settings, frames: int, max_seconds: float = 30.0) -> dict:
    """Save a finite external-trigger run; retain failure reports/partial images.

    max_seconds bounds acquisition cooperatively, excluding connection/setup and
    cleanup. A blocking SDK wait may exceed it by about one second. No FPS cap is
    imposed. Physical settings are checked by the existing driver and helper.
    """
    if frames < 1 or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("frames and max_seconds must be positive and finite")
    if not math.isfinite(cfg.exposure_s) or cfg.exposure_s <= 0:
        raise ValueError("exposure_s must be positive and finite")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    cfg = replace(cfg, output_dir=Path(cfg.output_dir) / ("external_" + name))
    cfg.output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "FAILED", "source": cfg.source, "settings": asdict(cfg),
        "requested_frames": frames, "max_acquisition_seconds": max_seconds,
        "nominal_source_hz": 1000, "software_fps_cap": None,
        "pulse_accounting_verified": False,
        "timing_note": "Host rates are receipt rates, not exposure timing. "
                       "Recorder IDs cannot detect unaccepted input pulses.",
        "mock_note": "Mock does not wait for external pulses or validate hardware timing.",
    }
    try:
        lab.prepare(cfg, frames)
        with lab.configured_camera(cfg, lab.TriggerMode.EXTERNAL_EXPOSURE_START) as camera:
            print("Starting capture. Keep the external pulse train running; Ctrl+C stops the run.", flush=True)
            deadline = time.perf_counter() + max_seconds
            result = lab.saved_run(
                camera, cfg, "external_trigger", frames,
                abort_check=lambda: time.perf_counter() >= deadline,
            )
            lab.require(result["trigger_mode"] == lab.TriggerMode.EXTERNAL_EXPOSURE_START.value,
                        "Saved trigger mode differs")
            report["result"] = result
        report["status"] = "CAPTURE_VERIFIED" if cfg.source == "physical" else "MOCK_ONLY"
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], flush=True)
    finally:
        report["retained_files"] = [str(p.resolve()) for p in cfg.output_dir.glob("*.h5*")]
        report_path = cfg.output_dir / "report.json"
        report["report_file"] = str(report_path.resolve())
        report_path.write_text(json.dumps(report, indent=2, default=lab.json_default), encoding="utf-8")
    print(f"{report['status']}: {report_path.resolve()}", flush=True)
    if "result" in report:
        result = report["result"]
        print(f"Saved {result['frames']} frames; capture/save rate {result['total_fps']:.1f} fps; "
              f"host read rate {result['host_read_fps']}; consecutive IDs verified.")
    return report


def main(argv: list[str] | None = None) -> int:
    """Parse reviewed settings; physical camera access requires --physical."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--physical", action="store_true", help="Apply settings to the real camera (default: mock)")
    parser.add_argument("--serial", default="61008541")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--exposure-s", type=float, default=0.001)
    parser.add_argument("--roi", type=int, nargs=4, default=(0, 1016, 2560, 1144), metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--output-dir", type=Path, default=lab.ROOT / "data/test_data/external_capture")
    args = parser.parse_args(argv)
    cfg = lab.Settings(
        source="physical" if args.physical else "mock", expected_serial=args.serial if args.physical else None,
        output_dir=args.output_dir, roi=tuple(args.roi), exposure_s=args.exposure_s,
        readout=lab.ReadoutMode.GLOBAL_SHUTTER,
    )
    report = run_capture(cfg, args.frames, args.max_seconds)
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
