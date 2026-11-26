# TC motor — separate LTA-HS / SMC100 control

All motor-specific implementation, GUI, tests, documentation, profile template,
and launcher live together under `src/hhg_control/tc_motor/`. The Python package
uses an underscore because spaces are not valid in a Python import name. Its
window is named **TC motor**. The launchers resolve the checkout from their own
location; it does not depend on the directory from which it was double-clicked.

## First setup on the lab PC

If replacing LabVIEW currently produces a serial decoding error, first close
LabVIEW and TC motor. Run `setup_physical_mode.vbs` once after pulling the update
to install pySerial alongside PyVISA. Then double-click `check_serial_link.vbs`
beside the motor launcher and enter **21**. It uses address 1. Choose **1**
for the short comparison, which checks VISA, closes its port, then checks
Direct serial for up to 30 seconds each. Choose **2** to check Direct serial
for ten minutes using the same driver startup and status reads as the GUI in
inspection mode. The JSON includes the query sequence and controller replies.
The console stays open while it collects replies and
stops the run on the first communication error. If setup already passed in the
same environment, run the check directly. Choose **3** for the two-minute
LabVIEW reference test over VISA. It sends only TS and TP, and reports each
received chunk separately.

### What the LabVIEW trace showed

The two captured COM21 sessions opened and closed separately. The VISA
attribute writes were timeout `0x3FFF001A=300` ms, baud
`0x3FFF0021=57600`, data bits `0x3FFF0022=8`, flow control
`0x3FFF0025=1` (XON/XOFF), parity `0x3FFF0023=0`, stop bits
`0x3FFF0024=10` (one bit), and term character `0x3FFF0018=13` (CR).
No COM21 buffer resize or flush was captured. The `1TS\r\n` and
`1TP\r\n` writes match Python's wire bytes. LabVIEW first checked byte
availability (`0x3FFF00AC`) about 99–103 ms after issuing a query. Its
first read requested 11 bytes for status or 13 for position and returned
one fewer, ending in CR. The second read requested and returned LF about
7–12 ms later. During status polling, query starts were about 130 ms apart.
Both recorded moves returned plausible positions. The reference check
does not replay these moves.

The reference mode uses a 100 ms first-read delay, 130 ms minimum send
spacing, VISA CR termination, and the strict shared CRLF frame parser.
Each exclusive session issues TP, eight consecutive TS queries, then TP
again before closing. The reference keeps the NI-VISA timeout at 300 ms
throughout each session rather than rewriting a host timeout before each
chunk. It reads only the available count and rejects any reply completed
after the software deadline. If the native read unexpectedly blocks, the
fixed VISA timeout bounds that last call. The report records each read's
requested count, returned count and native completion code. The trace did
not show writes to the end-mode or
termination-enable attributes, so the explicit VISA end mode is an
inference from the observed read split. The other VISA and Direct serial
modes read raw chunks without CR termination and start checking after
10 ms, with a 60 ms minimum send gap and an input flush at open. The
reference check compares those behaviors.

### Recovery through the motor GUI

The two-minute reference run on COM21 stopped after 20.203 seconds. Its 77
complete snapshots had status `000033` and position `-0.00000` in controller
units. Its valid replies showed the same CR/LF split as the LabVIEW capture.
The failed TS reply was `b1 d5 05 82 82 82 9a 9a 6a 0a`, identical to the
failed reply from the longer direct serial test. Starting at the last three
zero digits, each damaged byte through CR equals the corresponding clean byte
shifted left three bits, truncated to eight bits, with the low bits set to
`010`. The GUI's damaged position reply shows the same pattern. This is
consistent with a serial receiver sampling across character boundaries. It
does not identify which component caused the slip. The original bytes are
rejected and retained. The program never tries to reverse that shift.

The successful replies make a fixed baud, parity, or terminator mismatch
unlikely. Reproducing the fault with direct COM and with the reference VISA
read takes the GUI, VISA alone, and the Python text decoder out of the
required failure path. The raw data still allow a fault in the Windows
serial driver, the adapter, electrical signaling, or the controller's
transmit path. The two captured LabVIEW sessions only contained 20 query
replies in total. They prove the instrument worked in those sessions, and
give us a short-session pattern to test on the same hardware.

Use **Physical → VISA (LabVIEW inspection)** with COM **21**, axis **1**, and
a blank profile. This mode uses the reference read timing and opens a new
exclusive VISA session for each stationary-monitoring burst. Each new window
checks controller identity, reads TP, polls TS up to eight times, and reads
TP again before publishing the fresh position. Status polling ends early on
a reported positioner fault or non-ready state. The first corrupt-byte failure may be followed by one
fresh connection or one fresh snapshot on the same backend, after a 300 ms
quiet period. It logs the original error and raw bytes if it recovers.
A second corrupt reply pauses polling. Timeouts, serial configuration drift,
and unverified device changes also fail closed. A failed close blocks renewal.
Disconnecting releases the inspection port without sending
STOP, since this mode cannot command motion. Leave the controller powered
and use its existing coordinates.

For lab validation, watch the position for at least two minutes and record
any “Inspection recovered on a fresh session” or serial fault message.
Save the setup report while connected to include the number of session
renewals and details of any recovered exchange. If polling pauses, retain
the log and disconnect. Success would establish reliable readback for that
observation, but still requires separate motion validation. If the same
error recurs after a fresh reopen, compare a longer known-good LabVIEW
trace for the same port and workload, or compare a different USB port or
known-good RS-232 adapter if accessible. Keep the controller powered during
the comparison.
The second check is cancelled if the first port cannot be released.
The console stays visible and names
the JSON file saved under `tc_motor/reports/`, including when communication fails.
Send that report for diagnosis. No move, STOP, home, enable, reset, zero or
configuration write is sent; this tool closes only its own COM handle. It cannot
stop externally initiated motion or confirm holding. A passing result establishes
only communication during the check, not calibration or motion safety. `TS`
queries clear the controller's status error register; replies are saved verbatim.

The comparison JSON lists `passing_transports` and each separate run. A PASS
means that path worked during the observation. If Direct serial passes, select
**Physical → Physical connection: Direct serial (pySerial)** in the GUI, COM 21,
address 1, and leave the profile blank for inspection. VISA remains the default
and is still available. Selecting a transport never falls back to another one.
Both use the same driver, safety profile, STOP logic and mm calibration checks.
Direct serial bypasses the system VISA DLL using exclusive native Windows COM
access; it still uses the same ATEN adapter/driver/cable. It is an explicit
compatibility alternative after the lab report showed VISA delivering corrupt
raw bytes, not proof that VISA caused them. Physical acceptance is still open.

1. **Identity:** confirm the controller label, actuator, and address. The app also
   reads the firmware version and stage reference.
2. **Millimetres:** obtain this actuator's calibration/control report. Compare its
   encoder increment with SU in the exported report. A matching number alone does
   not prove the actual motion scale or direction; that needs physical review.
3. **Travel:** the controller can supply its configured limits, but someone must
   check the mounted optics/cables and choose a safe interval and stopping margin.
4. **Settings:** close other software using this serial port. Select Physical,
   enter only the COM port number (21 for COM21) and controller address,
   leave the profile blank, and
   connect for inspection. Click **Save setup report…** and save the JSON file.
   No movement is enabled by collecting or saving this report.
5. **Reference:** the report states whether the controller reports READY. If it
   does, no home is needed just for this application. If not, review the reported
   state; homing remains unavailable pending a separate safe procedure.

The report includes a fresh status/position snapshot and the cached settings last
read at connection or motion preflight: SU (encoder increment), VA (velocity),
AC (acceleration), SL/SR (controller limits), BA/BH (compensation). Settings are
native controller units until calibration is reviewed. It also records simulation
provenance, requested port/address, unit verification, STOP latch, and current
motion permission. Saving performs no setting writes or motion; status queries
may clear controller error registers, which the driver retains locally.
Home type/speed/timeout are not collected by this export. This diagnostic JSON is
not a safety profile and does not supply calibration or mounted-clearance approval.
The active safety profile cannot be overwritten using the report button.

You can test the export in Mock mode first; that report is explicitly synthetic.
The Mock export dated 2026-10-26 reports `simulation: true`, mock READY at 0 mm,
and an empty `native_settings` object. Its COM21/axis-1 fields are requested GUI
inputs, not a physical connection check. Its `units_verified`, motion permission,
and 0–10 mm/1 mm/s/2 mm/s²/2 mm limits apply only to the emulator. The physical
safety profile remains blank; see the handoff for the outstanding checks.
Give the physical report to the next agent along with the calibration report and
the reviewed mounted travel/clearance. Those facts are needed to finish the
existing safety-profile template. Do not copy mock limits into a physical profile.

Double-click `src/hhg_control/tc_motor/run_TC_motor.vbs` for a GUI without a console.
The BAT launcher remains available for compatibility and visible troubleshooting.
You can also run:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m hhg_control.tc_motor.gui
```

The separate window starts disconnected in **Mock** mode. Connect, enter an
absolute position or relative step in **mm**, then click the corresponding move
button. Editing a number never sends a command. There are no preset, scan, home,
zero, or automatic motor-enable controls. Camera code and its window are separate.

The mock has a synthetic 0–10 mm envelope, 1 mm/s velocity, 2 mm/s² acceleration,
and a 2 mm maximum displacement per command. These values apply only to the mock.
The project-owned emulator is based on the vendor's motion/state contract; it is
**not a Newport-supplied simulator**, nor proof of physical accuracy or safety.

## STOP, disconnect, and closing the window

STOP latches cancellation immediately, including a pending move or physical
preflight. The worker sends the addressed `ST` controlled-stop command and checks
for standstill. No command disables the servo, so holding remains enabled if it
was already enabled. STOP does not turn a disabled motor on. Click **Resume after
STOP** only after checking the apparatus, then request a new move.

A close request keeps the window open while the worker stops, verifies standstill,
and disconnects. The window closes only after success and worker termination.
Failure leaves the window open with uncertainty reported and motion disabled as
appropriate; retry cleanup after resolving the problem. A failed connection attempt
must also be cleaned up before closing. The software never forcibly kills its
worker thread. OS process termination, power loss, or a disconnected cable cannot
be prevented by a window close guard.

`ST` decelerates using the controller's existing acceleration setting. It is not an
instantaneous hardware emergency stop. Cable loss may prevent both STOP and its
verification. Mechanical clearance and an accessible physical stop remain necessary;
software tests cannot establish that mounted optics cannot collide.

## Identification and evidence

The supplied photos identify a Newport **LTA-HS**, serial **B15 2823**. LabVIEW VI
paths explicitly use `SMC100 Controller_Drivers\SMC100`. `PA`, `VE`, and `ZX?` in
the supplied diagrams match that family. SMC100CC is the expected DC servo variant;
the physical controller and the pairing of that actuator with axis 1 are not yet
verified. Blank LabVIEW front-panel fields are not instrument readbacks.

The original VI uses 57600 baud, 8 data bits, no parity, one stop bit, XON/XOFF,
and a displayed 300 ms VISA timeout. Its parent supplies serial port 21 and axis 1;
the GUI starts at the user's confirmed COM number 21 and requires explicit Connect. The Python
transport uses Newport's documented CRLF framing and the same serial parameters.

## Physical mode starts with inspection

If Physical mode reports missing PyVISA, close the GUI and double-click
`src/hhg_control/tc_motor/setup_physical_mode.vbs`. It opens a visible setup console
so errors can be read. The equivalent command from the project root in PowerShell is:

```powershell
.\src\hhg_control\tc_motor\run_TC_motor.bat setup
```

The setup command installs `pyvisa>=1.14,<2` and `pyserial>=3.5,<4` (matching
the `hardware-smc100` extra) into this checkout's `.venv` without reinstalling the application,
then checks imports and the system VISA runtime in a separate process. It does not
enumerate/open serial ports or command hardware. It requires internet access for
any missing Python packages. Installation failure stops before the runtime check.
PyVISA is a Python package; it does not include the native VISA runtime. If the
check reports that the runtime is missing, VISA mode needs NI-VISA repair, while
Direct serial remains available if pySerial imports. Installing `pyvisa-py` is not a substitute for VISA mode's
reviewed exclusive-access requirement. A passed check proves library loading,
not device availability or successful locking. Restart the GUI after setup.
Mock mode does not require this setup or a VISA installation.

A system VISA backend supporting exclusive serial-resource access is required in VISA mode
(normally NI-VISA already used by LabVIEW). The driver requests an exclusive lock;
it does not fall back to shared access if the backend cannot provide it. Close
LabVIEW/Newport utility sessions controlling this port before Python connects.
Do not run two controllers of the same axis concurrently.
Direct serial instead uses pySerial's exclusive Windows COM handle and requires
no VISA runtime. Other platforms are intentionally refused for this transport.

Select **Physical**, enter the COM port **number only** (default 21) and axis,
leave the profile blank, and click Connect only when authorized to query the physical controller.
No ports are enumerated or opened automatically. The driver queries version,
stage reference, state, position, encoder increment, velocity, acceleration,
software limits, and compensation. The activity log records native configuration.
Until reviewed calibration is supplied, position is labelled **controller units
(mm unverified)**; motion is locked. `get_position()` also refuses to return mm.

Connection and inspection never reset, enable, home, zero, download actuator
parameters, change units, or change limits/speed/acceleration. The physical motion
interface deliberately has no configuration setters. Existing settings are read
back and checked before each move; unsafe or changed values cause refusal.

### Serial fault and safe exit

A malformed or non-ASCII controller reply latches communication as uncertain.
Automatic position polling stops after the first failed read, and physical motion
stays locked. This protects against a repeated-error loop and against using an
untrusted position. Check the serial cable, controller power, grounding/EMI, and
VISA serial settings before another physical session; a successful connection
alone does not prove the link will remain reliable.

The receive path now follows the supplied LabVIEW `read_write.vi`: wait in
10 ms intervals for available serial bytes, read exactly that count, assemble
one complete CRLF frame, then validate ASCII and the expected address/command.
It replaces PyVISA's immediate large blocking text read. VISA serial input/output
termination modes, XON/XOFF characters and NUL handling are explicit rather than
inherited. A single total deadline (300 ms by default) and 256-byte reply limit
bound partial/silent/malformed replies. STOP writes do not wait for this polling.
Corrupt data is never converted to a guessed position, flushed and retried, or
accepted by stripping high bits. Errors include received hex bytes and resource.
This corrects differences in Python's receive implementation; whether they
caused the lab's invalid bytes needs a physical run of the updated code.

The 2026-10-26 physical report disproved that the receive-only change alone
resolved the issue: after two valid position reads, VISA returned corrupted raw
bytes. Both transports now enforce a 60 ms gap between query sends (a conservative
project guard, not Newport's stated minimum), while STOP writes remain immediate.
Each query records host serial settings; detected drift blocks transmission and
is recorded without automatically rewriting settings. The VISA report reads VISA
attributes; the direct report labels pySerial's configured attributes explicitly.

The connection checker saves VISA library/version, actual host serial attributes,
each reply, raw receive bytes, durations and cleanup outcome. An alternative to
the VBS launcher, from the checkout root, is:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m hhg_control.tc_motor.check_serial_link --physical --port 21
```

Add `--transport compare` to compare both, or `--transport serial` for an explicit
Direct serial check. No argument selects VISA for command-line compatibility;
the VBS launcher explicitly requests comparison.

The tool never scans other ports and aborts after the first failure without a
retry. Generated reports stay under the motor folder and are ignored by Git.

Newport command error `M` means the controller rejected a command while its
internal state was MOVING. A failed inspection connection releases its port and
permits a fresh attempt; this code sends no move during Connect. `M` alone does
not establish visible motion at the time the GUI displays the error. A byte such
as `0xCA` in an ASCII `TP` position reply is invalid protocol data, so the GUI
does not guess a position or silently retry a command on that stream.

For an inspection-only session that never commanded motion, **Stop & disconnect**
or window close releases the COM handle after a serial fault without sending a
motor command. The message explicitly says the current standstill could not be
reconfirmed. For a session with an enabled motion profile, failed STOP verification
keeps the window open. After checking that the stage is physically stationary and
holding, use **Release COM port after physical standstill check…** and type
`STATIONARY`. This recovery closes only the COM handle; it sends no new command and
does not claim software STOP succeeded. It appears only after communication loss
and a failed disconnect attempt. If physical standstill/holding is uncertain,
use the lab's independent stop procedure before releasing the connection.

## Required calibration and apparatus review

The nominal 50 mm actuator stroke does not establish a safe mounted travel range,
nor does it establish coordinates of 0–50 mm. Homing moves to a reference switch
and may sweep much of that stroke; it remains unavailable until a separate safe
procedure is established. The controller must already be referenced and READY.

Before enabling physical motion:

1. Confirm the actual controller is SMC100CC, the resource/address, attached LTA-HS,
   and whether the photographed serial is the installed unit. `VE` may report only
   a generic `SMC` version; it does not independently prove the CC model or serial.
2. Obtain the actuator's calibration/control report. Newport explicitly calls for
   the true, stage-specific encoder increment for LTA-HS/HL. Verify `SU?` matches
   that increment **in mm per count**, and verify displacement/sign against a
   suitable physical reference. Reading SU alone does not prove its units.
3. Establish the current reference/zero and safe mounted motion interval, cable
   slack, speed, acceleration, and worst-case stopping clearance while supervised.
   Read-back velocity/acceleration must not exceed the reviewed profile maxima.
   The code does not invent LabVIEW speed/acceleration values absent from the
   screenshots. The 20 mm/s² profile ceiling is a project cap, not a claimed
   manufacturer acceleration limit. The 5 mm/s ceiling follows LTA-HS specifications.
4. Keep the target envelope inside the controller's limits by at least the reviewed
   stopping clearance. The code also checks encoder rounding cannot move the target
   outside either bound or the per-move maximum. Clearance is an experimentally
   reviewed quantity, not a computed guarantee against a collision.
5. Review backlash/hysteresis. Initial physical motion refuses nonzero `BA?`/`BH?`
   because compensation can add excursions beyond requested motion. It does not
   erase calibrated compensation. Supporting such motion requires a separate
   excursion/safety review. The backlash sticker is not sufficient by itself.

Copy `src/hhg_control/tc_motor/docs/motor_safety_profile.template.json` to your own setup file and fill it
only with verified values; its null/false fields intentionally prevent motion.
Units, controller type, and mounting-clearance confirmations require actual review.
Provide the exact `VE` payload after its axis/command prefix, and the exact `ZX?`
stage reference (`LTA-HS`). A profile does not establish physical calibration by
asserting booleans: acceptance measurements must support it.

Select the reviewed JSON before connecting. Identity, calibration, limits,
compensation, and readiness must pass. A fault or malformed/misaddressed response
latches movement off. Do not retry PA automatically. Query timeouts make response
synchronization uncertain; STOP is still attempted, but standstill may be
unverifiable through that session. Resolve the physical/communication condition
instead of suppressing the close guard. No physical acceptance has been performed.

## Reuse in measurement loops

Code lives under `hhg_control.tc_motor.drivers` and `hhg_control.tc_motor.sequencer`.
The GUI only uses the sequencer; neither driver nor sequencer imports Qt or camera
code. The same base contract is used by the mock and physical adapter.

```python
from hhg_control.tc_motor.drivers.base import MotorLimits
from hhg_control.tc_motor.drivers.mock import MockMotor
from hhg_control.tc_motor.sequencer import MotorSequencer

# Synthetic example only; never copy these limits into a physical profile.
motor = MockMotor(MotorLimits(0, 10, 1, 2, 2))
sequence = MotorSequencer(motor)
try:
    sequence.connect()
    for target_mm in [0.2, 0.4, 0.6]:
        status = sequence.move_to(target_mm, timeout_s=10, settle_s=0.1)
        # Call your detector here. status.position_mm is the arrival readback.
finally:
    sequence.disconnect()
```

For physical use substitute `SMC100Motor(resource, axis=1,
profile=SafetyProfile.from_json(path))` after authorization/review. The sequencer
also offers `scan(positions_mm, measure_callback, ...)`: prevalidates the entire
path, waits for READY/arrival/settling, and passes status to the callback. Results
are unmodified and paired with readback in `ScanPoint`; no acquisition data is
processed or overwritten. Exceptions retain any callback-returned partial results.

`request_stop()` is thread-safe and performs no I/O. Other operations are serialized
and concurrent moves are rejected rather than queued. Both `request_stop()` and
an external `cancel_event` are checked during driver preflight, including immediately
before dispatch. The total deadline includes preflight; expired operations cannot
start a late move. Detector callbacks must have their own bounded waits
and cancellation. A blocked arbitrary callback cannot be interrupted by the motor
worker. Default arrival tolerance is 0.001 mm and settling is 0 s; choose these for
the measurement. Neither is a physical accuracy/settling claim. GUI refresh is
2 Hz while idle; motion polling is bounded and sleeps between reads.

## Driver survey and protocol references

- [Newport SMC100 manual, EDH0206En2054](https://www.newport.com/medias/sys_master/resources/h6d/h75/9954230960158/SMC100CC%20and%20SMC100PP%20-%20User%20Manual/SMC100CC-and-SMC100PP-User-Manual.pdf): query-only `TP`, `TS`, `TE`, `VE` have no `?`; configuration queries do. READY states are 32–35. TS/TE clear error registers, so observed faults are latched locally. Addressed PA/ST are the only motion-changing commands emitted.
- [Newport encoder calibration note](https://www.newport.com/medias/sys_master/npresources/hbe/hb1/10079772672030/DS-122502%20Encoder%20Res%20Calib%20Tech%20Note/DS-122502-Encoder-Res-Calib-Tech-Note.pdf): stage-specific encoder calibration, including LTA-HS/HL.
- [Newport LTA specifications](https://www.newport.com/f/precision-motorized-actuators-lta-series): nominal capabilities, distinct from apparatus-safe settings.
- [PyMoDAQ Newport plugins](https://github.com/PyMoDAQ/pymodaq_plugins_newport): existing SMC100 plugin, documented tested with SMC100PP/URS150. Its individual implementation could not be retrieved in this review; no claim is made about its initialization behavior. Installing the full framework solely for this independent window would add unrelated orchestration; this adapter instead follows Newport's protocol with focused transport tests.
- [PyMeasure Newport drivers](https://pymeasure.readthedocs.io/en/latest/api/instruments/newport/index.html): ESP300 support found; not a confirmed SMC100CC replacement. QCoDeS search did not establish a matching, validated CC/LTA-HS driver. Newport's utility/LabVIEW/.NET support exists; no official offline SMC100 simulator was located.
- [PyVISA resource manager](https://pyvisa.readthedocs.io/en/stable/api/resourcemanager.html): backend managers can be shared; this driver closes only its own serial handle so other instruments remain connected.
- [pySerial native-port API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html) and [Windows implementation](https://github.com/pyserial/pyserial/blob/master/serial/serialwin32.py): nonblocking reads, bounded writes and Windows share-mode-zero COM ownership. This generic alternative removes VISA from the direct path without replacing the guarded instrument driver.
- [Community pySMC100](https://github.com/freespace/pySMC100): reviewed for its direct pySerial use and explicit corruption detection. Its command delays, retry loops, homing and configuration routines are not adopted wholesale; our query gap is an independent compatibility guard.

Tests use project mocks and synthetic serial peers. Passing them establishes
software behavior only. Verify physical identity, units, short motion in both
directions, STOP distance, holding, limits, disconnect, and communication-failure
behavior under supervision before measurement use. Record that acceptance separately.
