# Camera acquisition: lab validation plan

The section-by-section main-computer checklist and executable tests are in
[`application tests/lab_computer_acceptance.ipynb`](../application%20tests/lab_computer_acceptance.ipynb).
Select the project's `.venv` as the notebook kernel and run one section at a
time. Its Python helper is `scripts/lab_computer_acceptance.py`.

Use this at the first physical-camera session. Recheck the code and rerun the
offline tests after further edits. The evidence below is from 2026-09-24 and
is not a hardware acceptance result. The notebook now defaults to a centered
2560 × 128 ROI; the older 1740 × 128 results below are historical comparisons.

## Current evidence

- Camera target: pco.edge 5.5 USB 3.0, Global Shutter. The manufacturer lists
  455 fps at **2560 × 128** with a centered ROI; this is a reference, not an
  exact specification for our 1740 × 128 ROI. See the
  [manufacturer datasheet](https://www.excelitas.com/assets/product/document/pcoedge-55-usb-datasheet.pdf?file=26806.pdf).
- A GUI run at 1740 × 128, 1 ms exposure, 2 × 1,000 frames took 6.47 s, but
  its HDF5 files identify the source as `pco.edge 5.5 USB (EMULATOR)` with
  simulated timestamps. Its 309 fps overall rate says nothing about physical
  camera throughput. The emulator also synthesizes noisy images on the CPU.
- On the current test PC, prepared frames paced at 455 fps through the
  production HDF5 writer completed in 4.62 s for 2,000 frames (433 fps
  including startup/close). IDs and image markers were intact. The report is
  `data/test_data/paced_pipeline_20260923T215529_483238Z.json`. The replay
  can wait; its maximum schedule lateness was 81 ms. This is **not** proof that
  a physical FIFO can absorb every stall. Windows caching may affect results.
- Completed scan steps are uncompressed HDF5 (`uint16`, `[frame, y, x]`) with
  frame metadata. A physical scan fails on FIFO overflow, frame-number gaps,
  or a full writer queue, retaining an incomplete `.h5.partial` file.
- The **External trigger** checkbox beside exposure selects the PCO SDK's
  *External Exposure Start* mode: one fixed-duration exposure is requested per
  accepted trigger pulse. The exposure field still sets that duration. The
  setting is read back before a scan and stored as HDF5 `trigger_mode`.
  Simulation records the selection but does not wait for real pulses.
- A matched software benchmark is available with
  `./.venv/Scripts/python.exe -m scripts.benchmark_simulation_overhead`.
  It times 2 x 1,000 frames at 1740 x 128, 1 ms, using the production HDF5
  scan writer for the exposure-paced mock and prepared-frame replay. Mock
  image synthesis always follows the full requested exposure wait. Reports go
  to `data/test_data/benchmark_runs` in `BENCHMARK_ONLY_*` run folders; HDF5 files are retained for manual deletion. The
  replay isolates storage and has no exposure, USB, or camera SDK cost.

## Before the lab session

1. Recheck branch/status and run `./.venv/Scripts/python.exe -m pytest -q`
   after tomorrow's code work. Keep unrelated changes out of the validation.
2. Use the intended acquisition drive; confirm free space for the full run.
   Repeat the paced writer benchmark on the **final PC and drive** if either
   differs from this test PC.
3. Record camera serial, USB controller/port, storage drive, SDK version,
   shutter mode, applied ROI, exposure, trigger mode, and software revision.
   Select **Physical pco.edge** explicitly in the GUI and verify the HDF5
   `camera_model` and serial; never infer source from the image appearance.
4. Decide the scientific timing requirement. At 128 rows the listed USB
   Global Shutter rate (455 fps) is below a 1 kHz laser, so one saved frame
   per laser shot is not established. A smaller centered ROI or different
   interface may help, but must be verified on hardware. Configure and verify
   the intended external trigger before claiming laser-shot synchronization.
   On the bench, confirm the actual trigger connector, electrical levels,
   polarity, and timing from the installed camera/manual before connecting the
   laser timing source. Test a slow known pulse train first, then compare pulse
   and saved-frame counts before increasing rate.

## Controlled physical trial

1. Begin with the intended 1740 × 128 centered ROI and 1 ms exposure. One
   millisecond is shorter than the approximately 2.20 ms period at 455 fps;
   shortening exposure alone is unlikely to remove a readout/transfer limit.
   With external triggering, no frame will arrive until a valid pulse does;
   missing or too-fast pulses may cause a timeout or dropped-shot count.
2. First acquire a short safe run, then 1,000 frames, then a sustained run
   long enough to expose storage stalls. Compare against pco.camware at the
   **same** ROI, shutter, exposure, trigger, USB port, and PC when possible.
3. After each run inspect the saved scan:

   ```powershell
   .\.venv\Scripts\python.exe -m scripts.inspect_scan_timing data\<scan_step>.h5
   ```

   Check `complete`, requested/written counts, consecutive recorder frame IDs,
   timestamp source, host-read median/p99 intervals, FIFO/queue errors, and
   any `.h5.partial` file. Host-read intervals are software observations, not
   sensor exposure or laser timestamps. Use camera or trigger timestamps/counts
   to test pulse synchronization.
4. Separate camera acquisition rate from total GUI scan duration, which also
   includes file close, display updates, and scan-step overhead. If physical
   capture is slow, compare the camera SDK/camware baseline with our saved run
   before changing the writer. If capture is fast but the queue fills, profile
   the writer and storage on that PC before changing FIFO or batch settings.

## Acceptance for research use

Require complete files, exact expected frame counts, consecutive hardware
frame IDs, no FIFO/queue errors, and sustained throughput close to a physical
same-settings camera baseline. For one frame per laser shot, additionally
require verified trigger timing and one-to-one shot/frame accounting. Do not
use emulator scan duration or the 455 fps paced replay alone as acceptance.

The current sequencer records scan setpoints as metadata; it does not command
a physical delay stage. Validate any stage integration separately.

## Approved software safeguards

The GUI owns camera configuration; the vendor SDK default reset on connection is intentional.
Enter the physical camera serial from its label once. After a verified connection,
the GUI remembers it in `data/camera_selection.json` and uses it in later sessions;
an unavailable saved camera produces an error rather than selecting another device.
Shutter changes reboot
that same camera and restore verified ROI, exposure and triggering. Physical commands
run in workers under the sequencer lock. Failed cleanup is reported as an unknown state.
Configuration blocks conflicting button clicks and mouse/keyboard edits until completion,
while retaining the image, live labels and control appearance. STOP remains available.
The camera reader briefly pauses where required; Qt painting and STOP remain responsive.
Exposure is limited to 2 s in rolling/global-reset and 100 ms in Global Shutter,
and intersected with reported SDK limits. Scan and export creation refuses overwrites.

Saved acquisition uses bounded batches, a disk preflight and a once-per-second free-space
check reserving 100 MB. Whole-stack acquisition checks RAM before allocation. Synthetic
benchmarks retain labelled output, and notebook test-process launch is opt-in
(`CONFIG.run_offline_tests = True` or `--run-offline-tests`).

These are offline-verified software protections; physical timing and device behaviour
still require the controlled bench validation above.
