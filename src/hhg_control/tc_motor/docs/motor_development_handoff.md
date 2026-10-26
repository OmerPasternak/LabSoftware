# TC motor implementation handoff — 2026-10-20

Workspace: `C:\Users\omeravp\Documents\GitHub\LabSoftware`.
Feature branch: `codex/smc100-motor-control`; original base: `5188b6a`.
At the original 2026-10-20 checkpoint, no hardware had been contacted or pushed.
Later branch pushes and the lab-PC inspection are recorded below.

## Setup report reviewed — 2026-10-26

The supplied `TC_motor_setup_report.json` was exported at
`2026-10-26T10:20:51.228002+00:00`. It explicitly says `simulation: true`,
`mode: Mock`, `MOCK SMC100CC (project emulator)`, and `MOCK LTA-HS`.
Its COM21 request and axis 1 are saved UI inputs; this report did not open or
identify the physical serial device. It contains no native controller settings.

The report confirms the mock's READY state at synthetic position 0 mm and its
test envelope: 0–10 mm, 1 mm/s maximum velocity, 2 mm/s² maximum acceleration,
and 2 mm maximum move. These values are **mock specifications only**. Do not
transfer them, `units_verified: true`, `motion_profile_enabled: true`, or
`move_permitted_now: true` into a physical safety profile. The physical profile
template remains intentionally unfilled and motion-locked.

Still open for physical operation: an actual Physical-mode setup report after
stable serial readback; controller label and installed actuator/serial pairing;
stage-specific encoder calibration, mm scale and direction; physical reference
and current position; mounted safe interval and stop clearance; reviewed velocity,
acceleration and per-move limit; and supervised motion, STOP/holding, and
disconnect acceptance. Homing parameters/procedure are also unreviewed and
homing remains unavailable. The separate earlier lab log below provides native
readbacks, but its `0xCA` serial fault has not been diagnosed.

## Lab serial-fault recovery — 2026-10-26

The user ran inspection-only Physical mode on the lab PC at COM21, address 1.
The lab log reported `SMC_CC - Controller-driver version 3.1.2`, stage reference
`LTA-HS`, and native settings `SU=3.53932e-05`, `VA=5`, `AC=20`, `SL=0`,
`SR=50`, `BA=0`, `BH=0`. These observations establish the responding controller
family and stage reference, but do not verify mechanical mm calibration, mounted
travel, or physical motion safety. No motion profile was loaded in that session.

An earlier connection returned Newport TE error `M`; a subsequent connect
succeeded. About six seconds later, a `TP` position query failed decoding an
unexpected byte `0xCA`. The old GUI kept polling, then repeatedly failed to
verify STOP and trapped window close. Root cause of the byte corruption remains
unproven on the lab PC; investigate cable/adapter, controller power and EMI,
while preserving 57600 8N1 XON/XOFF CRLF from the manual.

The new code pauses polling on the first failed snapshot, hides stale position,
disables motion, and releases a faulted inspection-only serial handle without
issuing ST. A motion-capable session still requires physical standstill/holding
confirmation before an explicit COM release; that recovery sends no further
motor command. Disconnect scheduling is atomic so it cannot race into an extra
ST. Failed connect with a successfully closed port leaves the GUI ready for a
fresh attempt. Offline fake-serial/Qt tests cover the reported fault and both
recovery paths. Physical acceptance remains outstanding.

## Dependency setup and VBS launchers — 2026-10-26

The GUI now asks for a numeric COM port (default 21, confirmed by the user) and
internally passes COM21/ASRL21 to the existing driver. No discovery/auto-connect
was added. `run_TC_motor.vbs` launches with pythonw without a terminal;
`setup_physical_mode.vbs` opens visible setup output via `run_TC_motor.bat setup`.
The BAT remains for compatibility. VBS files are included in package data.
Setup installs only PyVISA (matching the hardware-smc100 extra) into the checkout's .venv and
checks PyVISA/native VISA in a separate process without opening or listing ports.
Missing Python packages versus missing native runtime now have distinct errors.
The setup cannot install/repair NI-VISA itself; that is a lab-PC system dependency.
PyVISA 1.16.2 was installed in the local working .venv; the local native VISA
runtime is absent, and the new check reports that correctly. No motor was opened.

Targeted GUI/launcher/dependency checks passed (60 tests); harmless VBS/BAT stubs
exercise spaces, unrelated cwd, interpreter selection, and installer failure.
An early full run was stopped after long test-output paths broke existing camera
benchmark fixture lookups. Use a short clean export and the configured default
pytest path for final validation; do not alter camera code for this path artifact.
Final clean-export validation: **426 passed** using the exact `python -m pytest -q`
command in `C:\Temp\tcm26final`, Python 3.12.10, isolated test-only environment
with neither PyVISA nor PCO installed. The installed clean wheel includes both
VBS launchers, BAT, and the dependency checker. No camera source was changed.
The previous setup-report feature is already pushed as `04e87cc`; this addition
targets the same `codex/smc100-motor-control` branch.

## Setup-report addition — 2026-10-25

The motor GUI now has **Save setup report…** while connected/idle, in either
Mock or Physical mode. It serializes a fresh status/position snapshot and cached
connection/preflight settings through the existing sequencer/worker. It does not
issue motion or configuration commands, enable motion, infer calibration, or
generate an approved safety profile. Native units remain unverified without a
profile; mock provenance is explicit. It reports READY versus not READY, but
home type/speed/timeout are not queried and homing remains unsupported.
The report cannot overwrite the active safety profile. JSON replacement is atomic;
write errors remain visible without disconnecting or closing the window.

Implementation is in `sequencer.py`, `reporting.py`, and `gui/`. The guide now
starts with five short lab-PC setup steps. Added fake-serial, mock, file-failure,
unit-provenance, and Qt export checks. On branch `codex/tc-motor-setup-report`,
all **116 motor tests passed** using Python 3.12.10 and the working `.venv`, with
normal filesystem access after the sandbox denied pytest temp-directory access.
Command: `python -m pytest -q src/hhg_control/tc_motor/tests
--basetemp=data/test_artifacts/pytest/tc_setup_report_20261025_access`.
Per `docs/test_selection.md`, this isolated motor change did not require camera
tests or another full-project run. No physical hardware or remote CI was used.
Earlier validation evidence below belongs to the original implementation.

## Run and inspect

Double-click `src/hhg_control/tc_motor/run_TC_motor.bat`, or run `.venv\Scripts\python.exe -m
hhg_control.tc_motor.gui` from the root. The separate window starts disconnected in
Mock mode. `src/hhg_control/tc_motor/docs/motor_smc100.md` covers use, architecture, physical gating,
calibration, API examples, protocol sources, and acceptance still required.

All motor-specific files are consolidated under `src/hhg_control/tc_motor/`,
including `gui/`, `tests/`, `docs/`, and the launcher. Camera source/layout were not edited. Shared changes
are launcher/documentation, a `hhg-tc-motor` console entry and optional PyVISA extra.
The mock is project-owned; no company simulator was found.

## User requirements and confirmed evidence

- Photos identify Newport LTA-HS serial B15 2823. LabVIEW paths and PA/VE/ZX?
  confirm the expected SMC100 software family. SMC100CC is the expected servo
  variant, not yet physically verified or matched to this photographed actuator.
- Mechanical mm only. No presets, scan UI, optical-delay conversion, home, or zero.
- STOP retains existing servo holding. It must not enable a previously disabled axis.
- Window close must wait for verified stop and disconnect. Failure leaves it open;
  the worker must never be forcibly terminated to hide uncertainty.
- User proposed motor-wide bounds and existing LabVIEW/default speed/acceleration,
  while supervising physically later. Those settings/calibration are not in the
  screenshots. Physical code reads actual settings, never invents or changes them,
  and requires a reviewed profile before movement. Template is intentionally invalid.
- Physical commands and Git push require separate authorization; neither occurred.

## Safety design to preserve

One worker owns sequencer/driver I/O. Cancellation uses persistent thread events,
including pending/preflight moves, with explicit Resume after STOP. Never queue
movement behind another move. Validate finite targets, both bounds, relative-move
targets, per-move distance, state, identity, calibration, speed/acceleration,
compensation and encoder rounding. Do not rewrite controller settings or retry PA.
`TP`, `TS`, `TE`, `VE` have no question mark; parameter queries do. CRLF, 57600 8N1,
XON/XOFF, 300 ms default bounded I/O. Addressed `ST` is a decelerating software stop.
TS/TE clear controller error latches; retain faults locally. Loss of reference,
unknown states, mismatched responses and calibration uncertainty must not silently
rearm. Native-unit values cannot be labelled mm until reviewed calibration matches.
Only close this motor's serial handle, never a shared PyVISA ResourceManager.
Physical serial access requests an exclusive VISA lock.

## Validation checkpoint

- Before the TC motor folder rename: **395 full-suite tests passed** with the
  default pytest command in a short clean export and isolated test-only environment.
- After consolidation/rename: **107 motor/mock/sequencer/GUI/protocol tests passed** on
  Python 3.12.10. This includes reference-loss, preflight cancellation/deadline,
  unknown state, uncertain cleanup and Qt lifecycle regressions.
- Worktree default `pytest -q` discovered a pre-existing full checkout export at
  `data/test_artifacts/layout export` and failed with duplicate-import collection
  errors. Do not delete that artifact or alter camera tests to work around it.
  Worktree full validation uses `--ignore=data`; clean exports use default pytest.
- A separate environment `data/test_artifacts/motor_validation_env` has only `.[test]`
  installed (no PyVISA or pco). Its final clean-export wheel imports the motor GUI
  successfully; `pip check` passes. The renamed entry is `hhg-tc-motor`.
  The installed wheel includes the guide, safety JSON template and BAT launcher.
  The actual BAT was tested with a checkout path containing spaces and an unrelated
  working directory, using a harmless stub module rather than a hardware session.
  Before the rename the short clean
  export was `C:\Temp\lsmotor-01a1597c` (created for this task only); it passed.
  Rename validation uses `C:\Temp\tcm-01a1597c`. The final full run was interrupted
  at the user's explicit END ASAP request; do not claim a completed full-suite
  pass after rename. The 107 focused tests passed after rename; 395 full-suite
  tests passed before rename. Resume with the final full clean-export run if asked.
  Earlier nested exports
  under `data/` made existing benchmark filenames exceed Windows path limits;
  those diagnostic runs were interrupted. Do not change camera code to fix that
  validation-location artifact.
- Available interpreter: 3.12.10. Launcher inventory reports only 3.12; 3.10/3.11 and
  remote CI have not been validated. Do not equate the local pass with CI or hardware.
- Preview: `data/test_artifacts/motor_gui_preview.png` (not tracked).

## If interrupted before final delivery

Work stopped at the user's explicit END ASAP request. Inspect `git status`, the staged diff, and running exec sessions before rerunning
checks. The dispatch guard for deadlines/external cancellation is complete and
tested: BaseMotor.motion_guard/check_motion_guard, sequencer wrapping, and physical
preflight/PA checks agree. Source is staged with no unstaged code edits at checkpoint.
Run `python -m pytest -q src/hhg_control/tc_motor/tests` with a unique pytest basetemp first.
Then run the full suite from a fresh tracked/index export with the isolated test
environment and default pytest command. Do not run competing pytest processes
against the same basetemp. Update this validation section with final evidence.

Check `git diff --check`, verify no camera paths are in the change, and commit only
this feature locally per AGENTS.md. Do not push. Deliver the launcher, guide, test
results, outstanding calibration/physical validation, and this handoff to the user.

## Physical next step, after explicit authorization

Inspect actual controller identity/configuration and stage calibration/control
report. Establish mm scale/sign/reference, safe mounted travel and stop clearance.
Nominal 50 mm stroke is not proof of safe mounted range. Validate small motion in
both directions, STOP distance and holding, limits, close/disconnect and cable-loss
behavior under supervision. Nonzero BA/BH compensation is intentionally blocked
pending review of its extra excursions. Physical homing needs its own reviewed
procedure. Record physical acceptance separately from synthetic software tests.
