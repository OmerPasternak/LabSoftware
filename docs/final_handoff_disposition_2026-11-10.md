# Final handoff implementation disposition — 2026-11-10

All 43 confirmed findings are implemented and validated offline. F02 recovery and the exact default clean-export validation are complete. Physical/scientific acceptance remains separate.

## Scope and preservation

- Reviewed starting HEAD: `f4425b4b1faeb131b2a9fdb002cf64209556119c`, branch `codex/smc100-motor-control`.
- The initial 242-file working-tree snapshot, staged/unstaged patches and byte-for-byte index backup are retained under `data/test_artifacts/final_handoff_baseline/`. Initial index SHA256: `bfe49a375bf13ca1cb2f4badac3bbb2a50bfbc6bff733b808f3731a4fb45f6d0`.
- No physical hardware operated, no push, no remote CI. Existing motor edits/pruning retained. Representative no-dispatch and nonmoving reference-loss coverage restored without arbitrary test-count reduction.
- Local review commits use an alternate index, rooted in an explicitly labeled snapshot of the initial working tree. This allows intended fix commits to exclude unrelated pre-existing work, while leaving the user's original branch and index unchanged. The local review branch is `codex/final-handoff-fixes-20261110`.
- All Python locations below are under `src/hhg_control/` unless prefixed `scripts/` or `HHG analysis/`. MATLAB locations are under `src/HHG analysis/matlab/`.

## ID-by-ID disposition

| ID | Disposition | Changed owning files | Resulting behavior | Regression evidence |
|---|---|---|---|---|
| F01 | Fixed | camera/drivers/pco_edge.py | Whole-stack and FIFO share strict pixel/shape/recorder-ID validation; no uint16 cast or replacement IDs. | test_whole_stack_preserves_sdk_payload_or_rejects; existing FIFO gap/overflow/shape tests |
| F02 | Fixed | camera/payload.py; camera/drivers/base_camera.py and pco_edge.py; camera/sequencer/recovery.py, capture_pipeline.py, scan_manager.py; diagnostic wrappers | Stop/join SDK ownership, preserve every unhanded/queued/uncertain payload in incomplete lossless sidecars, retain malformed dtype/shape/metadata, preserve unassigned continuous tails, and retain failed recovery writes on the original manager for retry. | test_acquisition_recovery.py: terminal/hash/after-yield/abort/full-queue/writer-half-pair/SDK-local/malformed/tail/retry/final-flush/instrumentation cases with independent byte/ID oracles; diagnostic no-discard regression |
| F03 | Fixed | camera/drivers/pco_edge.py; gui/camera/camera_panel.py | Failed recorder stop latches unknown state, retains acquisition/cleanup detail, and blocks every acquisition entrypoint until close/reconnect; GUI reflects lockout. | test_failed_stop_locks_all_acquisition_until_reconnect; GUI safety suite |
| F04 | Fixed | gui/camera/camera_panel.py | STOP generations invalidate delayed mode/trigger resume intentions; running configurations lose their resume flags. | test_stop_invalidates_delayed_mode_resume; existing STOP handoff tests |
| F05 | Fixed | gui/camera/workers.py | Next unfinished step advances before completed-step delivery; STOP after final capture finishes rather than resuming a saved step. | test_saved_step_is_not_reported_unfinished_after_stop; existing resume/continuous tests |
| F06 | Fixed | gui/camera/camera_panel.py | Close clears pending configuration state and waits for actual workers; exposure handoff cannot leave a permanent pending flag. | test_event_gated_live_handoff_releases_ownership[exposure_close] |
| F07 | Fixed | gui/camera/camera_panel.py | Measurement queues behind the live worker throughout its stopping interval, including after STOP clears the display boolean. | test_event_gated_live_handoff_releases_ownership[stop_measurement] |
| F08 | Fixed | gui/camera/camera_panel.py; gui/camera/workers.py; camera/sequencer/scan_manager.py | Run destination is snapshotted before callbacks. Acquisition/grid/output fingerprint gates resume; changed contracts begin a new ID at step zero. Storage/header/parameter controls lock while scanning. | test_paused_run_identity_tracks_acquisition_contract (unchanged plus eight changed contracts); test_run_destination_is_frozen_before_step_callbacks |
| F09 | Fixed | gui/camera/workers.py; gui/camera/camera_panel.py | Resolve/mkdir/free-space checks run in scan worker and report through controlled error signals. Folder-opening filesystem work also has its own cooperative worker. | test_measurement_rejects_run_larger_than_free_space; event-gated GUI handoffs; full GUI suite |
| F10 | Fixed | gui/camera/camera_panel.py | Large-frame Minimum/Mean now explicitly say sampled; full-pixel Maximum/saturation and raw buffers are unchanged. | test_sampled_metrics_are_labeled_and_saturation_remains_exact |
| F11 | Fixed | camera/analysis/hdf5_io.py; camera/analysis/correlation.py; camera/sequencer/scan_manager.py; scripts/analyze_saved_scan_g2.py | Central aggregation validation rejects incomplete/count/geometry/settings/run mismatches and duplicate indices. Explicit repeats and pooling options; declared grids checked; unknown legacy fields returned as warnings. Low-level diagnostic readers remain available. | test_aggregation_rejects_unrelated_or_incomplete_inputs; test_explicit_repeats_and_pooling_are_separate_decisions; source hash assertions |
| F12 | Fixed | HHG analysis/matlab/hhg_analysis_current_action.m; hhg_analysis_reduce.m; new invalidate/processing helpers | Correction changes invalidate derived caches while retaining geometry/calibration. Temporary raw-reference reads use onCleanup. Step-mean reference provenance identifies the actual selected file/aggregation. | actual MATLAB integration: A/B reference replacement, direct arithmetic, cache/calibration preservation, corrected MAT export |
| F13 | Fixed | HHG analysis/matlab/hhg_analysis_processing.m; export_mat/current_masks/current_fourier/current_action/write_csv/write_processing | Derived MAT/workspace/Fourier/mask/CSV/image exports include correction/source/sample/binning provenance or a JSON sidecar. Native MATLAB arithmetic is explicitly described. | actual MATLAB correction-off/on MAT, mask/Fourier processing checks and CSV sidecar checks; raw input SHA256 unchanged |
| F14 | Fixed | HHG analysis/matlab/hhg_analysis_axis_g2.m | Centered parallel covariance replaces pedestal-sensitive subtraction for Pearson; raw g2 moments remain unchanged. | actual MATLAB 65000-ADU opposite one-ADU fixture returns -1 for batches 1/2/3/4; ordinary/constant parity integration |
| F15 | Fixed | camera/analysis/beam_profiling.py | One size-aware corner-median policy for centroid/width; optional measured background; undefined signal raises; outputs documented as observed-crop moments/Gaussian-equivalent FWHM. | independent discrete moments for three cropped shapes; blank/constant rejection; prior Gaussian fixture |
| F16 | Fixed | tc_motor/sequencer.py | Relative deadline starts before initial status and is reused through target computation and fresh driver motion guard; redundant sequencer read removed. | test_relative_initial_status_consumes_motion_deadline: zero dispatch, STOP latched |
| F17 | Fixed | tc_motor/drivers/serial_framing.py | 60-ms pacing precedes a separate configured receive budget. STOP writes still bypass pacing; malformed/late replies remain rejected. | minimum-timeout sequential-query tests through direct and VISA fake peers; complete framing suites |
| F18 | Fixed | camera/analysis/spectral.py | Every harmonic shares one second influence-statistics traversal; selected-slice sums accumulate float64 without full-batch float conversion. | test_harmonic_ci_shares_two_passes_and_matches_independent_oracle, batches 1/3 and two regions |
| F19 | Fixed | camera/analysis/correlation.py | One camera iterator feeds bounded accumulators; means are reused. Existing raw mean/active-mask API retained and raw/corrected means explicitly named. | test_acquisition_g2_streams_one_iterator_and_names_raw_and_corrected_means; existing mock acquisition test |
| F20 | Fixed | HHG analysis/matlab/hhg_analysis_reduce.m; hhg_analysis_current_workflow.m | Diagonal uses variance-only updates; covariance requests do not build Pearson; Pearson slices use centered row cross-covariance plus per-channel variance. | actual MATLAB diagonal/slice parity, constant-channel checks, cache assertions excluding full covariance/Pearson |
| F21 | Fixed | HHG analysis/matlab/hhg_analysis_current_workflow.m | Spatial PCA only requires spatial observations; spectral views retain their own memory guards. | actual MATLAB injected cache: empty spectra permits spatial PCA and rejects frequency PCA |
| F22 | Fixed | scripts/external_trigger_trial.py | Convert trigger/exposure edge lists once before pairing; preserve eligibility, duplicate and censoring semantics. | test_scope_pairing_searches_one_converted_array; existing scope-boundary/pairing tests |
| F23 | Fixed | scripts/capture_diagnostics.py | Queue diagnostic takes one final preview copy, matching production. | test_queue_diagnostic_copies_only_final_preview; existing reusable-buffer checks |
| F24 | Fixed | scripts/pco_filemode_trial.py | Recorder supervision uses exposure/readout-aware total bound and explicit no-progress deadline, separate from throughput acceptance. Production caller supplies configured exposure. | test_native_recorder_supervision_accounts_for_exposure: 2000x0.1-s progression succeeds below reference FPS; hung recorder stops near 10 s |
| F25 | Fixed, host I/O scope | scripts/lab_computer_acceptance.py; benchmark_capture_pipeline.py; storage_diagnostics.py; stress_camera_storage.py; pco_filemode_trial.py; rolling/complete/external trials; safe_io.py; diagnostic test helpers | Extended paths applied at host create/write/stat/sync/HDF5/rename/PNG/report boundaries; readable report paths retained. Vendor recorder path support remains separate. | short default suite plus dedicated long-path production/diagnostic suite; see validation results below |
| F26 | Fixed | pyproject.toml | Explicit offline roots and generated-directory exclusions prevent old exported checkouts under data from being discovered. No unknown data removed. | default pytest in existing workspace containing generated exports; clean-export default validation |
| F27 | Fixed | tc_motor/tests/test_motor_launcher.py | Detached result wait requires valid complete JSON or nonempty text and fails boundedly with last read/parse error. | test_detached_result_waits_for_complete_json (event-gated publication); real harmless BAT/VBS stub tests |
| F28 | Fixed validation gap | scripts/validate_installed_package.py; pyproject.toml | Separate clean-export wheel build/install into isolated test-dependency venv; empty cwd, no PYTHONPATH, no SDK; aliases, resources and three entrypoints checked. Motor test packages excluded from distribution. | installed-wheel smoke: 77 resource hashes, three disconnected offscreen entrypoints, compatibility identities, no pco installed |
| F29 | Fixed | gui/camera/camera_panel.py | Visible camera activity history capped at 2000 Qt blocks; no persistent full-history requirement was established. | test_activity_log_is_bounded: 2200 synthetic messages |
| F30 | Fixed | scripts/analyze_camera_axis_g2.m; application tests/analyze_camera_axis_g2.m; HHG analysis/matlab/hhg_analysis_axis_g2.m | Both launch surfaces keep their settings/default behavior and invoke one canonical calculation function. | actual MATLAB wrapper parity/default-folder test plus direct canonical bright-fluctuation regression |
| F31 | Fixed | camera/analysis/spectral.py; HHG analysis/mark_harmonic_limits.py | Validate normalized source/output identity and JSON destination before preview/map writes; protect other existing HDF5 targets and hardlink aliases; preserve atomic map replacement. | source-hash tests for same/relative/HDF5-as-JSON/other-HDF5/hardlink targets; existing atomic-map tests |
| F32 | Fixed | camera/analysis/hdf5_io.py; camera/analysis/harmonic_statistics.py | Normalize validated integer xyxy sequences before comparing actual acquisition ROI attributes with JSON maps. | SI integration fixture uses real map saver and normal acquisition-format ROI arrays |
| F33 | Fixed | camera/analysis/harmonic_statistics.py | ON/OFF parameter name/units/value and trigger checked before moments. No-XUV background is explicitly delay-independent but must match detector/exposure settings. Observed settings/policy exported; missing fields warn. | test_si_pairs_normal_acquisition_roi_and_rejects_coordinate_mismatch_before_moments: delay/trigger/units table and matched arithmetic |
| F34 | Fixed | HHG analysis/matlab/hhg_analysis_load_hdf5.m | Candidate first read/correction validation occurs on isolated temporary context before publishing source or changing controls/caches. | actual MATLAB failed exposure-mismatch load preserves source, pixels/context, ROI/navigation, labels, calibration and cached reduction |
| F35 | Fixed | HHG analysis/matlab/hhg_analysis_current_masks.m; current_action.m; validate_fft_axis.m | Validate FFT axis before reduction; compute locally; publish complete workspace bundle after success. Selected-frame export reads before publishing identity. | actual MATLAB nonuniform-axis failure preserves seeded pixels/axis/filename/provenance; successful exports retain matching bundles |
| F36 | Fixed | camera/sequencer/scan_manager.py; capture_pipeline.py | Deep-snapshot complete per-frame metadata at queue ownership transfer, independent of pixel buffer ownership. | event-gated reused dictionary/list/nested-value regression in direct public writer path |
| F37 | Fixed | camera/drivers/pco_edge.py | Retained partially connected handle prevents another SDK constructor until successful cleanup; original handle/errors retained. | test_failed_setup_and_close_retains_handle_before_retry |
| F38 | Fixed | tc_motor/sequencer.py | Recheck cancellation/deadline after status callback and just before measurement; retain partial results and STOP cleanup. | test_slow_status_callback_prevents_measurement_and_retains_results: one prior result, no pending measurement |
| F39 | Fixed | camera/sequencer/scan_manager.py | Integer index/count and finite start/step/final-coordinate validation occurs before workers/callbacks. NumPy integer indices remain supported. | test_scan_plan_rejects_before_any_side_effect: float counts/indices, NaN/Inf/overflow, zero callbacks/workers/files |
| F40 | Fixed | camera/sequencer/scan_manager.py | While partial HDF5 remains open, handled batch-write errors best-effort persist exact acknowledged complete image/metadata-pair progress and complete=false; original/progress errors retained. | event-gated later failure plus parameterized after-1/after-5/half-pair failures; byte/ID/progress checks |
| F41 | Fixed | camera/sequencer/capture_pipeline.py | Version-2 source manifests add canonical order-sensitive per-frame JSON hashes; verification reports metadata coverage. Legacy version-1 references explicitly report missing metadata coverage. | timestamp/exposure/nested raw-metadata corruption is detected; equivalent JSON formatting passes; pixel/ID/root-setting tests retained |
| F42 | Fixed | gui/camera/camera_panel.py | Normalize one ordered uint16-domain limit pair and apply it consistently to state, controls and artist without intermediate colorbar expansion. | test_constant_display_limits_share_one_ordered_interval: zero/saturated/intermediate, source unchanged; existing editable-limit tests |
| F43 | Fixed | HHG analysis/matlab/active/ShowScanGUI.m; hhg_analysis_read_legacy_bin.m | Exact sample/dimension/trailing-byte validation and onCleanup before legacy arithmetic; active loader only. Archived originals untouched. | actual MATLAB 0/1/partial-row/15-sample truncation failures, file-handle closure and complete 4x4 orientation/conversion parity |

## Intentional behavior changes

- Invalid SDK payloads fail without conversion or fabricated recorder identities; failed stop/cleanup requires verified recovery.
- Paused acquisition/grid/destination changes start a new run. Completed steps are not resumed. Storage preflight is asynchronous.
- Aggregation rejects incompatible/incomplete runs and duplicate steps by default. `run_id` selects a run; `repeat_policy="allow"` explicitly permits repetitions; g2 pooling different setpoints requires `pool_steps=True`. Low-level file/frame readers remain the separate partial-file diagnostic path. Legacy unknowns are returned in `validation_warnings` and printed by the saved-g2 CLI; its `--run-id`, `--allow-repeats`, and `--pool-steps` options expose the same policy.
- Beam auto-background is a size-aware corner-median estimate, requiring beam-free corners for a physical background interpretation. Supply a measured scalar for broad/cropped beams. Widths describe observed-image moments; no full-beam Gaussian extrapolation is made. Blank/constant auto-background cases raise instead of fabricating a center.
- MATLAB's established native-class correction and legacy arithmetic/orientation are retained, including uint16 saturation where historically applicable. Exported provenance describes that arithmetic. Raw HDF5 is read-only.
- Serial `timeout_ms` now means receive budget after at most 60 ms of compatibility pacing. Motion deadlines still encompass status/preflight/pacing and reject late dispatch.
- Native-file recorder supervision allows three times the exposure/readout period plus five seconds (minimum ten-second total/stall bounds). Throughput acceptance remains separate.

## F02 completion

The former two-frame reproduction is now covered by a deterministic regression:
reader-queued/dequeued pixels survive a terminal check or source-hashing failure.
Driver-local valid prefixes and malformed returns, writer queues and partial
pairs, and diagnostic queues are also preserved. Normal acquisition files retain
the existing schema; separate recovery files are permanently incomplete and do
not attribute read-ahead to another setpoint. Unavailable recovery storage keeps
payloads on the original manager and blocks new acquisition until explicit retry.
See `docs/acquisition_recovery.md` for the file format and retry contract.

## Final validation

| Check | Result | Evidence under `data/test_artifacts/final_handoff_baseline/` |
|---|---|---|
| Working-tree default offline suite | **534 passed**, 236.08 s, Python 3.12 / MATLAB R2026b; before the last three long-path helper/boundary files, which are included in the final clean run below | `f02_full_default.log` |
| Final clean-export default command | **534 passed**, 189.37 s; exact `python -m pytest -q` from the clean source export, default roots/cache/basetemp; isolated declared runtime/test dependencies; PYTHONPATH removed, optional SDK absent | `f02_clean_default_final.log` |
| Core recovery/integrity regressions | **140 passed**; final instrumentation/context refinements included in both complete suites | `f02_final_focused.log` |
| Long Windows paths | **167 passed**, 68.33 s; real filenames up to **444 characters**, plus **12 passed**, 2.12 s, for the final external-trigger/stress boundaries and helpers | `f02_long.log`, `f02_long.json`, `f02_external_long.log` |
| Installed wheel | PASS: **77 resource hashes**, compatibility aliases and **three disconnected offscreen entrypoint launches**, empty cwd, no source PYTHONPATH or pco SDK | `f02_installed.log`; `data/test_artifacts/package_complete/result.json` and `commands.log` |
| Final code/export identity | Every proposed Python/MATLAB/configuration file matched the clean export used for the final suite | final export/checkpoint verification |
| Original work/index | Original branch and HEAD remain `codex/smc100-motor-control` / `f4425b4b1faeb131b2a9fdb002cf64209556119c`; original index and staged patch remain byte-for-byte unchanged | `initial.index`, `staged.patch`, `completion_verified.json` |

The final clean run exposed two remaining long-path test-helper failures. Their
integrity assertions were retained and routed through `filesystem_path`; the
external-trigger diagnostic's remaining host boundaries were also covered.
Nothing was skipped or deleted to obtain a passing default suite.

All final fixes are stored in local review history on
`codex/final-handoff-fixes-20261110`, above the preservation snapshot and six
original fix batches. Exact commit IDs are in `commits.json`. The final runtime
code matches the isolated installed wheel. The final source export additionally
contains the last diagnostic/test-helper boundary corrections; only completion
documentation changed afterwards.

## Remaining validation limits

- Python 3.10/3.11 were not installed; those workflow versions remain unverified.
- No physical hardware was operated, no push was performed and no remote CI result is claimed.
- Sensor cadence, external-trigger acceptance, vendor recorder path support and representative lab-data scientific acceptance still need physical/scientific review.
- If storage cannot accept recovery writes, buffers remain in RAM with an explicit error and acquisition lockout; they cannot be made durable until storage is available. A stuck OS/HDF5 call is not forcibly terminated.

These are explicit validation/operating limits. **No F01–F43 implementation remains deferred.**
