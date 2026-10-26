"""Bounded, query-only SMC100 link check; physical access requires --physical.

Uses the same transport as the GUI. Never sends PA, ST, home, reset, enable,
zero, or setting writes. TS clears the controller's status error register; all
received frames and errors are retained in the report. Motion stays untested.
"""

import argparse
from datetime import datetime, timezone
import math
import platform
from pathlib import Path
import re
import sys
import time

from .drivers.base import MotorError
from .drivers.smc100 import VisaSerialTransport
from .reporting import save_setup_report


def inspect_serial_link(transport, *, axis: int = 1, duration_s: float = 30.0,
                        interval_s: float = 0.5) -> dict:
    """Inspect one explicit axis for up to duration_s seconds, with no retries.

    Native positions/settings are uncalibrated controller units. Always close
    only the host port, including on error; this does not stop or verify motion.
    Passing proves only successful replies during this observation interval.
    Injectable transport lets offline tests cover the identical query sequence.
    """
    if type(axis) is not int or not 1 <= axis <= 31:
        raise ValueError("Controller address must be 1–31.")
    if not 1 <= duration_s <= 120 or not 0.05 <= interval_s <= 5:
        raise ValueError("Use 1–120 seconds and a 0.05–5 second polling interval.")
    report = {
        "schema_version": 1,
        "report_type": "tc_motor_serial_link_check",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "simulation": False,
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

    def query(command):
        """Retain each reply/error and check its exact address and command prefix."""
        row = {"command": f"{axis}{command}"}
        try:
            response = transport.query(row["command"])
            row["response"] = response
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
        finally:
            row["wire"] = dict(getattr(transport, "last_exchange", {}))
            report["exchanges"].append(row)

    try:
        transport.open()
        report["identity"]["controller_version"] = query("VE")
        report["identity"]["stage_reference"] = query("ZX?")
        if ("SMC" not in report["identity"]["controller_version"].upper()
                or report["identity"]["stage_reference"] != "LTA-HS"):
            raise MotorError("Unexpected controller/stage; link check aborted.")
        for key in ("SU", "VA", "AC", "SL", "SR", "BA", "BH"):
            report["native_settings"][key] = query(key + "?")
        started = time.monotonic()
        deadline = started + duration_s
        try:
            while time.monotonic() < deadline:
                # These are queries only. A reported moving/error state is kept
                # verbatim, never acted on by issuing a motor command.
                query("TS")
                query("TP")
                report["successful_snapshots"] += 1
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(interval_s, remaining))
        finally:
            report["completed_duration_s"] = time.monotonic() - started
        report["ok"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["transport"] = dict(getattr(transport, "session_details", {}))
        try:
            transport.close()
            report["port_released"] = True
        except Exception as exc:
            report["ok"] = False
            report["error"] = (report["error"] or "") + f"; port cleanup failed: {exc}"
    return report


def main(argv=None) -> int:
    """Require explicit physical access and save a report even when reception fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical", action="store_true", help="Authorize query-only access to the explicit port.")
    parser.add_argument("--port", type=int, required=True, help="COM number, for example 21.")
    parser.add_argument("--axis", type=int, default=1)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", type=Path, help="JSON destination; default: reports/ beside this module.")
    args = parser.parse_args(argv)
    if not args.physical:
        parser.error("No port opened. Add --physical to authorize this query-only check.")
    if args.port <= 0 or not 1 <= args.axis <= 31 or not 1 <= args.duration <= 120:
        parser.error("Use a positive COM number, axis 1–31, and duration 1–120 seconds.")
    output = args.output or (Path(__file__).resolve().parent / "reports" /
                            ("TC_motor_serial_check_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Query-only check: COM{args.port}, address {args.axis}, {args.duration:g} seconds.", flush=True)
    print("Close LabVIEW and TC motor first. No move, STOP, home, or configuration command is sent.", flush=True)
    report = inspect_serial_link(VisaSerialTransport(f"COM{args.port}"), axis=args.axis,
                                 duration_s=args.duration)
    save_setup_report(report, output)
    print(f"{'PASS' if report['ok'] else 'FAILED'}: {report['successful_snapshots']} complete status/position snapshots.")
    if report["error"]:
        print(report["error"])
    print(f"Port released: {report['port_released']}. This does not verify motor standstill or safety.")
    print(f"Report saved: {output.resolve()}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
