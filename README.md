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

Launch the GUI with `hhg-camera` or `python -m hhg_control.ui.camera_gui`.
The GUI defaults to the simulated camera. Selecting the physical camera never
silently falls back to simulated data.
Press GO to start live view; RUNNING is a status indicator and only STOP ends
live view. After STOP, choose another camera source; the previous camera is
disconnected before the next GO connects the selected one.
The bright color bar touches the image, and its adjacent endpoint numbers are editable: click the top
number for maximum ADU or the bottom number for minimum ADU, then press Enter.

On Windows, double-click `run_camera_gui.vbs` to open this checkout without a
Command Prompt window. It uses this checkout's `.venv` and `src` directory and
shows a setup message if `.venv` is missing. Keep `run_camera_gui.bat` for
troubleshooting: it leaves a console open when startup fails.

## Validation

Run offline tests before each lab deployment:

```powershell
python -m pytest -q
```

The physical driver, readout-mode switching, timing, trigger behavior, and ROI
must additionally be verified on the benchtop with conservative settings before
automated scans. Offline tests are not hardware acceptance.

## Data

Scan steps are written as versioned HDF5 files with raw `uint16` images ordered
as `[frame, y, x]`, acquisition settings, ROI, and per-frame metadata. Files are
first written with a `.partial` suffix and renamed only after successful close.
Incomplete `.partial` files should be retained for diagnosis, not analyzed as
completed measurements.

The current camera-file layout and MATLAB reading notes are documented in
[docs/hdf5_camera_schema.md](docs/hdf5_camera_schema.md). The implementation uses
bounded frame batches, small gzip-compressed chunks, and a separate file per
scan step; changing to a different HDF5 hierarchy would require a reader
migration and a new schema version.

## Offline storage stress test

Run the synthetic storage benchmark **on the acquisition PC**, with the output
directory on the drive intended for real scans. It never connects to the camera.
This example writes 1,000 full-resolution frames (about 11.1 GB raw) per case,
one case at a time, then removes only its own data files. It leaves a JSON report.
Check available space first; the script also refuses to start if its minimum
free-space check fails.

```powershell
python -m scripts.stress_camera_storage --output-dir D:\camera_benchmark --frames 1000
```

Start with `--frames 20` to check the setup. The default `--pattern noise`
uses hard-to-compress 16-bit data; repeat with `--pattern low_entropy` to see
how compression changes the result. `--width`, `--height`, and `--batch-size`
can model an ROI or another batch size. The report includes wall time, process
CPU time, bytes written, file count, and effective raw MB/s for six cases:

- `discard`: replay the same frame batches without writing; measures loop and
  synthetic-source overhead.
- `application`: call `CameraScanManager.acquire_and_save_step`, including its
  current gzip HDF5 writer, metadata, close, and rename.
- `hdf5_gzip`: write the same chunks with the application's gzip level and
  shuffle settings, without scan metadata or final rename.
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
`hdf5_gzip` to investigate metadata and file-finalization work.
High process CPU time relative to wall time suggests CPU work; low CPU time
suggests waiting on storage or the OS. Windows write caching can make short
runs look faster than sustained disk writes, so use enough frames to exceed RAM
cache and compare repeated runs. The replay source reuses a prepared batch and
does not model USB transfer, SDK buffers, triggers, exposure, or the GUI.
