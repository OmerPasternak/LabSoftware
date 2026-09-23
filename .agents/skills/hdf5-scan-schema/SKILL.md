---
name: hdf5-scan-schema
description: Standardizes experimental data persistence in HDF5 with complete metadata, multi-dimensional scan layouts, and native MATLAB compatibility (h5read/h5info). Use when saving or loading scan data, defining scan file structures, or ensuring MATLAB interop.
---

# HDF5 Experimental Scan Schema & MATLAB Interoperability

This skill defines standard HDF5 dataset structures, metadata attributes, and formatting rules to guarantee complete experimental reproducibility and seamless data analysis in both Python and MATLAB.

## Current camera format

The implemented camera writer uses schema 2.0, documented in
`docs/hdf5_camera_schema.md`. It saves one scan step per file with a root
`/images` dataset ordered `[frame, y, x]`, root acquisition attributes, and
`/frame_metadata/json`. Keep this layout for existing camera scans. The
`/entry/...` structure below is a possible future multi-instrument design,
not the current reader contract; adopting it requires a new schema version
and a reader migration.

---

## 1. File Structure & Hierarchy

Experimental scan files should follow a clean, hierarchical layout:

```text
/
├── entry/
│   ├── metadata/
│   │   ├── timestamp_start       # ISO 8601 string
│   │   ├── timestamp_end
│   │   ├── operator              # User name
│   │   └── description           # Notes on the scan
│   ├── laser/
│   │   ├── wavelength_nm         # e.g., 800.0
│   │   ├── repetition_rate_hz    # e.g., 1000.0
│   │   ├── pulse_energy_uj       # e.g., 850.0
│   │   └── pulse_duration_fs     # e.g., 30.0
│   ├── scan/
│   │   ├── type                  # "1D_delay", "2D_delay_power", "spectrum_vs_delay"
│   │   ├── axis_names            # ["delay_stage"]
│   │   ├── axis_units            # ["fs"]
│   │   └── setpoints             # Array of scan target values
│   └── data/
│       ├── raw_images            # 3D dataset [n_steps, height, width] or 2D [n_steps, spectrum]
│       ├── actual_positions      # Readback positions per step
│       └── timestamps            # Acquisition epoch per step
```

---

## 2. MATLAB Compatibility Best Practices

To make HDF5 files natively readable in MATLAB with zero friction (`data = h5read('scan.h5', '/entry/data/raw_images')`):

1. **Array Dimensionality & Transposition**:
   - Python uses C-order (row-major), while MATLAB uses Fortran-order (column-major).
   - If Python saves a frame array of shape `(100, 2048, 2048)` (Steps, Height, Width), MATLAB `h5read` will read it as `(2048, 2048, 100)`.
   - Store explicit axis labels as HDF5 attributes on the dataset:
     ```python
     dset.attrs["axes"] = ["steps", "height", "width"]
     ```
2. **Strings**:
   - Save strings using UTF-8 or ASCII string datatypes:
     ```python
     import h5py
     # h5py strings
     dset.attrs["unit"] = "fs"
     ```
3. **Data Compression & Chunking**:
   - Use `gzip` (level 4) or `lzf` for image stacks:
     ```python
     f.create_dataset(
         "/entry/data/raw_images",
         shape=(n_steps, height, width),
         dtype="uint16",
         chunks=(1, height, width),  # Chunk per frame for fast streaming
         compression="gzip",
         compression_opts=4
     )
     ```
   - Chunking `(1, height, width)` allows incremental appending as each scan step finishes without reading the entire dataset into memory.

---

## 3. Atomic Writing & Long-Scan Crash Resilience

For scans taking hours, never keep data only in memory. Flush data to disk after every step:

```python
with h5py.File("scan_0042.h5", "w") as f:
    dset = f.create_dataset(...)
    for step_idx in range(n_steps):
        # acquire frame...
        dset[step_idx, :, :] = frame
        f.flush()  # Ensures data is safely written to disk if power or process dies
```

