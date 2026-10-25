# Test selection and evidence guide

Reviewed against the checked-in implementations on 2026-10-25. This is an
inventory and routing review, not a new hardware acceptance result. Recheck the
implementation when it changes. `AGENTS.md` and `GEMINI.md` share this guide.

## Choose one question first

1. For a code change, select the matching offline group below. Include the
   nearest caller/consumer only when the interface or behavior it relies on
   changed. Do not start camera benchmarks to validate a software-only edit.
2. For an unexplained lab result, inspect the existing JSON/log and deployment
   revision first. Choose the diagnostic that separates the remaining causes.
   Do not rerun every older diagnostic to reconstruct evidence already present.
3. Stop after the selected checks answer the question. Broaden for a concrete
   failure, missing coverage or required gate. Before a code/test push, run the
   complete offline suite on the final state per `AGENTS.md`. A passing full run
   already includes its targeted tests: do not rerun those separately afterward.
4. Reuse results only when code, dependencies and relevant settings still match.
   For physical/storage evidence also match PC, drive, free-space range, USB
   topology, camera/SDK, ROI, exposure, shutter, trigger, batch/buffer/verification
   policy and background load. Record differences rather than silently combining
   incompatible rates. Historical report validity is separate from current CI.

Documentation-only edits need checked links, paths and `git diff --check`.
No full suite or physical run is needed solely to commit documentation. New
numerical behavior, concurrency and safety boundaries need behavioral checks;
do not add tests that merely repeat a trivial implementation.

## Offline software regression map

All paths in the following table are under `tests/` unless explicitly stated.
These tests use mocks, fake SDKs, small synthetic files or saved-data fixtures;
they establish software behavior, not physical instrument performance. Start
with the group matching the change, using explicit file paths in pytest.

| Change or failure | Primary test files | Add only when relevant |
| --- | --- | --- |
| Camera contract, SDK settings/readback, FIFO, shutter, trigger, timestamps or delay adapter | `test_pco_driver_contract.py`, `test_safety_corrections.py` | `test_roi_and_live_controls.py` for ROI rules; `test_mock_camera.py` for shared/mock behavior; `test_delay_characterization.py` for delay settings; production tests if the reader contract changed. Fake-SDK success does not prove device acceptance. |
| GUI measurement acquisition, bounded reader, source hashes, independent sync/verifier, continuous file boundaries, completion or STOP | `test_production_capture_pipeline.py` | `test_streaming_and_integrity.py` for the underlying step writer/queue; driver contract tests for SDK changes; GUI workers/safety for lifecycle changes. This is the primary regression set for the measurement button. |
| Per-step HDF5 writer, chunking, metadata, queue ownership, disk limits or partial-file publication | `test_streaming_and_integrity.py`, `test_scan_manager.py` | `test_consecutive_acquisitions.py` for repeated step lifecycle; production tests for public scan behavior; saved-data readers for schema changes. A direct step call is not the complete `execute_scan` pipeline. |
| Preview, typed settings, ROI editing, source switch, live/scan handoff or GUI responsiveness | `test_gui_workers.py`, `test_safety_corrections.py` | `test_gui_layout.py` for render/layout changes; `test_roi_and_live_controls.py` for bounds/cropping; production measurement-button case when acquisition completion changed. Do not benchmark disks to diagnose a text-input repaint issue. |
| Mock images, exposure waits, simulated spectrum or simulated RAM behavior | `test_mock_camera.py` | `test_gui_layout.py` for GUI mock RAM budget; matching analysis tests for changed mock signal assumptions. Mock trigger selection does not model electrical pulse acceptance. |
| HDF5 readers, export or archived-data inspection | `test_analysis.py`, `test_inspect_camera_scan.py` | `test_inspect_scan_timing.py` for frame-ID and host-timing reports; streaming/production tests if a shared schema changes. |
| Spectral Pearson/covariance, saved ranges or legacy per-harmonic g2 | `test_spectral_analysis.py` | `test_correlation.py` for array/streaming g2; harmonic-statistics tests if shared range or calibration behavior changes. Use expected numerical results, not just successful plotting. |
| Automatic harmonic detection, persistent maps, half-omega reference or SI calibration | `test_harmonic_statistics.py` | `test_spectral_analysis.py` for shared range consumers; GUI layout/workers only for changed GUI interactions. Offline SI fixtures do not validate real detector calibration. |
| Rectangle intensity mean/sample STDEV or persistent intensity window | `test_harmonic_intensity.py` | Delay-characterization tests if its shared intensity calculation/selection contract changed. |
| Camera delay scan, restore-on-failure, diagnostics window or delay plot/statistics | `test_delay_characterization.py` | `test_harmonic_intensity.py` for shared rectangle statistics; driver/safety tests if timing setters changed; GUI layout/workers if main-window behavior changed. |
| Notebook runner, summaries, budgets, settings or recovery | `test_lab_computer_acceptance.py` | The corresponding diagnostic module below when its execution behavior changes. This includes notebook syntax/status checks without physical acquisition. |
| 4C/4D/4E batch/format/sync controls, windows, budgets or pixel manifests | `test_storage_diagnostics.py` | Underlying writer/production tests for shared code; `test_benchmark_capture_pipeline.py` for paced replay; path regression cases for shared filesystem changes. |
| Official PCO native-file comparison (4B) | `test_pco_filemode_trial.py` | Acceptance/storage tests for changed shared result/settings contracts. Stub SDK discovery/version calls as well as camera objects. |
| Earlier finite benchmark implementation (4F) | `test_complete_pipeline_trial.py` | `test_rolling_pipeline_trial.py` for changes shared with 4G; production tests only if production code changes. Passing 4F mocks does not establish parity with the GUI. |
| Rolling benchmark/recycling (4G) | `test_rolling_pipeline_trial.py` | Complete-pipeline tests for shared reader/workers; verify strict deletion boundaries and failure retention. |
| Current production external-trigger diagnostic (4H), timing summaries or scope CSV pairing | `test_external_trigger_trial.py` | Production tests for shared sequencer changes; driver tests for trigger/timestamp behavior. CSV pairing uses one scope clock. |
| Older external-capture CLI | `test_external_capture.py` | Current 4H/production tests only when shared code changes; do not run both physical tools by default. |
| Paced synthetic replay, lateness or stage/fsync diagnosis | `test_benchmark_capture_pipeline.py` | Writer tests for shared writing changes. Synthetic sources can wait and have no physical FIFO. |
| Older synthetic storage cases | `test_stress_camera_storage.py` | Writer tests only for changed shared writer behavior; do not rerun format benchmarks for routine acquisition edits. |
| MATLAB folder analysis or MATLAB/Python HDF5 compatibility | `test_matlab_axis_g2.py` | Schema/reader tests if file layout changed. Verify the selected MATLAB executable/license first. The test skips when MATLAB is absent; a skip is not MATLAB validation. |
| Package moves, legacy aliases, entry points or checkout resource routing | `test_package_layout.py` | Affected consumer tests, motor launcher tests for its launcher, and clean-export validation for packaging/pytest-path changes. |
| TC motor simulator, driver/protocol, safety, motion/cancel sequencing, GUI or launcher | `src/hhg_control/tc_motor/tests/test_motor_mock.py`, `test_motor_smc100.py`, `test_motor_protocol_review.py`, `test_motor_sequencer.py`, `test_motor_gui.py`, `test_motor_launcher.py` (all six in that directory) | Select the changed layer plus callers for a narrow change. Use the whole motor directory for safety/lifecycle changes. Camera suites are unnecessary for an isolated motor edit; the full-project pre-push gate still applies. No real motion occurs in these tests. |

Shared `safe_io.py`, raw-data schema, process/thread lifecycle, or compatibility
changes can span groups. Choose consumers by the affected API and failure modes;
do not presume a fast one-file test covers them. New instrument tests should
remain in that instrument's package and be included in the full suite.

## Commands and full-suite boundaries

Use the checkout `.venv`; refresh the test process PATH as described in
`AGENTS.md` (especially MATLAB R2026b versus stale R2025a). Examples from root:

```powershell
# Routine targeted change; replace the paths with the matching group above.
.\.venv\Scripts\python.exe -m pytest -q tests/test_harmonic_intensity.py

# All motor checks, offline.
.\.venv\Scripts\python.exe -m pytest -q src/hhg_control/tc_motor/tests

# Full local suite, including nested motor tests, excluding copied data trees.
.\.venv\Scripts\python.exe -m pytest -q --ignore=data
```

`pytest tests` alone omits the motor package. Root discovery can collect stale
checkout copies under ignored `data/`, yielding duplicate imports and obsolete
tests. `--ignore=data` excludes those artifacts, not the real nested motor tests.
Do not delete user data or change test assertions to repair collection pollution.
Avoid storing exported checkouts inside the working checkout. Do not run pytest
processes concurrently against the same configured `--basetemp` directory.

CI currently runs Windows Python 3.10/3.11/3.12, installs only `.[test]` and uses
`python -m pytest -q`. Read the current workflow/config when those files change.
For packaging, optional dependencies or pytest/discovery changes, use a clean
tracked checkout/export and the exact CI command with only `.[test]` installed.
An alternative basetemp diagnoses a local cleanup/access problem; it does not
validate default CI artifact setup. Report filesystem/license setup failures
separately from assertion failures; follow `AGENTS.md` before changing permissions.

After successful targeted validation, a required full gate is one full run on the
final state, not repeated full runs after each small edit or documentation change.
Include a required fix in the tested commit. After an authorized push, inspect
the exact commit's matrix results; a fresh passing CI result covers the pushed
state only, not later local edits. Do not poll unchanged CI state rapidly.

## Lab diagnostic routing

The notebook is `application tests/lab_computer_acceptance.ipynb`. Run setup and
the selected cell explicitly; **never Run All**. Import/setup alone does not
authorize a physical run. Review hardware settings/ownership and output budgets
before an authorized run, including optical/electrical requirements when relevant.
Physical connects can reset SDK defaults; shutter changes can reboot the camera.
Use serial 61008541 with strict identity readback and existing safe cleanup.

| Question | Select | Reuse / avoid redundant work |
| --- | --- | --- |
| Is this PC/interpreter/drive basically ready? | Section 1 (synthetic paced/unpaced writer); enable its offline subprocess only when needed | Keep `CONFIG.run_offline_tests=False` when the same software suite already passed. No camera/USB conclusion. Use 4D for a specific sustained format/sync question instead of repeating both storage screens. |
| Did identity/settings/reconnect change? | Section 2 | Two connects, no saved frames. Do not require it before every later cell when identity/readback is already verified there; repeat after an unexplained settings/reconnect failure. |
| Are short saved images and settings sensible? | Section 3 plus its first/middle/last review PNG | First-use/changed imaging configuration screen, not a throughput benchmark. Section 7 is optional when structural checks already passed, unless copy/readback is the question. |
| Can an existing file or its archived copy be read unchanged? | Section 7, `inspect_camera_scan`, or `inspect_scan_timing` for timing/ID questions | Reuse existing files; do not reacquire just to inspect storage or compare copies. File fingerprints alone lack an original pre-write pixel reference. |
| Quick matched CamWare capture-only versus saved run? | Section 4 | Older coarse baseline. Prefer 4C when component profiling is needed; do not require both. Match measured Record Sequence counts/time, not display/playback FPS. |
| Does official native `.pcoraw` beat HDF5 on C/D? | 4B, `pco_filemode_trial.run_hard_drive_decision_test` | Historical format alternative, not the default drive verdict now. Repeat only for a specific native-format/SDK hypothesis. Combined output cap is 30 GB per drive. Missing native pixel/ID readback remains unresolved, even if counts pass. |
| Is the slow stage camera/SDK, Python/batching, queue, or step writer? | 4C, `storage_diagnostics.run_batch_size_trial` | Matched capture-only, queue-to-discard and step-HDF5 controls plus instrumentation overhead. Uses `acquire_and_save_step`, not the full GUI continuous pipeline. Start with one ROI/stage; larger stages only address a remaining cache/backlog question. |
| Is sustained writing or sync responsible, independent of camera? | 4D, `storage_diagnostics.run_bin_hdf5_trial` | Synthetic prepared pixels; no camera opens. Compare durable MB/s and whole sync cost. Keep BIN only while a matched format question is unresolved; a `.pcoraw` result does not settle BIN versus HDF5. Use HDF5-only controls when format is already settled for this configuration. |
| What overhead comes from source hashing and post-capture readback? | 4E, `run_hdf5_pixel_verification_trial` | Explicit baseline/hashed step pair. Current production already hashes/verifies: do not run 4E as a routine prerequisite for 4H or reverify unchanged files repeatedly. |
| Compare concurrent versus deferred verification, file sizing, or earlier buffered pipeline candidate? | 4F, `complete_pipeline_trial.run_complete_pipeline_trial` | Separate benchmark reader/orchestration. Reuses the production step writer but is not `CameraScanManager.execute_scan`. Use for its controlled policy experiment, not proof the GUI is identical. Default retains 30 GB on one selected drive. |
| Will RAM/backlog grow during a long run with limited free disk? | 4G, `rolling_pipeline_trial.run_rolling_pipeline_trial` | Same benchmark path as 4F; default 600 s with a 12 GB live budget and verified-file recycling. Can run without 4F first. Never use for retained experimental data. A pass does not prove production parity or a TB retention run: recycling/readback/TRIM and free space differ. |
| How does the current GUI pipeline respond to external triggers? | 4H, `external_trigger_trial.run_external_trigger_trial` | **Primary current production diagnostic:** `CameraScanManager.execute_scan(continuous=True)`, batch 4, one SDK recording across files, independent sync and full integrity checks. Match GUI RAM/configuration; no automatic trigger pacing. Start small after setup/wiring changes, then increase only for a remaining question. |
| What is actual trigger-to-exposure latency/jitter or response fraction? | 4H optional scope-edge analysis and `external_trigger_validation.md` | Reuse a valid scope CSV; no camera rerun needed for analysis. Requires trigger input plus exposure-status output on one timebase. A trigger-only trace, host timer, nominal Hz, or consecutive recorder IDs cannot establish input pulse accounting. |
| Does integrated harmonic intensity change with camera exposure-start delay? | GUI Diagnostics → Camera delay characterization; `camera_delay_characterization.md` | Separate physical signal test: reference/rectangle then 15 delay points (0–100 ns and 1900–2100 ns), separate recorder per point, actual delay readback and restoration. Intensity mean/sample STDEV is not electrical latency or jitter; do not use this as an FPS benchmark. Mock has no physical delay response. |
| Are repeated steps/long retained files reliable? | Section 5 | Older per-step writer path, estimated duration from target FPS; use only for that lifecycle or retained-output requirement. Not automatically required after 4F/4G/4H. For current GUI behavior, favor its production regression tests and matched authorized GUI/4H evidence. |
| Do abort/write failure recovery and controls behave correctly? | Section 6 (mock faults) and relevant GUI tests/manual checks | Section 6 remains mock even with physical CONFIG. Reuse its automated regression coverage for routine code edits; manual physical STOP/disconnect needs separate authorization. |
| Legacy pulse-count bench or minimal external CLI compatibility? | Section 8 or `scripts.test_external_capture` | Prefer 4H for new diagnoses. Section 8 stays deferred without independent count evidence; neither legacy tool replaces the scope or establishes one frame per laser pulse. |
| Mock synthesis versus prepared replay cost? | `scripts.benchmark_simulation_overhead` | Offline only. Use when simulation itself is slow; never infer physical camera or USB throughput. |
| Raw/file-creation/older format microbenchmark or paced writer behavior? | `scripts.stress_camera_storage` or `scripts.benchmark_capture_pipeline` | Narrow storage questions only. Reuse section 1/4D results if equivalent; replay can wait through stalls and cannot prove FIFO tolerance. |

4C and 4D currently default to a **20 GB C stage** and separately selected D
stages of **20, 100 or 200 GB**, including metadata reserves. Do not reinterpret a
stage budget as a per-variant allowance. 4C selects small/medium/large manually;
4D shares its stage budget across the selected automatic windows/formats/sync
policies. Retained outputs accumulate across runs. Use actual cell settings and
fresh free-space preflight, not an older chat's budget estimate.

For GUI Auto Sequence performance, there is currently no dedicated 4H-style
Auto Sequence wrapper using the exact current public production pipeline.
Use an explicitly reviewed matched GUI recording and its production timing,
or propose that missing narrow wrapper if needed. Do not silently call 4F/4G
the current GUI implementation or change 4H's trigger semantics to fill the gap.
The same limitation applies to claims about the GUI's long-run RAM stability:
4G measures its own benchmark path. If production-specific evidence is required,
reuse the current public sequencer in a reviewed narrow diagnostic instead of
building another reader/writer implementation. First check whether existing
matched GUI/4H reports already resolve the question.

## Interpret once, then choose the next measurement

- Native camera cadence low and also low in matched CamWare: investigate camera
  timing/trigger acceptance/configuration first. A faster disk does not explain
  that result by itself. Integer trigger-period multiples are a pattern, not a
  measured rejected-pulse count.
- Camera cadence healthy but host retrieval/queue-to-discard slower: inspect
  SDK returns, host scheduling, copies, batching/hash/observer cost and queue
  feedback. Host receipt times include buffering and do not measure exposure.
- Only HDF5 slows or queue backlog grows: use 4D to separate storage/sync from
  producer work. Stage durations overlap; do not add them as sequential costs.
- Capture healthy but durable/verified FPS lower: compare sync completion,
  write/drain and verification timelines. A post-capture delay alone is not lost
  frames. Do not remove integrity checks to make reported FPS look better.
- Read/hash verification checks saved pixels against SDK-returned source bytes.
  It does not prove sensor accuracy, optical shot isolation, trigger accounting
  or survival through power loss. Reuse the manifest for a specific later audit.

Keep experimental raw files immutable. Keep compact JSON/log paths and the
settings/revision needed for comparison; avoid embedding reports in the notebook.
Record the question answered, outcome, uncertainty and one justified next step.
Do not promote `EXECUTED`, an offline pass, a finite RAM extrapolation or an old
benchmark into physical/scientific acceptance.
