# Motor–camera Scan

Launch `run_scan.bat` from a checkout with its `.venv`, or run
`python -m hhg_control.scan`. This opens a separate, disconnected window.
Existing camera and TC motor windows retain their layouts and controls.

## Recording boundaries and frame attribution

Each visit gets a unique recording ID. The coordinator reserves both
sequencers, moves and settles the motor, then starts a fresh finite camera
recording with `CameraScanManager.execute_scan(continuous=False)`. The motor
cannot advance until the reader has ended, the driver confirms recording OFF,
the file has closed, and sync plus source-pixel/frame-ID/context verification
have all passed. This deliberately spends time draining and checking data at
each position. Continuous camera metadata scans remain a separate workflow.

A late transfer belongs to the recording in which it was acquired. Its
assignment never uses the GUI's current delay or host arrival time. The
immutable context is frozen before acquisition, written to the HDF5 root
`recording_context_json`, repeated as `recording_context` in every frame JSON,
and included in the source manifest. Verification checks every frame context
against that frozen reference. A context mismatch fails the point and blocks
the next move. Frame IDs are consecutive within a recording; they may reset
in the next recording.

The PCO 2.2.1 implementation creates a new FIFO recorder on each iterator;
the vendor `record()` stops/deletes the previous recorder before creating the
new one. Its iterator stops on the owning SDK-reader thread, and the scan
additionally reads hardware recording OFF. Drivers without this explicit
isolated-recording contract are refused. Mock tests exercise that software
contract; physical SDK/trigger/optical acceptance is still required.

Only the coordinator may control these reserved sequencers. Other public
operations from competing threads are rejected. Same-thread moves are also
prohibited while the position hold is active. Close LabVIEW, CamWare, Newport
utilities and other controllers of these instruments before physical use.
External/manual motion outside this software cannot be prevented by a Python
reservation. Boundary readbacks and sampled motor health checks cannot prove
the physical position was constant throughout every exposure.

## Plan and controls

Enter one absolute **mm** position per line, optionally followed by a label:

```text
0.1, reference
0.2, signal
0.3, signal
0.2, signal
```

These are examples for the mock, not approved physical coordinates. The repeat
count repeats the entire ordered path. Duplicate and reverse visits are kept
as separate points. Put reference/background visits explicitly where needed;
the scan performs no subtraction or image correction. The full remaining path
and every transition, including repeat boundaries and the initial readback,
are validated against the motor's reviewed limits before any scan move.

Choose frames per position, exposure, trigger, shutter, ROI, settling interval,
arrival tolerance, and motion/acquisition/storage/cleanup deadlines. Exposure
is displayed in ms and stored in seconds. ROI is zero-based `x0,y0,x1,y1`,
with exclusive upper bounds. Blank ROI retains the connected camera's ROI.
External Exposure Start requires actual input pulses and an enabled trigger
input; its visible enable checkbox preserves the camera's existing edge
polarity. Changing to Physical clears that checkbox for explicit review.
Missing pulses fail explicitly. The mock does not model electrical
trigger acceptance or a physical dependence on delay.

Mechanical position is always recorded in mm. Optional signed **fs/mm** and
optical-zero **mm** fields enable `requested_delay_fs = (target_mm - zero_mm)
* fs_per_mm`. Both are required together. No optical geometry, pass count,
direction, or zero is inferred. Requested optical delay is a calibrated
setpoint, not an independent optical-delay measurement.

The motor remains at its final position on success. There is no home, zero,
automatic return, automatic motion retry, or automatic scan restart.

The window starts with project mocks. Changing modes clears example positions,
ROI and calibration fields. Physical mode requires an explicit serial resource,
reviewed safety-profile JSON, calibrated/READY controller, exact camera serial
(lab default `61008541`), and acknowledgement of the setup and visible settings.
No physical limits are inferred from the mock. Follow the
[motor calibration/acceptance guide](../src/hhg_control/tc_motor/docs/motor_smc100.md)
and [camera validation plan](camera_lab_validation_plan.md) before using it.

## Storage and recovery

The selected parent gets a unique `scan_<UUID>` directory with `run.json` and
`point_000000/attempt_0001_<recording-ID>/` directories. Every attempted visit
retains its own schema-2.0 raw HDF5, pixel manifest and verification sidecar.
`/images` stays unchanged `uint16 [frame,y,x]`; `/frame_metadata/json` remains
one JSON object per frame. Added context does not change the storage hierarchy.
`scan_parameter_unit="mm"` is machine readable. Existing camera-only outputs
and their metadata remain compatible.

The run manifest records plan hash, point/repeat/visit/attempt/recording IDs,
labels, motor identity/inspection, actual camera settings, pre/post readbacks,
UTC and process-local monotonic boundaries, verification, cancellation and
cleanup outcomes. It also records the whole-scan effective rate: total verified
frames divided by active scan execution time. That timer includes preflight,
every motor move and settling interval, acquisition, storage, and verification;
when recovering a run, it accumulates active execution segments and excludes
time while the scan was not running. The scan window displays this result at
completion. It is the end-to-end rate, so motor actions and per-position
recording barriers reduce it as they do in a real scan. Monotonic values from
different process runs are not a shared absolute clock. Host receipt timestamps
are not exposure timestamps or proof of one frame per external pulse.

To recover, explicitly select the earlier run folder and the identical plan
and camera settings. Recovery holds an OS file lock, rechecks completed source
pixels and frame contexts, and refuses concurrent ownership. Verified points
are skipped by **index**, not position.
The first unfinished point starts a fresh attempt; old files are never
overwritten, concatenated or relabelled. A new attempt may repeat a position
whose earlier data were captured but not fully verified/committed.

STOP latches cancellation, prevents the next move, stops retrieval through the
reader owner, drains returned batches into the current partial file, and
requests/verifies controlled motor STOP on its owner. A timeout or fault also
stops progression. The window requires explicit STOP/failure acknowledgement
after successful cleanup; the next Start performs fresh preflight. Partial files
retain `frames_written`; unwritten allocated
tail rows are not acquired data. Partial manifests remain `.partial` and do
not establish successful verification. Storage failure can prevent saving
returned data; this is an explicit failed attempt, never a completed point.
No software can promise successful writes to a failed disk.

Cleanup does not forcibly terminate instrument threads. An unresponsive SDK
or OS file operation leaves uncertainty and the close guard active; resolve
that condition and retry cleanup before closing or starting another session.
Disconnect preserves existing camera Rolling Shutter/100 ms safety behavior.

## Offline validation and physical acceptance

The focused regression group is `src/hhg_control/scan/tests`, production capture,
streaming/integrity, camera driver contracts, and motor sequencing/safety tests.
Full-project validation includes both nested instrument and scan tests; follow
[test selection](test_selection.md). No physical connect/move occurs in them.

Physical acceptance needs a reviewed short path and an independently detectable
position/delay signature across recording boundaries, including deliberately
late USB delivery, STOP, external triggers, controller faults and restart.
Confirm every SDK-returned pixel/identity is retained and each recording starts
fresh after the previous recorder is OFF. Record that evidence separately from
mock/fake-SDK success. The frame assignment mechanism provides no pulse-count,
sensor-accuracy, continuous-position, or power-loss-persistence guarantee.
