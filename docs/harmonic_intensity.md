# Average integrated harmonic intensity

Run from the project folder:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_harmonic_intensity.py "D:\Data\measurement.h5"
```

The first run displays **frame zero** of your saved measurement. For a triggered
measurement, this is its first recorded triggered image. Drag a rectangle around
the full harmonic, including the desired vertical extent, then click **Use this
window**. Closing the selection window cancels the operation. Subsequent runs
reuse the saved rectangle automatically.

Omit the input path to pick your measurement files in a file dialog. To choose
and save the window **before** recording the full measurement, use one saved
triggered image:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_harmonic_intensity.py "D:\Data\triggered_reference.h5" --select-only
```

This setup step saves the rectangle; the later measurement run reports the
statistics using that rectangle.

You can use a separate saved single-frame triggered reference:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_harmonic_intensity.py "D:\Data\measurement.h5" --reference "D:\Data\triggered_reference.h5"
```

The script reads saved images; obtain the triggered image using the camera
program's existing external-trigger measurement controls. No new physical
camera acquisition path is introduced.

For each image, the script sums every raw pixel inside the chosen rectangle.
It reports exactly two statistics: **Average** and **STDEV**, in summed ADU per
image. STDEV is the sample standard deviation of the individual image sums
(`N-1` denominator), measuring image-to-image fluctuations. At least two images
are required. Background, pedestal and read noise remain in the raw sums.
No acquired pixels are changed, filtered, clipped or rejected.

Pass multiple HDF5 files or a folder to pool all images from one measurement;
each image gets equal weight. The folder should contain only that measurement's
completed camera files. Scan indices and parameter labels are ignored. Partial
files are excluded from folder discovery; incomplete selected files are rejected.

- `--select`: choose a replacement rectangle from the first measurement image.
- `--window path.json`: choose a specific saved window; useful for multiple harmonics.
- `--bounds X0 Y0 X1 Y1`: set the rectangle numerically instead of drawing it.
- `--output result.json`: optionally retain both statistics, frame count, window
  and source provenance; existing report files are preserved.

Bounds are zero-based, half-open array coordinates: `images[:, Y0:Y1, X0:X1]`.
Saved image dimensions and camera ROI must match on reuse. The default rectangle
lives at `%LOCALAPPDATA%\HHGControl\harmonic_intensity_window.json` on Windows,
independent of measurement folders. `HHG_INTENSITY_WINDOW` can override this path.
The saved rectangle is separate from the existing calibrated harmonic/g2 map.
Optical realignment may require selecting a new rectangle even if the camera ROI
and image dimensions are unchanged.
