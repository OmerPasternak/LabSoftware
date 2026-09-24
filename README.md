# HHG Control

Python instrument-control software for the laboratory pco.edge 5.5 camera. The
project separates camera drivers, scan sequencing and storage, and the PyQt GUI.
Every hardware-facing implementation has a mock counterpart for offline work.

## Supported environment

Use 64-bit Python 3.12 for the laboratory installation. The Excelitas `pco`
package currently declares support through Python 3.12; Python 3.13+ must not be
used for physical-camera operation until Excelitas supports it and the lab has
completed a benchtop validation.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Launch the GUI with `run_camera_gui.vbs`, `run_camera_gui.bat`, or
`.\.venv\Scripts\python.exe -m hhg_control.ui.camera_gui`. An unqualified
`python` can import a different checkout if another copy is installed.
The GUI defaults to the simulated camera. Selecting the physical camera never
silently falls back to simulated data.
Press GO to start live view; RUNNING is a status indicator and only STOP ends
live view. After STOP, choose another camera source; the previous camera is
disconnected before the next GO connects the selected one.
The mode selector offers Rolling Shutter and true Global Shutter on the
pco.edge 5.5 USB. Changing the physical sensor mode reboots the camera; verify
the selected mode on the camera before an experiment. Global Reset is a distinct
legacy API mode and is not the GUI's Global Shutter selection.
The bright color bar touches the image, and its adjacent endpoint numbers are editable: click the top
number for maximum ADU or the bottom number for minimum ADU, then press Enter.

On Windows, double-click `run_camera_gui.vbs` to open this checkout without a
Command Prompt window. It uses this checkout's `.venv` and `src` directory and
shows a setup message if `.venv` is missing. Keep `run_camera_gui.bat` for
troubleshooting: it leaves a console open when startup fails.

## Validation

Run offline tests before each lab deployment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The physical driver, readout-mode switching, timing, trigger behavior, and ROI
must additionally be verified on the benchtop with conservative settings before
automated scans. Offline tests are not hardware acceptance.
Use [the camera lab validation plan](docs/camera_lab_validation_plan.md) for
the first physical-camera session and its acceptance checks.

## Data

Scan steps are written as versioned HDF5 files with raw `uint16` images ordered
as `[frame, y, x]`, acquisition settings, ROI, and per-frame metadata. Files are
first written with a `.partial` suffix and renamed only after successful close.
Incomplete `.partial` files should be retained for diagnosis, not analyzed as
completed measurements.
The GUI activity log reports each scan's active measurement duration after all
steps are saved. A stopped scan reports elapsed active time, and a resumed scan
adds subsequent active time without counting the pause.
Scan log entries identify SIMULATED or PHYSICAL acquisition. Simulated scan
duration includes synthetic image generation and cannot establish the physical
camera's maximum frame rate.
If live view was running when a measurement starts, the GUI pauses its camera
reader for the scan and restarts live view after a successful measurement. The
last saved frame is displayed after each scan step. STOP, scan errors, and window
close do not restart live acquisition; GO can start it manually afterward.

The current camera-file layout and MATLAB reading notes are documented in
[docs/hdf5_camera_schema.md](docs/hdf5_camera_schema.md). The implementation uses
bounded frame batches, per-frame HDF5 chunks, and a separate file per
scan step; changing to a different HDF5 hierarchy would require a reader
migration and a new schema version.

For camera intensity correlations, open `scripts/analyze_camera_axis_g2.m` in
MATLAB and Run it. Select a folder containing completed `.h5` acquisitions
from one scan step. The script checks that the step index, setpoint, ROI,
exposure, and frame dimensions agree, then reads all frames one at a time.
It computes `g2Pixel(x,y)` at each pixel and sums over `y` in each frame to
form a normalized `g2Matrix(x1,x2)`; `g2X` is the matrix diagonal. Set
`yRange` to `[first last]` to limit the summed y band. `binWidth` defaults
to 1 for a pixel-by-pixel x-x matrix; increase it to reduce matrix size.
Results and the analyzed file list remain in the MATLAB workspace. These
are zero-lag **ADU intensity** correlations across frames, not time-delay
correlations or photon-count coincidences.

New scans use uncompressed HDF5 and a bounded writer queue so saving can overlap
continuous camera acquisition. A simulated camera waits if storage is slower;
a physical camera stops with a `.partial` file if the queue fills or frame
integrity fails. Gzip remains available for slower scans. Before a GUI scan,
click **Apply ROI** if the ROI controls have changed. The GUI blocks a scan if
the controls differ from the camera's active ROI or if estimated storage for
the remaining steps exceeds free space.
The Global Shutter setting is the camera's true global exposure mode, with a
100 ms maximum exposure; the GUI reads the current sensor mode on connection.

## Offline storage stress test

Run the synthetic storage benchmark **on the acquisition PC**, with the output
directory on the drive intended for real scans. It never connects to the camera.
This example writes 1,000 full-resolution frames (about 11.1 GB raw) per case,
one case at a time, then removes only its own data files. It leaves a JSON report.
Check available space first; the script also refuses to start if its minimum
free-space check fails.

```powershell
.\.venv\Scripts\python.exe -m scripts.stress_camera_storage --output-dir D:\camera_benchmark --frames 1000
```

Start with `--frames 20` to check the setup. The default `--pattern noise`
uses hard-to-compress 16-bit data; repeat with `--pattern low_entropy` to see
how compression changes the result. `--width`, `--height`, and `--batch-size`
can model an ROI or another batch size. The report includes wall time, process
CPU time, bytes written, file count, and effective raw MB/s for six cases:

- `discard`: replay the same frame batches without writing; measures loop and
  synthetic-source overhead.
- `application`: call `CameraScanManager.acquire_and_save_step`, including its
  current uncompressed HDF5 writer, metadata, close, and rename.
- `hdf5_gzip`: write the same chunks with the earlier application's gzip level
  and shuffle settings, without scan metadata or final rename.
- `hdf5_plain`: write the same batches to uncompressed HDF5; isolates much of
  the compression cost compared with `hdf5_gzip`.
- `raw_single` and `raw_per_frame`: compare one binary file with a file per frame
  to measure the file-creation concern on this drive.

Compare sustained raw MB/s with the experiment's required rate. At full sensor
size, one frame is 11.06 MB, so 20 frames/s requires 221 MB/s before format
overhead. If `raw_per_frame` is slower than `raw_single`, per-file overhead is
real on that PC, but it does not explain the current application path because
the application writes one file per scan step. Compare `hdf5_gzip` with
`hdf5_plain` to estimate compression cost; compare `application` with
`hdf5_plain` to investigate queue, metadata, and file-finalization work.
High process CPU time relative to wall time suggests CPU work; low CPU time
suggests waiting on storage or the OS. Windows write caching can make short
runs look faster than sustained disk writes, so use enough frames to exceed RAM
cache and compare repeated runs. The replay source reuses a prepared batch and
does not model USB transfer, SDK buffers, triggers, exposure, or the GUI.

## Paced capture check

The paced benchmark sends prepared frames through the production scan manager
and HDF5 writer at a requested rate. It verifies the saved frame IDs and a
marker in every image, times close/rename and `fsync`, then removes its data
file unless `--keep-data` is specified. It leaves a small JSON report in the
selected output directory. Run it on the scan drive, one case at a time:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_capture_pipeline --output-dir data\test_data --frames 10000 --width 2560 --height 56 --fps 1000
```

`pipeline_fps` includes the paced frame supply and HDF5 finalization;
`durable_fps` also includes an explicit file `fsync`. Both can benefit from
Windows or drive caching. A passing result establishes that this synthetic
software path kept up on this PC for this duration and ROI. It does not establish
the physical camera rate, USB transfer capacity, SDK FIFO behavior, or laser
synchronization. The benchmark produces frames in batches, so a physical SDK
that delivers frames individually can add per-frame costs.

For a saved physical-camera scan, inspect frame numbering and host read timing:

```powershell
.\.venv\Scripts\python.exe -m scripts.inspect_scan_timing path\to\scan_step.h5
```

The host read rate is measured after SDK image retrieval. It is not a sensor
exposure rate or a laser-shot timestamp. To establish one frame per laser shot,
also verify the camera's external-trigger configuration and hardware trigger
counts/timestamps on the benchtop. Test the intended ROI and exposure over a
long enough run to expose FIFO overflow, dropped frame IDs, and sustained
storage limits before relying on unattended acquisition.
