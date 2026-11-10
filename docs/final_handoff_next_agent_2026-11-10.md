# Hard cutoff: F02 recovery and final default clean-export validation

The user requested wrap-up here. Stop adding unrelated fixes. F01 and F03–F43 have implementations and ID-by-ID evidence in `docs/final_handoff_disposition_2026-11-10.md`. **F02 is confirmed, unimplemented, and is the next coding task.**

## Exact code boundary

Start at `src/hhg_control/camera/sequencer/capture_pipeline.py:200`, `BufferedCamera.iter_frames`: it raises the terminal reader error before draining already retrieved valid batches. The deterministic retained probe reports retrieved=2, delivered=0, pending=2, reader stopped.

Recovery must also include `_read` at line 131 and `close` at line 241, the driver-local FIFO lists in `src/hhg_control/camera/drivers/pco_edge.py:iter_frames`, the producer/queue boundary in `src/hhg_control/camera/sequencer/scan_manager.py:548`, and discard cleanup in `scripts/complete_pipeline_trial.py`. Fixing only the eager raise is insufficient. Preserve every returned byte/ID as incomplete evidence, keep malformed payloads separately, retain cleanup/write errors, avoid full-queue deadlocks, and never move read-ahead frames to another setpoint. Do not terminate a hardware-owning thread. See the F02 specialist continuation in the disposition report.

## Git and preservation checkpoint

- Original checkout is the user's junction-backed workspace. Original branch remains `codex/smc100-motor-control`, HEAD `f4425b4b1faeb131b2a9fdb002cf64209556119c`.
- Local review history: `codex/final-handoff-fixes-20261110`, rooted in `codex/final-handoff-baseline-20261110`, an explicitly labeled snapshot of the pre-existing working tree. Six subsequent coherent fix commits contain this task's intended changes. Inspect `data/test_artifacts/final_handoff_baseline/commits.json` for exact IDs.
- The original index is preserved byte-for-byte, not restaged to the review branch. The working tree includes the intended fixes and the original staged/unstaged work. **Do not reset, stash, check out the review branch, or commit the user's staged work blindly.** Use the review refs for comparison or a separate worktree/export.
- Baseline originals, hashes, `initial.index`, staged/unstaged patches and probe outputs are in `data/test_artifacts/final_handoff_baseline/`. Do not overwrite them.
- No physical hardware was operated. No changes were pushed. Continue with mocks/synthetic files only; no push without explicit authorization.

## Validation already completed

Final exported-source suite: **510 passed**, Python 3.12, MATLAB R2026b, isolated environment containing only declared runtime/test dependencies; optional pco SDK absent. This used explicit roots, exported `pyproject.toml`, and an alternate short basetemp. Working default suite previously passed 508 cases; final CLI/GUI refinements are included in the 510-case run. Long-path suite: **143 passed**, actual filenames up to **446 characters**. Final installed-wheel smoke: **77 runtime-resource hashes**, aliases, and all three disconnected offscreen entrypoints passed from empty cwd without PYTHONPATH.

Logs are listed in the disposition report. Python 3.10/3.11 are unavailable. Physical timing/trigger acceptance, vendor recorder long-path support and remote CI are unverified.

## Required validation left at cutoff

The remaining infrastructure check is the exact **default** command from the final clean export, without explicit roots or a basetemp override. The export and isolated interpreter already exist:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
$env:PYTHONPATH = $null
$env:QT_QPA_PLATFORM = 'offscreen'
Set-Location -LiteralPath 'C:\Users\omeravp\Documents\GitHub\LabSoftware\data\test_artifacts\package_verified\source'
& '..\isolated\Scripts\python.exe' -m pytest -q
```

Verify that the source export still matches the proposed code before using it. The only later edits at this checkpoint are the disposition/continuation documentation. `data/test_artifacts/pytest/.gitkeep` is included in the export. Use normal filesystem access if the sandbox denies artifacts; do not change ACLs. Refresh the export and rerun appropriate focused/full checks after implementing F02. Keep source-suite, installed-wheel, default-clean-export and physical validation claims separate.
