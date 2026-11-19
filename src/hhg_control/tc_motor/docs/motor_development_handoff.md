# TC motor implementation handoff — 2026-10-20

Workspace: `C:\Users\omeravp\Documents\GitHub\LabSoftware`.
Feature branch: `codex/smc100-motor-control`; original base: `5188b6a`.
At the original 2026-10-20 checkpoint, no hardware had been contacted or pushed.
Later branch pushes and the lab-PC inspection are recorded below.

## Diagnostic release — 2026-11-19

User authorized pushing the motor diagnostic on `codex/smc100-motor-control`.
The older paused checkpoint below is historical. Current branch HEAD before
this release is `81a23d6`, not the historical `81ebe05`. Unrelated working-tree
camera/analysis edits are excluded from this release and must be preserved.

Physical calibration is deferred at the user's request. No controller settings
or calibration are changed, and a blank profile remains inspection-only.
Homing establishes the position reference by moving to a reference switch;
it is not settling after a move. No automatic homing is implemented.

Motor validation: 132 offline cases pass, down from the pre-consolidation 185
(29% reduction, including additional safety regressions). All previously lost
SMC100/disconnect checks were restored. Current coverage: 1457/1691 statements
(86.16%) and 330/476 branches (69.33%), versus 1450/1688 (85.90%) and 327/474
(68.99%) before consolidation. Production line shifts mean raw line-number
comparisons across the sequencer changes are not meaningful.

Disposable-copy fault injection detected disabled position bounds, displacement
limits, late-reply rejection, host-setting drift checks, and encoder-increment
validation. Removing a redundant stage-identity check alone was not detected
because the independent stage check still rejects the mismatch; this is not
proof of exhaustive fault detection. No physical port was opened.

Lab procedure after pulling this release in GitHub Desktop:

1. Close LabVIEW and TC motor. Leave the apparatus stationary.
2. Open `src\hhg_control\tc_motor` and run `setup_physical_mode.vbs` once.
3. Run `check_serial_link.vbs`; enter **21**. Axis defaults to **1**.
4. Allow about one minute for separate VISA and direct-serial checks.
5. Send the timestamped JSON from the `reports` folder beside the launcher.
   The console also prints its full path. A passing check validates only the
   observation interval, not motion, mm calibration, or collision clearance.
6. If one path passes, select that transport explicitly in the GUI, with a
   blank profile for readback-only inspection. Do not assume automatic fallback.

Release validation: all **451 offline tests passed** from the clean staged-file
export `C:\Temp\tcm_diag_1119` with default pytest configuration, Python 3.12,
and test dependencies only (no PyVISA/pySerial installed). The long nested export
hit an existing HDF5 Windows path-length limit; the short export passed unchanged.
Wheel build/content checks and isolated wheel imports of both transports passed.
Only Python 3.12 is installed locally; the remote CI matrix must establish
3.10/3.11 compatibility. Final doc-only validation notes do not change code.

Physical serial corruption root cause remains open until the report is reviewed.
Motor movement/STOP/holding/closing acceptance requires a later supervised test.

## PAUSED CHECKPOINT — user requested immediate wrap-up, 2026-10-26

Last pushed commit is `81ebe05` on `codex/smc100-motor-control`. Everything
described below about direct serial/comparison and test reduction is currently
UNCOMMITTED and NOT PUSHED. The index contains the pre-reduction feature; the
working tree additionally contains reduced/consolidated tests. Review BOTH
`git diff` and `git diff --cached`, then stage the final state deliberately.
Do not discard either. No physical hardware was accessed by this agent.

Completed locally: explicit Windows pySerial transport (VISA retained, no silent
fallback), shared bounded framing, 60 ms query pacing, live VISA/configured
pySerial host-settings diagnostics, GUI transport selector, sequential query-only
comparison VBS, and setup installation of optional pySerial. The exact uploaded
corrupt frame is retained as a regression. A real pySerial 3.5 port=None API
smoke check passed with no port opened. Clean wheel/dependency checks passed;
the isolated environment has neither pySerial nor PyVISA. **485 full offline
tests passed** on the feature BEFORE the following test pruning, from clean
export `C:\Temp\tcm26compare` (Python 3.12.10, test dependencies only).

The user then explicitly requested a huge reduction in test inflation without
compromising reliability. Baseline was 185 motor cases (183 mentioned earlier,
then two final regressions). Removed 29 duplicate test functions, reduced repeated
parameter examples and folded related connection/report lifecycle assertions
into existing end-to-end tests. **Current reduced suite: 119 passing motor cases**
(36% reduction); no final full-suite validation of this reduced state yet.
Reduction is NOT FINISHED or accepted as reliability-equivalent: coverage audit
lost 15 previously executed production lines and 5 branches. Restore those
critical checks before further pruning/push. No mutation audit has run yet.

Audit artifacts, outside Git:

- `C:\Temp\tc_motor_before.json` / `tc_motor_before.coverage`: 185-case baseline,
  1450/1688 production statements, 327/474 branches.
- `C:\Temp\tc_motor_after.json` / `tc_motor_after.coverage`: 119-case first cut,
  1435/1688 statements, 322/474 branches.
- `C:\Temp\motor_coverage_contexts.json` / `tc_motor_contexts.coverage`: baseline
  per-test line/arc attribution. Coverage-only greedy selection found 90 cases,
  but this excludes assertion strength and some subprocess launcher tests; do
  not blindly select by coverage. Preserve explicit safety/race regressions.
- `C:\Temp\tcm26compare`: clean PRE-PRUNING source/tests, also a backup for
  restoring individual tests. `C:\Temp\tcm26comparepkg` and
  `C:\Temp\tcm26compare-wheel`: package export/wheel of that same feature.
- coverage 7.17.0 is installed in the working .venv only, not a project dependency.

Lost coverage in CURRENT production source: `drivers/smc100.py` lines
261–266, 279, 297, 506–507; branches (277,279), (296,297), (505,506).
`sequencer.py` lines 234–235, 238–239, 242; branches (233,234), (239,242).
Inspect their meaning and restore focused behavior checks; do not just hide the
coverage loss or combine unrelated scenarios to make the reported count smaller.

Resume steps: finish measured test consolidation; compare production line/branch
coverage and retain known STOP/cleanup/units/deadline/race protections; run a small
offline mutation audit on a temporary copy for critical guards; run the full
final suite from a new clean tracked export in the isolated test-only environment;
review/stage/commit/push under the existing branch authorization; check the exact
Actions SHA on Python 3.10/3.11/3.12. Only Python 3.12 is installed locally.
Then the lab user should pull via GitHub Desktop, close LabVIEW/TC motor, run
`setup_physical_mode.vbs` once and `check_serial_link.vbs` with COM 21. The new
checker explicitly compares both transports for up to 30 s each, with no motion
or configuration commands. Physical communication and motion acceptance remain
unverified. Camera code/layout must remain untouched.

## Physical report and explicit transport comparison — 2026-10-26

`TC_motor_serial_check_20261026T140749759187Z.json` is a genuine physical report
from the updated source under `C:\Users\lab\Documents\GitHub\LabSoftware\src`.
It ran Python 3.12.15 (Anaconda), Windows build 17763, PyVISA 1.16.2 and
`C:\windows\system32\visa32.dll`. Reported host settings were 57600/8N1/XON-XOFF.
Identity/settings matched the earlier log. Two TP snapshots reported `29.99999`
native units; TS `000033` reported no error and READY from MOVING. This establishes
a controller-reported reference/readiness at that time, not actual mm calibration.
The third TP received `4c 15 25 ca 72 ca ca ca ca ca 6a 0a`; the check aborted
after about 1.062 s and released the port. Thus the earlier receive correction
did NOT resolve the physical corruption. Raw bytes rule out a text-codec-only
explanation but do not prove which host/runtime/adapter component produced them.

Next implementation adds an explicit pySerial transport alongside VISA, retaining
the same SMC100Motor, mock, sequencer, profile and STOP protections. This is a
deliberate exception to the PyVISA preference to isolate the failing VISA path;
there is no automatic fallback. Windows COM is opened exclusively by pySerial.
Both transports share strict framing and a bounded 60 ms query-send guard, never
applied to STOP writes. Host settings are captured around exchanges and drift
refuses a new query without overwriting settings. Direct settings are pySerial
configured attributes; VISA attributes are not a wire-level measurement.

After pulling, run `setup_physical_mode.vbs` once, then `check_serial_link.vbs`
with 21. It explicitly tests both paths for up to 30 seconds each, releasing one
before the next. It never issues movement or controller configuration commands.
It skips the second path after a cleanup failure. Reports preserve separate
results; passing one never silently selects it in the GUI. If direct passes,
select Physical / Direct serial (pySerial), COM21, address 1, blank profile.
Motion remains locked. Physical communication acceptance is still outstanding.

## Python serial receive correction — 2026-10-26

The user reports the existing LabVIEW program works through the lab setup.
Device Manager identifies COM21 as ATEN USB to Serial Bridge,
`USB\\VID_0557&PID_2008&REV_0300`, ATEN driver `3.8.15.5`, displayed date
`10/2/2017`. This does not establish that the driver is faulty. ATEN identifies
VID 0557 / PID 2008 with its older UC232A family in its
[hardware revision note](https://eservice.aten.com/eServiceCx/Common/FAQ/view.do?id=17892).
Do not replace the adapter or update its driver based only on its age.

Review found the supplied LabVIEW `read_write.vi` waits for Bytes at Port, reads
only available bytes and waits 10 ms between receive checks. Python instead used
immediate `session.query()` / a large blocking text read, relied on defaults for
VISA serial end modes, and had no byte-level transport regression tests. The
corrected `VisaSerialTransport.query` uses available counts, raw reads and strict
CRLF/ASCII validation under a single deadline and 256-byte limit. Serial end
modes, XON/XOFF characters and NUL handling are explicit; STOP remains an immediate
write. Bad bytes still latch a fault and are included as hex in the error. No
automatic retry or masking of corrupt bytes was added. This removes concrete
implementation differences, but lab causality/resolution is not yet proven.

`check_serial_link.vbs` asks for a COM number (default 21) and runs a 30-second
query-only link check at address 1. Close LabVIEW and TC motor first. It opens only
the chosen resource, never sends motion/configuration/STOP commands, and saves
success or failure plus backend metadata and raw reply diagnostics to the motor
folder's ignored `reports/` directory. Its transport is identical to the GUI's;
its PASS is a communication observation, not a motion/calibration acceptance.
TS clears controller status errors; all replies are retained. The checker closes
only the port and does not verify standstill. All implementation validation here
uses fake byte-level VISA peers; no hardware was accessed by the agent.

Validation of the final correction: **167 motor tests passed** in the working
Python 3.12 environment; **467 full offline tests passed** in a clean tracked-file
export at `C:\Temp\tcm26singlefinal`, using the isolated test-only Python 3.12.10
environment with neither PyVISA nor PCO installed. The clean wheel contains the
new module/VBS. Local Python 3.10 and 3.11 are unavailable; their CI matrix results
must be checked for the pushed revision. No physical validation is claimed.

The final implementation uses one low-level VISA read per available count,
avoiding the nested partial-read loop in PyVISA's `read_bytes` helper. A short
read or late return fails once. Only the normal full-count warning is suppressed
for this session. A separate offline smoke check used actual PyVISA 1.16.2
resource descriptors with a fake backend to verify serial attribute types,
CRLF output, counted read, warning scope, and close. The report records the loaded
driver file; VBS and documented PowerShell commands select this checkout's source
explicitly rather than an older installed wheel.

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

At 13:33:17 a later inspection-only Physical connection again read the same
`SMC_CC` version 3.1.2, `LTA-HS`, address 1, and native settings
`SU=3.53932e-05`, `VA=5`, `AC=20`, `SL=0`, `SR=50`, `BA=0`, `BH=0`.
At 13:33:19 its `TS` status query received an invalid non-ASCII byte `0xB1`
at the start of the response. At 13:33:24 the GUI released the inspection
port and reported that standstill was not reconfirmed. There was no repeated
error loop. This second failure on a different query (`TS` versus earlier
`TP`) strengthens evidence of an intermittent response-path problem, but does
not identify whether the cable, serial adapter, controller, VISA backend, or
software read synchronization caused it. Do not enable physical motion until
the response path is stable and the cause is investigated.

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
