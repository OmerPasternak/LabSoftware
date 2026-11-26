"""Exercise the native comparison with simulated VISA replies and no device."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_native_comparison_progresses_only_after_strict_lf_reads(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("The NI-VISA launcher uses Windows PowerShell.")

    script = Path(__file__).resolve().parents[1] / "check_ni_max_comparison.ps1"
    harness = tmp_path / "simulate_visa.ps1"
    harness.write_text(
        r"""
param($Script, $Folder)
$ErrorActionPreference = 'Stop'
. $Script
$null = New-NiVisaApi  # Compile the real binding without opening any session.

$api = @{
    Manager = { @{ code = 0; handle = 100 } }
    Open = {
        param($manager, $resource)
        if ($manager -ne 100 -or $resource -ne 'ASRL21::INSTR' -or $wire.open) {
            throw 'Unexpected resource or overlapping sessions'
        }
        $wire.open = $true
        @{ code = 0; handle = 200 }
    }
    Set = {
        param($handle, $attr, $value)
        if ($attr -eq 0x3FFF0018) { $wire.term = $value }
        0
    }
    Available = { $wire.rx.Count }
    Write = {
        param($handle, [byte[]]$bytes)
        if (-not $wire.open -or $handle -ne 200 -or $wire.rx.Count) {
            throw 'Unexpected write'
        }
        $command = [Text.Encoding]::ASCII.GetString($bytes)
        $response = switch -CaseSensitive ($command) {
            "1TS`r`n" { "1TS000033`r`n" }
            "1TP`r`n" { "1TP25.89997`r`n" }
            default { throw 'Unexpected instrument command' }
        }
        $reply = [Text.Encoding]::ASCII.GetBytes($response)
        if (($case -eq 'lf_fault' -and $wire.term -eq 10) -or
            ($case -eq 'counted_fault' -and $wire.term -eq 13)) {
            $reply = [byte[]](0xB1, 0xD5, 0x05, 0x0A)
        }
        $wire.rx.AddRange($reply)
        @{ code = 0; count = $bytes.Length }
    }
    Read = {
        param($handle, $requested)
        $n = [Math]::Min($requested, $wire.rx.Count)
        $end = $wire.rx.IndexOf([byte]$wire.term)
        if ($end -ge 0) { $n = [Math]::Min($n, $end + 1) }
        $result = $wire.rx.GetRange(0, $n).ToArray()
        $wire.rx.RemoveRange(0, $n)
        $code = if ($n -eq $requested) { 0x3FFF0006 } else { 0x3FFF0005 }
        @{ code = $code; count = $n; bytes = $result }
    }
    Close = {
        param($handle)
        if ($handle -eq 100) {
            if ($wire.open -and $case -ne 'close_fault') { throw 'COM port leaked' }
        } elseif ($case -eq 'close_fault') {
            return -1
        } else {
            $wire.open = $false
        }
        0
    }
}

foreach ($case in @('success', 'lf_fault', 'counted_fault', 'close_fault')) {
    $wire = @{ open = $false; rx = [Collections.Generic.List[byte]]::new() }
    $null = Invoke-NiMaxComparison $api 21 (Join-Path $Folder "$case.json") 2 1 0
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
            "-File", str(harness), str(script), str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    def report(case):
        return json.loads((tmp_path / f"{case}.json").read_text(encoding="utf-8"))

    clean = report("success")
    assert clean["ok"] and clean["port_released"]
    baseline, mixed, counted = clean["runs"]
    assert [run["name"] for run in clean["runs"]] == [
        "status_only_lf", "mixed_lf", "mixed_counted_cr",
    ]
    assert [run["valid_replies"] for run in clean["runs"]] == [2, 10, 10]
    assert baseline["exchanges"][0]["reads"][0]["requested"] == 64
    assert [row["command"] for row in mixed["exchanges"]] == ["1TP"] + ["1TS"] * 8 + ["1TP"]
    assert [row["command"] for row in mixed["exchanges"]] == [
        row["command"] for row in counted["exchanges"]
    ]
    split = counted["exchanges"][1]["reads"]
    assert [(row["requested"], row["returned"]) for row in split] == [(11, 10), (1, 1)]
    assert split[1]["hex"] == "0A"

    lf_fault = report("lf_fault")
    assert lf_fault["port_released"] and not lf_fault["ok"]
    assert lf_fault["follow_up"].startswith("skipped")
    assert len(lf_fault["runs"]) == 1
    assert lf_fault["runs"][0]["exchanges"][0]["rx_hex"] == "B1 D5 05 0A"

    counted_fault = report("counted_fault")
    assert counted_fault["port_released"] and not counted_fault["ok"]
    assert counted_fault["follow_up"] == "failed"
    failed_exchange = counted_fault["runs"][2]["exchanges"][0]
    assert failed_exchange["rx_hex"] == "B1 D5 05 0A"
    assert failed_exchange["reads"][0]["visa_status"] == "0x3FFF0006"

    close_fault = report("close_fault")
    assert not close_fault["port_released"] and not close_fault["ok"]
    assert close_fault["follow_up"].startswith("skipped")
    assert "Close COM session" in close_fault["runs"][0]["cleanup_error"]
