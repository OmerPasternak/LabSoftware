"""Bounded, query-only SMC100 link check; physical access requires --physical.

Uses the same transport as the GUI. Never sends PA, ST, home, reset, enable,
zero, or setting writes. TS clears the controller's status error register; all
received frames and errors are retained in the report. Motion stays untested.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import math
import platform
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

from .drivers.base import MotorError
from .drivers.smc100 import SMC100Motor, VisaSerialTransport
from .drivers.direct_serial import DirectSerialTransport
from .drivers.labview_reference import TRACE_STATUS_POLLS, LabviewReferenceTransport
from .reporting import save_setup_report
from .windows_serial_evidence import WindowsEvidenceSerial, host_evidence


MAX_DURATION_S = 600


def inspect_serial_link(transport, *, axis: int = 1, duration_s: float = 30.0,
                        interval_s: float = 0.5, gui_resource: str | None = None,
                        labview_reference: bool = False) -> dict:
    """Inspect one explicit axis for up to duration_s seconds, with no retries.

    Native positions/settings are uncalibrated controller units. Always close
    only the host port, including on error; this does not stop or verify motion.
    Passing proves only successful replies during this observation interval.
    Injectable transport lets offline tests cover the identical query sequence.
    """
    if type(axis) is not int or not 1 <= axis <= 31:
        raise ValueError("Controller address must be 1–31.")
    if not 1 <= duration_s <= MAX_DURATION_S or not 0.05 <= interval_s <= 5:
        raise ValueError(f"Use 1–{MAX_DURATION_S} seconds and a 0.05–5 second polling interval.")
    if gui_resource and labview_reference:
        raise ValueError("Choose one inspection query sequence.")
    sequence = "link_check"
    if gui_resource:
        sequence = "gui_inspection"
    elif labview_reference:
        sequence = "labview_reference"
    report = {
        "schema_version": 1,
        "report_type": "tc_motor_serial_link_check",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "simulation": False,
        "query_sequence": sequence,
        "axis": axis,
        "python": sys.version,
        "platform": platform.platform(),
        "requested_duration_s": duration_s,
        "completed_duration_s": 0.0,
        "successful_snapshots": 0,
        "identity": {}, "native_settings": {}, "exchanges": [],
        "units_note": "Native controller units; this check does not establish mechanical mm.",
        "ok": False, "error": None, "port_released": False,
        "standstill_verified": False,
    }
    reference_window_open = False
    stage = "open"
    opened_at = time.monotonic()

    def record_query(addressed):
        """Capture the wire exchange before the caller interprets its reply."""
        row = {"command": addressed, "stage": stage,
               "query_start_offset_s": time.monotonic() - opened_at,
               "expected_tx_hex": (addressed + "\r\n").encode("ascii").hex(" ")}
        try:
            response = transport.query(addressed)
            row["response"] = response
            return response
        except Exception as exc:
            row["error"] = str(exc)
            raise
        finally:
            row["wire"] = dict(getattr(transport, "last_exchange", {}))
            report["exchanges"].append(row)

    def query(command):
        """Retain each reply/error and check its exact address and command prefix."""
        response = record_query(f"{axis}{command}")
        row = report["exchanges"][-1]
        try:
            prefix = f"{axis}{command[:2]}"
            if not isinstance(response, str) or not response.startswith(prefix) or not response[len(prefix):]:
                raise MotorError(f"Unexpected reply for {prefix}: {response!r}")
            body = response[len(prefix):]
            if command == "TS" and not re.fullmatch(r"[0-9a-fA-F]{6}", body):
                raise MotorError(f"Malformed status reply: {response!r}")
            if command in {"TP", "SU?", "VA?", "AC?", "SL?", "SR?", "BA?", "BH?"}:
                if not math.isfinite(float(body)):
                    raise MotorError(f"Non-finite numeric reply: {response!r}")
            return body
        except Exception as exc:
            row["error"] = str(exc)
            raise

    try:
        if gui_resource:
            # No write method is exposed. This check owns cleanup in finally,
            # even if the driver's connect-time failure path also asks to close.
            peer = SimpleNamespace(open=transport.open, query=record_query, close=lambda: None)
            motor = SMC100Motor(gui_resource, axis=axis, transport="serial",
                                transport_factory=lambda: peer)
            stage = "gui_connect"
            info = motor.connect()
            report["identity"] = {"controller_version": info.controller_version,
                                  "stage_reference": info.stage_reference}
            report["native_settings"] = dict(motor.inspection_settings)
            poll = motor.get_status
        elif labview_reference:
            report["completed_visa_windows"] = 0

            def poll():
                nonlocal reference_window_open, stage
                reference_window_open = True  # A partially opened window still needs cleanup.
                stage = "open"
                transport.open()
                stage = "poll"
                query("TP")
                for _ in range(TRACE_STATUS_POLLS):
                    query("TS")
                query("TP")
                stage = "close"
                transport.close()
                reference_window_open = False
                report["completed_visa_windows"] += 1
        else:
            transport.open()
            stage = "identity"
            report["identity"]["controller_version"] = query("VE")
            report["identity"]["stage_reference"] = query("ZX?")
            if ("SMC" not in report["identity"]["controller_version"].upper()
                    or report["identity"]["stage_reference"] != "LTA-HS"):
                report["wrong_instrument"] = True
                raise MotorError("Unexpected controller/stage; link check aborted.")
            stage = "settings"
            for key in ("SU", "VA", "AC", "SL", "SR", "BA", "BH"):
                report["native_settings"][key] = query(key + "?")

            def poll():
                query("TS")
                query("TP")
        started = time.monotonic()
        deadline = started + duration_s
        try:
            while time.monotonic() < deadline:
                # These are queries only. A reported moving/error state is kept
                # verbatim, never acted on by issuing a motor command.
                stage = "poll"
                poll()
                report["successful_snapshots"] += 1
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    # The reference transport enforces its own send spacing.
                    time.sleep(min(0.01 if labview_reference else interval_s, remaining))
        finally:
            report["completed_duration_s"] = time.monotonic() - started
        report["ok"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["failure_stage"] = stage
        if isinstance(exc, KeyboardInterrupt):
            report["interrupted"] = True
    finally:
        report["transport"] = dict(getattr(transport, "session_details", {}))
        try:
            if not labview_reference or reference_window_open:
                transport.close()
            report["port_released"] = True
        except Exception as exc:
            report["ok"] = False
            report["error"] = (report["error"] or "") + f"; port cleanup failed: {exc}"
            report["cleanup_error"] = str(exc)
            report.setdefault("failure_stage", "close")
    report["valid_replies"] = sum("response" in row and "error" not in row for row in report["exchanges"])
    report["observed_status_words"] = dict(Counter(
        row["response"][len(str(axis)) + 2:] for row in report["exchanges"]
        if row["command"] == f"{axis}TS" and "response" in row and "error" not in row
    ))
    report["windows_serial_error_counts"] = dict(Counter(
        flag for row in report["exchanges"]
        for flag in row["wire"].get("windows_serial_errors", {}).get("flags", [])
    ))
    return report


def main(argv=None) -> int:
    """Require explicit physical access and save a report even when reception fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical", action="store_true", help="Authorize query-only access to the explicit port.")
    parser.add_argument("--port", type=int, required=True, help="COM number, for example 21.")
    parser.add_argument("--axis", type=int, default=1)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--transport", choices=("visa", "serial", "compare", "labview-reference", "comprehensive"),
                        default="visa")
    parser.add_argument("--query-gap-ms", type=int,
                        help="Set the LabVIEW reference minimum time between sends (130–2000 ms).")
    parser.add_argument("--gui-inspection", action="store_true", help="Use the GUI's inspection driver over direct serial.")
    parser.add_argument("--output", type=Path, help="JSON destination; default: reports/ beside this module.")
    args = parser.parse_args(argv)
    if not args.physical:
        parser.error("No port opened. Add --physical to authorize this query-only check.")
    if args.port <= 0 or not 1 <= args.axis <= 31 or not 1 <= args.duration <= MAX_DURATION_S:
        parser.error(f"Use a positive COM number, axis 1–31, and duration 1–{MAX_DURATION_S} seconds.")
    if args.gui_inspection and args.transport != "serial":
        parser.error("--gui-inspection requires --transport serial.")
    if args.query_gap_ms is not None and (
            args.transport != "labview-reference" or not 130 <= args.query_gap_ms <= 2000):
        parser.error("--query-gap-ms requires --transport labview-reference and a value from 130 to 2000.")
    comprehensive = args.transport == "comprehensive"
    prefix = "TC_motor_comprehensive_" if comprehensive else "TC_motor_serial_check_"
    output = args.output or (Path(__file__).resolve().parent / "reports" /
                            (prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Query-only check: COM{args.port}, address {args.axis}.", flush=True)
    print("Close NI MAX, TC motor, and any other COM program first. This check only sends queries.", flush=True)
    if comprehensive:
        probes = [
            ("standard_visa", "visa", 60, None),
            ("direct_windows", "serial", 60, None),
            ("reference_normal", "labview-reference", 60, 130),
            ("reference_slow", "labview-reference", 300, 1000),
        ]
        report = {"schema_version": 1, "report_type": "tc_motor_comprehensive_serial_check",
                  "created_utc": datetime.now(timezone.utc).isoformat(), "simulation": False,
                  "resource": f"COM{args.port}", "axis": args.axis, "host": host_evidence(args.port),
                  "planned_probes": [probe[0] for probe in probes], "runs": [],
                  "complete": False, "ok": False, "port_released": True,
                  "scope": "Read-only host and protocol observations. No on-wire or motion validation."}
        print("Running four independent probes, up to about eight minutes total.", flush=True)
    else:
        modes = ("visa", "serial") if args.transport == "compare" else (args.transport,)
        probes = [(mode, mode, args.duration, args.query_gap_ms) for mode in modes]
    runs = []
    for name, mode, duration, gap in probes:
        print(f"Testing {name} for up to {duration:g} seconds...", flush=True)
        factory = {"visa": VisaSerialTransport,
                   "serial": WindowsEvidenceSerial if comprehensive else DirectSerialTransport,
                   "labview-reference": LabviewReferenceTransport}[mode]
        resource = f"COM{args.port}"
        transport = factory(resource)
        if gap is not None:
            transport.QUERY_GAP_S = gap / 1000
            print(f"Minimum time between query sends: {gap} ms.", flush=True)
        result = inspect_serial_link(transport, axis=args.axis, duration_s=duration,
                                     gui_resource=resource if args.gui_inspection else None,
                                     labview_reference=mode == "labview-reference")
        result["requested_transport"] = mode
        if gap is not None:
            result["requested_min_query_gap_ms"] = gap
        if comprehensive:
            result["probe"] = name
        runs.append(result)
        print(f"{name}: {'PASS' if result['ok'] else 'FAILED'}; port released: {result['port_released']}", flush=True)
        if comprehensive:
            report["runs"] = runs
            report["port_released"] = all(run["port_released"] for run in runs)
            save_setup_report(report, output)
        if not result["port_released"]:
            print("Further checks cancelled: previous port cleanup failed.")
            break
        if result.get("interrupted") or result.get("wrong_instrument"):
            print("Further checks cancelled.")
            break
        if comprehensive and not result["ok"] and len(runs) < len(probes):
            # Let a late response from the ended session finish before the
            # next independent probe opens this port.
            time.sleep(0.3)
    if comprehensive:
        report["complete"] = (report["port_released"] and len(runs) == len(probes) and
                              not any(run.get("interrupted") or run.get("wrong_instrument") for run in runs))
        report["ok"] = report["complete"] and all(run["ok"] for run in runs)
    elif args.transport == "compare":
        usable = [r["requested_transport"] for r in runs if r["ok"] and r["port_released"]]
        released = all(r["port_released"] for r in runs)
        report = {"schema_version": 1, "report_type": "tc_motor_serial_transport_comparison",
                  "simulation": False, "runs": runs, "passing_transports": usable,
                  "ok": bool(usable) and released, "port_released": released,
                  "note": "Separate query-only checks, not an automatic GUI fallback or motion validation."}
    else:
        report = runs[0]
    save_setup_report(report, output)
    for result in runs:
        if "completed_visa_windows" in result:
            print(f"{result.get('probe', result['requested_transport'])}: "
                  f"{result['completed_visa_windows']} complete VISA windows.")
        else:
            print(f"{result.get('probe', result['requested_transport'])}: "
                  f"{result['successful_snapshots']} complete status/position snapshots.")
        if result["error"]:
            print(result["error"])
    print(f"Port released: {report['port_released']}. This does not verify motor standstill or safety.")
    print(f"Report saved: {output.resolve()}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
