# TC motor — separate LTA-HS / SMC100 control

All motor-specific implementation, GUI, tests, documentation, profile template,
and launcher live together under `src/hhg_control/tc_motor/`. The Python package
uses an underscore because spaces are not valid in a Python import name. Its
window is named **TC motor**. The launchers resolve the checkout from their own
location; it does not depend on the directory from which it was double-clicked.

## First setup on the lab PC

If replacing LabVIEW currently produces a serial decoding error, first close
LabVIEW and TC motor, then double-click `check_serial_link.vbs` beside the motor
launcher and enter **21**. It uses address 1 and the GUI's corrected transport
for a bounded 30-second query-only check. The console stays visible and names
the JSON file saved under `tc_motor/reports/`, including when communication fails.
Send that report for diagnosis. No move, STOP, home, enable, reset, zero or
configuration write is sent; this tool closes only its own COM handle. It cannot
stop externally initiated motion or confirm holding. A passing result establishes
only communication during the check, not calibration or motion safety. `TS`
queries clear the controller's status error register; replies are saved verbatim.

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

The setup command installs the PyVISA requirement (`pyvisa>=1.14,<2`, matching
the `hardware-smc100` extra) into this checkout's `.venv` without reinstalling the application,
then loads the system VISA runtime in a separate diagnostic process. It does not
enumerate/open serial ports or command hardware. It requires internet access for
any missing Python packages. Installation failure stops before the runtime check.
PyVISA is a Python package; it does not include the native VISA runtime. If the
check reports that the runtime is missing, install/repair NI-VISA on the lab PC
and rerun setup. Installing `pyvisa-py` is not a substitute for this driver's
reviewed exclusive-access requirement. A passed check proves library loading,
not device availability or successful locking. Restart the GUI after setup.
Mock mode does not require this setup or a VISA installation.

A system VISA backend supporting exclusive serial-resource access is required
(normally NI-VISA already used by LabVIEW). The driver requests an exclusive lock;
it does not fall back to shared access if the backend cannot provide it. Close
LabVIEW/Newport utility sessions controlling this port before Python connects.
Do not run two controllers of the same axis concurrently.

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

The connection checker saves VISA library/version, actual host serial attributes,
each reply, raw receive bytes, durations and cleanup outcome. An alternative to
the VBS launcher, from the checkout root, is:

```powershell
.\.venv\Scripts\python.exe -m hhg_control.tc_motor.check_serial_link --physical --port 21
```

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

Tests use project mocks and synthetic serial peers. Passing them establishes
software behavior only. Verify physical identity, units, short motion in both
directions, STOP distance, holding, limits, disconnect, and communication-failure
behavior under supervision before measurement use. Record that acceptance separately.
