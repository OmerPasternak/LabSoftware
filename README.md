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

On Windows, `run_camera_gui.bat` opens this checkout using its `.venv` and
`src` directory. It reports a setup message if `.venv` has not been created.

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
