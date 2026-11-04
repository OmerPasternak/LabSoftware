# Offline validation — 2026-11-04

The current acquisition schema 2.0 is the primary input. The supplied MATLAB
equations and display conventions are authoritative for overlapping analysis.
No physical camera or motor was contacted, and no representative lab data were
available. Real-data scientific acceptance remains open.

| Check | Result |
| --- | --- |
| Existing working checkout, Python 3.12, full offline suite | **431 passed** in 171.13 s |
| Clean HEAD + analysis changes, fresh environment with only `.[test]`, no PCO SDK | **479 passed** in 167.80 s |
| Installed wheel started outside the checkout | Passed; MATLAB assets, harmonic scripts and inactive checklist present |
| MATLAB R2026b synthetic integration | Passed, including 263-frame current-schema fixtures |
| Camera GUI layout and analysis-button regression | Passed |
| Final display-only screen fit and shorter labels, affected MATLAB/Qt checks | **19 passed** in 33.49 s |
| Preserved supplied package | SHA-256 manifest test passed for every original file |
| Own tracked diff whitespace check | Passed |

The suite counts differ because the working checkout contains pre-existing
motor source/test edits. The clean proposal excludes that unrelated work.
Python 3.10 and 3.11 were not installed on this computer and were not tested.
Remote CI was not run; no push was authorized or performed.

The final visual check found the legacy GUIDE design extending beyond a
smaller display. Its complete canvas now scales to the available screen with
normalized control/axes positions; the affected checks were rerun after this
display-only refinement. Original archive line endings are protected by a
scoped `.gitattributes` file so recorded SHA-256 values survive a fresh checkout.

The MATLAB checks cover raw hyperslab reads and MATLAB dimension order,
completion/count/metadata checks, one-frame loading, batch/remainder boundaries,
two 256-spectrum covariance updates, constant-channel Pearson NaNs,
source-equation agreement, source plot orientation and palette, bounded ROI
geometry and traces, PCA reconstruction/eigenvalues, explicit TC positions,
coherent-only subtraction semantics, masks with fractional labels, per-pixel
Fourier magnitudes, metadata-sorted step means, camera-mismatch rejection,
batched MAT export and unchanged hashes of all input HDF5 files.

The first clean export was nested too deeply under `data/test_artifacts`,
causing 24 Windows filename/path-length failures in storage tests. The same
proposal passed all 479 tests from a short generated export, with default
pytest temporary-path settings. No acquisition code or test assertions were
changed to work around the environment.

The direct grating calibration implementations require unavailable Signal
Processing and Curve Fitting Toolbox functions. Their full-data behavior was
not executed here. Unknown historical Fourier aggregation, empty controls and
the missing-helper calibration route are documented in
[`INACTIVE_FEATURES.txt`](../INACTIVE_FEATURES.txt).

Suggestions for further work are in [the feature guide](feature_guide.md).
