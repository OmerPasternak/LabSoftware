Current interaction (2026-11-10): choose CovarianceFromGUIdata or
TConlyAnalysisFromGUIdata in the launcher. Only the analysis interface and
its plots open; MATLAB runs without its desktop behind the scenes. Original
Scan Analyzer button captions are retained, including Load Scan ..., Load
Image ..., Load Two Color Scan ..., and Export data to workspace. These
controls continue to read current HDF5 data. Source-editor actions are hidden
in the application session. The developer entrypoint hhg_analysis_start remains
available for interactive MATLAB development.

# HHG analysis

Double-click `run_hhg_analysis.vbs` in this folder or at the repository root.
The `.bat` versions retain a console for diagnostics. The camera GUI's
**Fundamental Analysis** button opens the same application. It does not
connect to a camera. Module entry point: `python -m hhg_control.hhg_analysis`;
installed command: `hhg-analysis`.

**The supplied MATLAB code is authoritative for overlapping methods and
appearance.** The GUI's X Pearson action imports the chosen camera file into
MATLAB and uses the supplied spatial-mean method. The former Python Pearson
CLI remains a compatibility API; it is not the GUI's default implementation.
Harmonic marking and corrected harmonic g² retain the existing Python tools,
because the supplied package contains no equivalent corrected g² method.

The primary input is the **current camera schema 2.0**, with raw
`/images uint16 [frame,y,x]`, root acquisition settings and one
`/frame_metadata/json` record per frame. The old lab binary format is secondary.

Choose **CovarianceFromGUIdata** or **TConlyAnalysisFromGUIdata** to open the preserved
`ShowScanGUI` and its new **HHG Analysis** menu. Each launch opens an independent
analysis process; perform related steps in one desktop so the loaded scan,
references and settings stay together. Closing the Python launcher does not
terminate that MATLAB session. The startup log location appears in the Python
activity log. A MATLAB executable picker supports another installed release.

## Active functions

| Supplied functionality | Integrated location / behavior |
| --- | --- |
| TXT + BIN scan loading, numerically sorted delays | Legacy binary inputs submenu; original big-endian uint32-to-uint16 reader |
| PIC + TXT single image | Legacy binary inputs submenu |
| TCS + BIN two-color scan | Legacy binary inputs submenu; original 14-character filename suffix convention |
| Current camera HDF5 | Primary input; schema 2.0, complete/count/axis/metadata checks; bounded hyperslab reads, no raw-stack loading |
| Current multi-step HDF5 scan | Explicit menu; sorted by metadata step index, matched run/camera/exposure/ROI; one derived mean of all repetitions per step |
| Frame navigation / current delay | Original main slider and labels; HDF5 is explicitly labeled as frame index |
| Linear / logarithmic image and spectrum | Original radio controls, color limits and `log10(value + 0.1)` image convention |
| Full image / spatially integrated spectrum / intensity vs delay | Original axes; original image orientation retained |
| ROI selection, clearing, preview, integrated trace and counts | Original controls; bounded selection and consistent `width*height` pixels |
| Zoom and data cursor | Original controls |
| Figure PNG | Original PNG button; current MATLAB `exportapp` replaces unsupported legacy `print` |
| Workspace / disk exports | HDF5 source reference and current frame in workspace; full data exported to v7.3 MAT in bounded batches; original legacy workspace exporter retained |
| Use / discard background | Original controls and legacy semantics; frozen TC reference is a separate explicitly selected reference |
| Harmonic masks, yields, delay traces | Original `.msk` controls and CSV export; mask `[H,x,y,width,height]`, bottom-origin, one-based |
| Harmonic pixel arrays / prefixes / stored masks | Original exports; fractional harmonic variable names are made valid consistently |
| Full-image, user ROI, fixed relevant ROI, harmonic-mask per-pixel Fourier | Recreated per-pixel views from original exported data; explicit method below |
| Binning before pixel Fourier | Original even-or-one bin rule, vectorized block sums, remainder cropping and uint16 saturation; not applied to raw files |
| DC mean image | Menu; source logarithmic colors, limits 90–200 |
| Spectral Pearson / covariance / calibrated correlation | Menu; selected spatial **mean**, centered sample covariance `N-1`, source display conventions |
| Repeat heatmap, flipped Pearson, spectrum overlays and harmonic guides | Menu; source limits, flips, red overlays and guide styles |
| Covariance diagonal / Pearson slice | Menu; configurable row, source default 400 where in range |
| Pixel variance / spectral variance | Menu; source single precision for pixel variance, sample normalization |
| Legacy “space cov” block | Menu; preserved actual calculation, which averages spatially and correlates a **spectral subset**, despite its heading |
| Spatial / spectral PCA | Menu; source observations/reductions, weighted components, unweighted spatial components and explained fractions |
| TC delay map and coherent-only subtraction | Menu; source empirical 1600 nm calibration, explicit step and reference choice |
| Three complete grating-fit variants | One menu with distinct source presets; original model, initial values, bounds and MATLAB fitting functions |
| Saved harmonic-axis reuse | Import a monotonic, finite `xW` vector from MAT, with source provenance |
| Normalization utilities | Active original `normalizeData` and `normalizeDataWRT`; original constant-input NaN behavior retained |
| Source scripts / original sections | Review original scripts menu; all originals archived with checksums |
| Derived results and settings | Export result + settings to a new MAT file; existing files are not overwritten |

Inactive and dependency-limited features have their own durable checklist in
[`INACTIVE_FEATURES.txt`](../INACTIVE_FEATURES.txt). Buttons with empty original
callbacks remain visible and disabled. Unknown scientific behavior is not
silently inferred.

## Methods and appearance

The source MATLAB covariance script computes statistics on the **spatial mean**
in each frame. Spatial ranges are one-based inclusive raw-array rows, as in
`dataNow(spacePix,:,:)`. They are not the bottom-origin displayed ROI coordinates.
The former Python covariance API spatially sums pixels. For `m` spatial pixels,
its covariance is `m²` times mean-based covariance; Pearson is unchanged by that
constant factor. Do not compare differently reduced covariance amplitudes.

Single-step **frame mode** treats repeats as statistical observations, never
as physical delay samples. Explicit **step-means mode** keeps scan steps
separate and computes a derived image from all repetitions in each step;
statistics then describe those step means. Source frame counts and sample mode
are saved. A physical TC axis requires explicit confirmation of mechanical mm.
No timestamps, filenames or frame indices substitute for verified setpoints.

The prominent load buttons open current HDF5 data. Legacy loaders remain in
their compatibility submenu. The original GUIDE `.fig` is used directly,
including its saved colormap and axes. Script figures retain their separate
MATLAB session-default palette. Plots retain their original `imagesc`/`pcolor`, `hold on`,
flips, linear/log settings and shading. `pcolor` flat intentionally omits its
last row/column of cells, matching the source; it does not remove stored pixels.
Default script limits remain 90–200 for DC, 90–130 for repeats, -1–1 for Pearson,
0–20 for pixel variance and 0–2500 for the TC map. The repeat zoom 1200–1600 is
used only when that many repeats exist. Harmonic display limits are configurable.
The complete GUIDE canvas scales to fit the screen and remains resizable;
short labels and filename tooltips keep controls usable on smaller displays.

The Devon RGB table is bundled with its upstream MIT license and citation in
`matlab/colourmaps/`. The supplied lab helper version was unavailable; the
bundle records its source. It is available without installing `crameri`.

PCA uses MATLAB `pca` when installed. Otherwise, base MATLAB centered SVD
produces the same sample-covariance eigensystem for finite observations, with
`latent = singular_value²/(N-1)`. Scores, coefficients and the method are saved.
Repeated or zero eigenvalues can have different equally valid basis vectors.
No frames are omitted to make PCA succeed.

The recreated per-pixel Fourier view explicitly computes
`abs(fft(double(data),[],3))` and displays bins `0:floor(N/2)`, at `k/(N*dt)`.
It applies no window, DC removal, padding, normalization, amplitude doubling,
resampling or integration. It shows a chosen pixel's trace and transform, plus
the image at a chosen Fourier bin. These are documented replacements for a
deducible purpose, not claims about the absent original helpers' conventions.
Unknown integrated Fourier aggregation remains inactive by user decision.

The TC map retains the source calibration
`delay_fs = (1:N) * step_mm/(0.001*5.8*4) * 2.67`.
Supported historical steps are 0.001, 0.002, 0.003, 0.005, 0.010 and 0.050 mm.
It requires verified uniformly spaced mechanical positions. HDF5 shot indices
never become TC positions automatically. To attach a verified axis explicitly,
call `hhg_analysis_set_tc_axis(fig, delay_mm)` in MATLAB. Coherent-only subtraction
retains source MATLAB arithmetic; uint16 inputs saturate at zero. It does not
reinterpret this as signed floating-point subtraction.

## Safety, stability and performance

Original supplied files are unchanged in `matlab/reference/`; active copies
and small adapters live in `matlab/active/` and `matlab/`. Only this active
directory enters the MATLAB path. Migration placeholders and the conflicted
copy never shadow the selected general implementation.

Legacy loaders deliberately retain the source analysis behavior: big-endian
uint32 reading into uint16, first-pixel replacement in scans, a fixed crop for
1392×1040 data, and optional `0.95*bg` subtraction with uint16 saturation. These
are **derived legacy analysis operations**, not changes to acquisition. Their
presence is printed at startup. HDF5 import performs none of them and never
reads the global workspace background.

Current HDF5 inputs remain on disk. The viewer reads one selected frame, and
trace calculation is explicit so opening a large recording does not scan its
entire stack. Reductions use up to eight raw frames / about 32 MiB per read.
Spatial means are vectorized, and covariance uses 256-spectrum matrix products
and parallel/Welford merges independently of the smaller disk-read batches.
Reusable reduced spectra, covariance and image
moments are cached rather than raw stacks. DC/variance and non-covariance views
avoid computing an unnecessary covariance matrix. Pixel variance uses stable
batched moments returned as single precision; rounding can differ slightly
from a whole-stack `var(single(...))` call.

Reduced-spectrum/PCA/Fourier workspaces have explicit limits (256 MiB for
retained spectra and up to 1 GiB for larger numerical workspaces). No frames are
silently discarded. The legacy binary loader remains an in-memory compatibility
path. MATLAB runs in its own process so computation cannot block the camera GUI.

New data invalidate cached axes, analysis ranges, TC-axis confirmation and
the last result. A deliberately captured TC reference stays frozen, and its
shape is checked before use. Pixel selections are bounded; slider limits work
for a one-frame file. The original ROI off-by-one discrepancy between preview
and export is corrected so both use the exported width/height convention.

## Duplicate selection

`ShowScanGUI.m/.fig` is the reference explicitly selected by the user. The
edited GUIDE version changes radio controls and slider steps but adds no
numerical feature. `ShowScanGUI_App_1` contains converted source but refers to
radiobutton properties absent from its component declarations; the remaining
App Designer files are migration placeholders or incomplete containers.

`wAxis_Calibration.m` is the general parameterized helper-based function;
its `.txt` is byte-identical. The conflicted copy has fewer arguments but
contains a complete FWHM/direct-fit variant, so that behavior is retained as
its own preset. The old unsmoothed and newer smoothed calibration scripts
remain distinct presets instead of being discarded as filename duplicates.

## Suggested next improvements

1. Validate one small scan/reference/mask set in the lab before using results
   for interpretation. Save the expected plots and numerical results as fixtures.
2. Decide the inactive Fourier aggregation and normalization using a real
   phase-varying ROI; this determines which physical observable is displayed.
3. Extend current named background/reference checks to every legacy format;
   current HDF5 background correction is explicit, separate from raw pixels,
   and checks geometry, exposure, camera identity and mode.
4. Add cancellable progress reporting and disk-backed PCA scores for reduced
   observations that exceed the current memory budget.
5. Migrate the remaining manual `.msk` workflow to a visual mask editor with
   explicit bottom-origin versus array-row coordinates and calibration previews.

No representative lab data were available for this integration. Offline
synthetic checks establish software behavior, not physical/scientific acceptance.
See [the validation report](validation_report.md) for suite results and limits.
