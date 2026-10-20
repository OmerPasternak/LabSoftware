# Spectral Pearson correlation and harmonic ranges

These are explicitly requested derived analyses. Source images are immutable:
open measurements read-only and save results separately. No analysis correction
may enter acquisition. See [the raw-data policy](raw_data_integrity_policy.md).

The camera HDF5 schema stores images as `/images[frame, y, x]`. In this setup,
the detector x axis is the frequency-dispersed axis (the calibration may be in
wavelength; these tools use detector pixel coordinates only).

## X-pixel Pearson correlation

The GUI **X Pearson** button reads one completed HDF5 step and opens a Pearson
correlation heatmap automatically. Its range is -1 to +1, with diagonal 1 for
nonconstant x pixels. Constant pixels have undefined correlation (NaN, shown in
gray), including their diagonal. This is not g2 or a covariance matrix in ADU².

For each frame, sum the selected y rows into an x spectrum, then calculate
`r_ij = Cov(S_i,S_j) / sqrt(Var(S_i)*Var(S_j))` across frames. No smoothing,
background removal or correction is applied to the source pixels or profiles.
All computations produce separate derived arrays. Source HDF5 files are opened
read-only, never rewritten.

The compatible CLI filename is still
`python scripts/analyze_spectral_covariance.py <step.h5>`. A folder argument
accepts repeat files from the same step with matching dimensions, settings,
step index and setpoint; mixed steps are rejected. `--y-range START STOP` selects
a zero-based half-open y interval; the default uses all rows. `--no-plot` is
available for automation; the GUI always opens the plot.

New result files are named `*_spectral_pearson_*.h5` and contain
`/frequency_pixel_pearson_r` (dimensionless `[x_i,x_j]`), x coordinates,
`mean_spectrum_adu`, source metadata, constant-pixel count and computation timing.
A PNG heatmap is also saved. Existing covariance files are not overwritten or
relabeled. The old numerical covariance API remains available for compatibility.

The optimized path sums into float64 directly instead of making a float64 copy
of the entire raw batch. It combines reduced spectra in bounded matrix blocks
(default 256 frames), reducing repeated full-matrix updates. At 2560 x pixels,
a 256-frame reduced block is about 5 MiB, independent of scan length. Raw image
reads remain separately bounded (`--batch-size`, default 4). The optional
`--spectra-block-frames` controls the reduced-spectrum block. Stages are recorded
in `timing_s_json`; output-file compression and plot rendering are additional.

## Automatic harmonic windows and persistent reference

**Mark Harmonics** selects a saved HDF5 reference step. It averages the saved
frames, integrates that image along y, and proposes harmonic x windows. It never
connects to the camera or changes acquisition settings. One saved integrated
frame is also a valid marking input. The reference should include all harmonics
of interest (normally BSV ON); a coherent-only image cannot reveal absent peaks.

The review graph colors the line itself: dark blue inside selected windows, light gray outside. There are no background strips.
Only for positively identified saved mock-camera data, when the image has a clear vertical spectral trace, detection uses a zero-sum spatial projection to reject column-dependent background common to all rows. Cropped/flat traces fall back to a y sum. This affects detection only: the graph still displays the raw y-integrated spectrum, and saved pixels are unchanged. The detector then uses a rolling lower-quantile baseline, robust noise estimation,
local prominence and noise-limited tails. It deliberately does not threshold
relative to the strongest peak, which could remove weak even/half-integer peaks.
Noise and overlapping tails prevent a guarantee of complete, noise-free windows.
No uniform detector-pixel spacing is assumed. Physical harmonic orders are assigned from one user-confirmed odd reference, assuming consecutive half-omega steps as specified for this experiment.

- Significance is the hidden `HARMONIC_SIGNIFICANCE_SIGMA = 5.0` constant.
- Drag across x to add a missing window or replace overlapping windows.
- Right-click a window to remove it. Check weak peaks, tails and hot-pixel artifacts.
- Click **Save** (or Enter on the plot), identify one labelled peak known to be
  an odd harmonic, and enter its odd order. All other orders are inferred at
  0.5-omega intervals, increasing from right to left. No per-peak number entry.
  Ensure no intermediate window is missing or spurious: that would shift the
  inferred family and therefore the SI reference selection.
- Escape cancels and preserves the old map. Cancelling reference entry also
  leaves the previous map unchanged.

The single default reference is `%LOCALAPPDATA%/HHGControl/harmonics.json` on
Windows. The confirmed map survives restarts and is reused automatically until
Mark Harmonics saves a replacement. An atomic replacement preserves the previous
map if saving fails. It records the source, shape, ROI when available, detection
parameters, right-to-left peak indices, inferred orders, odd-reference calibration, review flag and review image path. Runtime reference
files are machine/user-specific calibration, outside Git, not shared defaults.
`HHG_HARMONIC_MAP` overrides its path; the marker CLI also accepts `--output`.

Do not reuse a map after changing spectral alignment or detector geometry without
reviewing it again. Shape/ROI validation cannot detect optical drift.

## SI-corrected per-harmonic light statistics

The GUI **Harmonic g²** uses the persistent map without a map picker. Select three
saved files in order: **BSV ON**, **BSV OFF** (coherent-only), **BACKGROUND**
(no XUV). These must be matched in settings, alignment, gain, timing and delay
where relevant. Reference and signal frame counts may differ. A mean image is
sufficient for marking but not for statistical analysis: each statistics input
needs a stack of individual shots (at least three; realistically many more).
Each ON file/scan delay is analyzed separately; do not concatenate different delays.

Command line:

```powershell
python scripts/analyze_harmonic_g2.py ON.h5 --bsv-off OFF.h5 --background BG.h5
```

A positional map path remains available as an explicit CLI override. The analysis
runs offline in a child process and streams all three datasets in bounded batches.
New and legacy files with `/images[frame,y,x]` are supported. Incomplete files are
rejected; absent legacy metadata produces warnings. Different dataset names,
MATLAB `[x,y,frame]` arrays, TIFF and other formats require an explicit converter;
no shape guessing or silent transposition is performed. Original data are read only.

### Estimator and source

Reference: Even Tzur et al., *Attosecond-resolved quantum fluctuations of light
and matter*, arXiv:2511.18362v1, SI **MCP response and g2 calibration** and
**Background subtraction**, PDF pp. 27–30 (SI S4–S7), Eqs. S8–S11.

1. Accumulate per-pixel means and unbiased sample variances across shots.
2. For odd integer orders use BACKGROUND; for even/half-integer orders use BSV
   OFF, following the SI's treatment of leakage from bright odd harmonics.
3. Form `mu = mean(ON)-mean(reference)` and
   `v = var(ON)-var(reference)`. References are independent runs; they are not
   subtracted shot by shot. A separate background variance is NOT subtracted a
   second time from BSV OFF. Never clip negative frame signals to zero.
4. For usable pixels form `g_detected = 1 + v/mu^2`, then fit
   `g_detected = g_light + k/mu` (S8). `g_light` is the intercept at inverse
   intensity zero; `k` absorbs the intensity-dependent detector response.
   The implementation uses pixels within each harmonic's x window and all y rows.
5. Require positive net means, mean SNR >= 3, nonnegative net variances, no
   dtype-ceiling samples, at least eight usable pixels, and a twofold 90th/10th
   percentile mean-intensity range. Negative moments and excluded-pixel counts
   remain reported. Fits use all usable pixels; at most 256 points are retained
   for diagnostic plots/report size. Failed or unphysical fits give no light-g2
   value, rather than substituting an uncorrected statistic.

The CSV summarizes per-harmonic inferred g2, fit status and reference family.
The JSON contains provenance, assumptions, exclusions, fit residuals, R-squared
and sampled fit points. The PNG shows inverse-intensity calibration curves and
intercepts. All outputs go into `analysis` beside ON; source files are untouched.
The integrated background-corrected ADU g2 is retained as a separate diagnostic;
it includes cross-pixel covariance and is not the MCP-calibrated light estimate.

### Scientific limits that must be checked

This is the SI's model-based estimate, not an unconditional measurement of the
light's exact photon statistics. It assumes linear unsaturated detection, a
complete half-omega peak sequence, a nearly constant detector-response coefficient and common light g2 across the
fitted pixels, and uncorrelated additive signal/background. Detector pixels
share shot fluctuations; ordinary regression error bars would overstate
precision. No confidence interval is assigned until an appropriate shot/block
resampling or independent-repeat uncertainty analysis is available. Inspect the
fit curves and excluded-pixel counts, especially for weak harmonics. Excluding
negative net variance and low-SNR samples can itself bias a weak-signal fit.

Compare matched pressure/flux levels with invariant normalized spectra to
validate the pixel-intensity extrapolation, as the SI does. A poor/curved fit,
strong sensitivity to selected pixels, MCP gain changes, saturation or optical
drift invalidate its interpretation. Dtype-ceiling exclusion only detects the
stored integer ceiling; it does not detect MCP saturation, nonlinear response,
or a camera ADC ceiling below the dtype maximum. Set the acquisition safely
within its separately measured linear range.

The SI uses a high-photon-number approximation. No absolute photon gain,
quantum efficiency, exact factorial-moment conversion or automatic nonclassicality
claim is supplied. The paper's P43 persistence produced about 1.05 pulses per
measurement at its timing. That number is NOT copied to this setup; temporal
mixing depends on the actual phosphor and trigger/exposure timings, and must be
measured separately. Spatial detector response and optical modes can also vary.
Separate reference runs cannot reveal the ON signal/background covariance.

The previous raw-ADU estimator is still available only through an explicit
`--raw-intensity` CLI flag, and retains its original meaning
`mean(S^2)/mean(S)^2`. It is not used by the GUI's corrected analysis.

## Plot windows and simulated spectrum

The GUI analysis buttons open ordinary Python Matplotlib/Qt result windows
automatically when calculation finishes. X Pearson opens its heatmap; harmonic
g2 opens a compact summary and the calibration plots. Results are also saved.
Close plot windows to finish the analysis child process; the camera GUI stays
responsive. CLI automation can pass `--no-show` to harmonic g2, or `--no-plot`
to Pearson. GUI buttons show plots by default.

The harmonic marker has a native Save button; significance is hidden and one odd-reference calibration is requested on save. Dark and pale line segments mark the proposed windows; drag to edit and
right-click to remove a window. The single odd reference fixes the remaining half-omega orders for SI reference selection.

Simulated acquisition now produces eleven separated spectral lines with unequal
widths and spacings, a strength ratio exceeding 160:1, shot/read noise and a
structured sloping background. This stresses weak-peak detection and covariance
viewing. It is synthetic test data, not a calibrated BSV/MCP statistics model.
The bounded simulation buffer and all physical-camera settings are unchanged.

The former full-height detector could mistake coherent background ripples for
additional peaks: they grow with the number of summed rows while independent
noise grows more slowly. Spatial background rejection addresses that source,
without imposing a peak count of eleven. Test fixtures recover all eleven lines
for full-height simulated references and averaged narrow ROIs. Very weak single
shots can still miss a line. Spatial projection assumes a shared nonuniform
vertical trace and uniform-in-y additive background; review tilted or changing
trace shapes. The hidden sigma threshold is a robust detection heuristic, not
an exact false-discovery probability.

## Physical-data isolation

Physical and unknown/legacy data always use the original y-summed detection
path, with no spatial background rejection. The newer background projection is
restricted to files recording both the known mock serial `MOCK-EDGE-5501` and an
`EMULATOR` model. The current GUI source selection and filename are never used
to decide this: a real saved file analyzed while the GUI is in simulation still
uses physical-data processing. Display, hidden significance and single-odd-line
half-omega calibration apply to both sources. Acquisition and g2 pixel statistics
are unchanged.
