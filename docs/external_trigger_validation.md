# External-trigger rate and timing (notebook 4H)

## Run the camera test

1. Restart the notebook kernel after updating the code; run setup. Review the
   physical source, serial **61008541**, ROI, exposure and Global Shutter.
   Close CamWare, LabVIEW and the camera GUI before connecting.
2. Open **4H**. Select the output folder, number of files/frames and nominal
   source rate. `None` means the rate is unknown. The nominal value is only a
   reference: the test never divides, slows or generates trigger pulses.
3. Review the existing physical commands before opting in: connect (vendor
   defaults reset), apply/read back exposure, shutter, ROI and External Exposure
   Start, start FIFO recording, stop, then the existing safe disconnect
   (Rolling Shutter/100 ms). A shutter change can reboot the camera.
4. Set `RUN_EXTERNAL_TRIGGER_TEST = True`, keep the reviewed external source
   running, then run just 4H. The initial source is 1 kHz, but the test accepts
   any positive nominal reference or `None`. Existing ~1 s SDK image waits still
   time out on long gaps/absent triggers; the acquisition deadline does not
   extend those waits. Ctrl+C stops cooperatively and retains partial files.
5. The standard 5 x 2,000 frames produce **6.55 GB raw** at 2560 x 128, with a
   **10 GB combined output cap** and space/RAM preflight. An oversized plan is
   rejected before connecting. The 120 s acquisition deadline starts after
   worker startup; final sync/verification/cleanup add time. Start with
   `EXTERNAL_FILES = 1`, `EXTERNAL_FRAMES_PER_FILE = 100` if wiring is unverified.
6. Send back `section_4h_*_external_2560x128.json` from the selected test folder.
   The notebook prints only minimal status/path. Detailed failure tracebacks
   remain in the adjacent `.log`; HDF5, manifests and verification sidecars stay
   in the labelled run folder. No output is deleted or overwritten.

## What is measured

This builds on 4F/4G, using the **current GUI sequencer itself**:
`CameraScanManager.execute_scan(continuous=True)`. One SDK recording spans all
files. It uses the GUI's current batch four, bounded reader, source SHA-256
references, uncompressed HDF5, independent sync and pixel-verification workers,
and final-frame preview copy. It does not use the older 4F benchmark's reader.
RAM budget/reserve come from notebook setup and are recorded; match them to the
GUI for a direct comparison. No raw pixels, frame identities or frame order
are changed. No scope/pulse-source commands are sent.

| Report field | Meaning |
| --- | --- |
| `timing.camera_timestamp` | Native PCO metadata cadence/interval distribution, only when supplied for every frame and strictly increasing. No pixel timestamp overlay is enabled. |
| `timing.host_receipt` | Intervals between SDK-image return times; includes USB/SDK and host scheduling. Not exposure time or trigger latency. |
| `rates.capture_fps` | Frames / driver iterator start-to-final stop, including recording startup. |
| `rates.saved_fps` | Frames / first-step preparation to last closed-file yield. |
| `rates.durable_fps` | Current production definition: first file capture preparation to final HDF5+manifest sync. |
| `rates.complete_verified_fps` | Frames / first-step preparation to all production pixel/frame-ID checks finishing. |
| `capture_profile`, `reader`, `files` | SDK costs, ID gaps, FIFO/errors, queue pressure and per-file write/integrity checks. Stage times overlap. |

Native camera intervals may cluster at integer multiples of the **nominal**
trigger period. That is a diagnostic pattern, not a measured rejected-pulse
count or electrical jitter. No camera timestamps means exposure cadence stays
unresolved. Consecutive recorder IDs do not count input pulses the camera did
not accept. A lower durable FPS alone does not mean lost camera frames.
`EXECUTED` means the finite software run completed; physical acceptance and
one-to-one laser-shot accounting remain unestablished. Mock results are
explicitly `MOCK_ONLY` and simulate no electrical triggers.

## Use the oscilloscope

Consult the [PCO manual, sections 7.1 and A1.3](https://www.excelitas.com/file-download/download/public/102439?filename=pco_edge_3_1__4_2__4_2_LT_and_5_5_User_Manual.pdf)
and verify your camera's connector labels before wiring. The documented input
is **Exposure Trigger, SMA #1**; exposure status is **Status Expos, SMA #4**;
**Status Busy, SMA #3** is an optional third channel. The exposure output must
be enabled and selected as **Status Expos**, not Status Line, with known polarity.
The test uses the existing default configuration and does **not** program these
I/O options; CamWare choices may be reset when Python connects. Verify the
output during the actual test. A missing output is inconclusive about exposure.

1. Observe the trigger at the **camera input** on scope CH1. Observe the camera's
   exposure-status output on CH2. A scope connected only upstream measures the
   source, not the camera response. Use high-impedance inputs/probes; confirm
   camera/scope electrical limits and grounding before connecting. Avoid
   blindly adding a 50-ohm termination.
2. Trigger the scope on the active CH1 edge. Capture repeated events while 4H
   is recording. Use enough pretrigger/time range and scope memory to retain
   both edges, including pulses without a camera response. Do not acquire only
   on CH2: that would omit nonresponses.
3. Measure CH1 active edge to CH2 exposure-start edge: mean/min/max delay and
   standard deviation across many events. Also inspect exposure pulse width.
   Account for probe/cable skew and scope time resolution. With CH3, compare
   missing responses with Busy state. Do not call timing residuals from the
   PC's unrelated clock electrical jitter.
4. Save the scope trace/settings. This is electrical exposure-status timing;
   it does not establish which optical pulse was integrated or which saved
   frame corresponds to each edge.

### Optional edge-list analysis, without rerunning the camera

Use the scope's measurements/export tools to extract **every active edge** on
both channels from the same capture/timebase, including unanswered trigger
pulses. Do not pair/filter them yourself. Preserve the original waveform.
Create a separate CSV (times in **seconds** relative to that scope capture):

```csv
trigger_s,exposure_s
0.000000,0.000050
0.001000,0.003060
0.002000,0.006040
0.003000,
```

These columns are **independent ordered lists**, not row-wise matched pairs.
Supply the valid scope window start/end and a reviewed maximum response delay
shorter than the shortest trigger interval. The optional 4H scope cell rejects
ambiguous overlapping pairing windows and reports matched delay statistics,
unanswered eligible triggers, unmatched exposures and end-censored triggers.
Its response fraction applies only to the scope capture/window and chosen
delay bound. Confirm camera recording was active throughout that window;
otherwise apparent nonresponses can simply precede arming/follow stopping.
The tool never aligns the scope clock with saved-frame times or claims complete
laser-shot accounting. Incorrect polarity, missed scope edges or an undersized
pairing bound invalidate that interpretation.

If the camera exposure output is absent after Python's default reset, send
the I/O configuration/readback details before adding any automatic I/O changes.
