# Spectral covariance and harmonic ranges

The camera HDF5 schema stores images as `/images[frame, y, x]`. In this setup,
the detector x axis is the frequency-dispersed axis (the calibration may be in
wavelength; these tools use detector pixel coordinates only).

## X-pixel covariance

Run `python scripts/analyze_spectral_covariance.py <step.h5>` for one completed
step, or pass a directory containing repeat files from one scan step. Directory
inputs are checked for matching image dimensions, step index, setpoint,
exposure, ROI, readout mode, and camera serial. A mixed-step folder is rejected.
The script streams the image files and calculates the unbiased sample
covariance across frames between x pixels after summing the selected y rows.
The optional `--y-range START STOP` is zero-based and half-open; omitting it
uses all y rows.

The covariance is in ADU² and is saved in an analysis HDF5 file under
`/frequency_pixel_covariance_adu2`. The file also records the x pixel
coordinates, mean spectrum, source files, selected y range, and scan metadata.
A heatmap PNG is saved beside the HDF5 result and displayed. The matrix is
`[x_pixel_i, x_pixel_j]`; it is covariance across frames, not Pearson
correlation, time-lagged covariance, or a photon-count-normal-ordered g².

In the camera GUI, **X Covariance** selects one completed step file and launches
the analysis without blocking acquisition controls.

## Mark harmonic ranges

Run `python scripts/mark_harmonic_limits.py <step.h5>`. The script displays the
mean of all frames in that completed step as the snapshot. Drag across x to add
each harmonic range in order; ranges are named H1, H2, and so on. Press `U` to
undo the most recent range, `S` or Enter to save, and `Q` or Escape to close
without saving. The saved `<step>_harmonics.json` sidecar uses zero-based,
half-open x ranges relative to the `/images` array, and stores the source file,
array dimensions, ROI, the annotated snapshot PNG path, and creation time. The
image and sidecar are saved beside the source file. If that sidecar already
exists, a numbered filename is selected rather than overwriting it.

The GUI's **Mark Harmonics** button opens a selected completed step in this
tool. Downstream per-harmonic analysis should check the saved image dimensions
and ROI against its input before applying the ranges. The sidecar stores no
wavelength calibration; its limits remain detector x-pixel positions.
