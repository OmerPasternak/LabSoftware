# Storage diagnostics and pixel verification integration plan

Section 4E now implements an explicit diagnostic for production HDF5 acquisitions:
it hashes original camera batches before writing, closes and syncs the file, then
rereads every pixel against the saved reference manifest. It works with the
existing physical or mock camera path. Routine production verification and the
PCO native-file reader described below remain planned work.
The synthetic 4D control already compares every saved pixel against its known
source and checks metadata IDs; it is not a physical camera acceptance test.

## Run the new diagnostics

### 4E: production HDF5 source pixels and readback

Run setup, close other camera applications, and run the 4E cell explicitly. Its
default destination is CONFIG.output_dir, with 2,000 frames each for a baseline
and a source-hashed recording (~2.62 GB retained at 2560x128). Configurable drive
paths allow repeating on C and D. Complete output/free-space checks precede
camera connection. Each reference/readback block is limited to 256 MiB.

Source SHA-256 runs before batches reach the production writer. References are
saved in a `.pixels.sha256.json` sidecar after the HDF5 file closes and fsync
finishes. Every pixel in the second recording is then reread in bounded blocks;
shape, dtype, completion and frame count must also match. Files and manifests
are retained on failure. The public `verify_hdf5_pixels(file, manifest)` helper
can rerun verification later without a camera. Keep both original files.

The compact `section_4e_*.json` is directly in the selected test folder. It reports
baseline and hashed acquisition/write/close FPS, durable FPS including fsync,
source hash time, verification time, added post-acquisition time, and total
duration and measured change versus baseline. The all-checks wall durations
also include the existing saved-run metadata/sample inspection. Acquisition FPS
is measured before readback; source hashing can still slow the producer. The
editable 5% tolerance reports whether measured FPS stayed within that range;
a tolerance miss is a performance result, while a checksum mismatch raises an
error. Sequential runs and cache/load changes preclude attributing every timing
difference to hashing. Reads may use OS cache, so this is not a power-loss test.

The first recording is a timing baseline; only the second has source references.
This diagnostic does not add routine GUI acquisition verification, protect the
manifest against deliberate alteration, or verify sensor/trigger correctness.

Open `application tests/lab_computer_acceptance.ipynb`, restart its kernel after
updating code, run setup, then run the desired individual cell. All diagnostic
reports and logs go to files; only brief RUNNING/DONE/FAILED messages appear in
the notebook. Failures raise an error after saving their diagnostics. No report
dumps or plots are embedded in the notebook.
Use one destination per drive. Every requested drive is preflighted for the
complete retained payload before recording or writing. Files are retained in
new directories and research data is never overwritten or deleted.

### 4C: camera cadence, host processing, queue and production HDF5

The notebook now selects one explicit drive/stage. C uses a 40 GB total retained
output budget. D selects 20, 100 or 200 GB; it never advances automatically.
C/D20 divide the budget across batches 4/16/64. D100/D200 require a selected
batch from screening and retain one continuous recording. Metadata reserves
(4 KiB/frame plus 100 MB/file) are inside the budget, reducing raw pixel payload
slightly. All retained outputs are checked together. Existing 1.5x free-space
reserve remains: C40 generally requires about 60 GB free. Runs do not delete
older files. Both sections/all stages can retain up to 80 GB on C and 640 GB on D.

Uses the existing reviewed configuration, with no new SDK configuration setter.
A one-time optional SDK configuration readback records pixel rate, binning,
exposure/delay and ROI outside rate timing; non-unit binning stops the diagnostic.
For each batch it compares matched-count capture-only, bounded queue-to-discard,
and production HDF5. Long stages require roughly three acquisition intervals
plus sync/readback; only HDF5 retains image payload. Hashing defaults on across
all three full measured modes for comparable processing work. A separate short
pair (at most 2,000 frames) measures uninstrumented versus instrumented capture.

The old capture_fps remains N / total capture-only wall duration: recorder
setup/start, SDK reads, Python batch handling/validation and stop. It is not
necessarily comparable to a displayed CamWare camera-timing number. Additional
boundaries are first-to-last host image returns, camera timestamp cadence only
when documented PCO timestamps are valid for every frame, and startup/stop
costs. Host fallback/simulated timestamps never establish physical camera cadence.
The report retains the user-entered CamWare reference without declaring its
measurement boundary verified.

Passive SDK proxies time only existing record/wait/status/image/stop calls;
profiling restores the original SDK object even after an error. Image-copy timing
includes work within that vendor API, not an independent USB-only measurement.
Driver opt-in timing separates Python frame/metadata work and np.stack. SDK wait
can be normal acquisition waiting. Source hashes and observer CPU work are
reported. The mock counterpart exercises the orchestration without an SDK.

Queue-to-discard uses a bounded queue, owned-batch semantics and the production
queue-size rule; it is a diagnostic control, not a second writer implementation.
The production writer records captured/written counts, queue capacity/max/full
waits, bounded occupancy/progress series, write/flush/close and writer drain.
Reports retain stage totals/maxima, FIFO capacity, available raw recorder counters,
overflow/errors, ID provenance/gaps and count/completion checks. Raw recorder
counts are not assumed to be FIFO occupancy; unknown measurements stay unavailable.

Every saved HDF5 pixel is read after close/fsync and checked against source hashes;
keep its sidecar manifest. Pixel failures raise and retain original files. Errors
preserve available profiles, counts, original exception text and partial paths.
The structured interpretation distinguishes camera/host rate gaps, changing
queue/writer behavior, and fsync after capture. Rate differences are not lost-frame
counts. Any FIFO-fill estimate is an explicitly conditional model assuming steady
rates and an initially empty FIFO. Sensor-side missing frames and external-trigger
accounting remain unknown without independent evidence. Pipeline stages overlap;
do not add stage times. Flush cadence remains every 32 batches.

### 4D: synthetic format and synchronization control

Never opens hardware, even with a physical setup. Uses a bounded seeded uint16
bank, distinct 32-bit frame markers and identical JSON metadata. BIN is little-
endian C-order [frame,y,x] with a JSON-lines metadata sidecar. HDF5 keeps /images,
/frame_metadata/json, production chunk shape and batch serialization, labelled
schema diagnostic-1.0 because it omits physical/queue orchestration.

The same one-stage selection applies: C40 or D20/D100/D200 total GB, metadata
reserves included. Default two formats/two sync policies divide each stage into
four files, roughly 10 GB each for C and 5/25/50 GB each on D before reserves.
Longer files increase sustained-load evidence but do not guarantee every SSD
cache is exhausted. Library callers can still request small offline controls.

COMPARE_PERIODIC_SYNC=True compares final-only and periodic-plus-final fsync
(every 0.25 GB); every run ends with fsync. Compare full durable duration, not
just the final pause. The immediate second fsync is outside the durable interval.
Source hash preparation precedes write timing; full pixel/metadata verification
follows it. CPU, bounded write-progress curves, sync and readback timings remain
in the compact report for comparison with physical 4C. Synthetic equivalent FPS
is not physical camera FPS. A short final sync alone does not locate Windows,
controller, firmware, cache or filesystem overhead.

INCLUDE_BIN_COMPARISON remains True: existing evidence established HDF5 versus
native PCO .pcoraw, not BIN. Change it to False once matched repeated evidence
settles that question; the same stage budget then divides into two HDF5 policies.
The storage interpretation explicitly avoids declaring a winner from one pass.

### Compact reports for analysis and charts

Reports are directly in the selected test folder, not in data subfolders.
Section 4B writes `section_4b_<UTC>.json`; its native-recorder troubleshooting
reports are `pco_filemode_<UTC>.json`. Sections 4C/4D write
`section_4c_<UTC>.json` and `section_4d_<UTC>.json` with matching logs.
Attach the appropriate section JSON here for analysis.

Reports use schema version 2.0. Sections 1–8 retain one important-metrics block
and essential settings, without repeating the full raw result. Section 4B no
longer embeds a second copy of itself. Sections 4C/4D use a `columns` header and
`rows` table, plus shared settings/drive information. Time columns use seconds,
rates use decimal MB/s and frame rates use frames/s. Data paths in the table
are relative to that row's drive `output_directory`. Pipeline stages overlap;
do not sum them to estimate total time. Numeric tables retain six decimal
places; pixel checksums and integer counts remain exact.

Keep errors, frame/pixel integrity, write/sync/verification timings, queue and
batch/flush metrics. Repeated settings, nested copies, verbose sample-image
descriptions and derived data that can be reconstructed are omitted. Detailed
progress and 4C/4D exception tracebacks remain in the log. Original acquisition
metadata is unchanged in HDF5 files, and existing historical reports are not
rewritten. In-process callers retain the full results; the PCO comparison can
also read the compact saved Section 4 summary.

## Pixel verification: separate the claims

Use independent status fields for:

1. `pixel_readback`: every stored image was decoded, with expected shape/type.
2. `pixel_source_match`: every stored image matches the pixels delivered by the
   acquisition source, using a digest calculated before storage.
3. `frame_id_integrity`: hardware IDs/counts agree and have no gaps or repeats.
4. `trigger_accounting`: separately measured shot/trigger-to-frame agreement.

Reading pixels successfully is not proof they match the original input.
Hashing only the saved file establishes a fingerprint but lacks an independent
pre-write reference. Checksums do not prove sensor physics or one frame per
laser pulse. A raw conversion that loses hardware IDs must leave the ID
status unavailable; TIFF page numbers are not camera frame IDs.

## Phase 1: verify production HDF5 against input pixels

1. Add an explicit verification option to the sequencer configuration, disabled
   initially for production timing until measured. Keep the driver interface
   and current image layout unchanged. Do not hash display-scaled pixels.
2. In the acquisition producer, after receiving an owned batch and validating
   dimensions/IDs, compute SHA-256 over canonical little-endian uint16 bytes in
   C-order. Compute before queueing or before permitting any buffer reuse.
   Keep start index, frame count, first/last hardware IDs and algorithm/version
   alongside the digest. Avoid an unnecessary whole-run image copy.
3. Carry that immutable digest record with the batch to the writer. Add an
   optional integrity group with block start/count/digest records. Preserve
   `/images`, `/frame_metadata/json`, root metadata and MATLAB reading; document
   the additive schema extension and ensure older readers ignore it safely.
4. After close and final fsync, read each corresponding image block, normalize
   byte order without changing numerical values, and recompute its digest.
   Require exact count/type/shape and all block digests to match. Separately
   run the existing metadata/frame-ID checks.
5. Save results in a sidecar verification JSON: source file identity/size,
   acquisition settings, expected/observed counts, algorithm, block coverage,
   first mismatch, timestamps and verifier version. Distinguish `VERIFIED`,
   `FAILED` and `UNAVAILABLE`; acquisition `complete=True` alone is not verified.
6. On mismatch, retain the original file, label it unverified/failed and stop
   automatic downstream analysis. Do not silently correct pixels or overwrite
   a source file. Do not publish a verification result before all checks finish.
7. Time input hashing and readback separately. At about 298 MB/s, input hashing
   consumes real CPU/memory bandwidth; measure its effect on FIFO errors,
   producer timing and queues before enabling it routinely. Post-run readback
   also consumes storage bandwidth and may compete with the next acquisition.

## Phase 2: obtain an independent PCO native-file reader

The pinned pco 2.2.1 file DLL currently returns `0x80123004` for
`cam.image()`/recorder image copying. Keep that result `UNAVAILABLE`; repeated
calls or ignoring the exception do not verify pixels.

1. Start with a small existing 20-frame `.pcoraw`, offline and without opening
   a camera. Preserve the original file and its hash. Inventory the installed
   SDK/file-DLL versions and check supplier-supported offline reading options.
2. The official `pco.fileconversion` package provides `pco_file_cmd` to convert
   `.pcoraw` to 16-bit TIFF or B16. Prototype an optional adapter around that
   executable using structured subprocess arguments, an explicit executable
   path, timeout, captured diagnostics and a unique output folder. Do not add
   a camera constructor to a file-reader adapter.
3. Use 16-bit raw output, not an 8-bit display export. Verify the installed
   utility's argument/version behaviour using known files. For large recordings,
   prove support for bounded frame ranges or appropriately sized output files;
   do not assume a >4-GB classic TIFF is supported. Preflight conversion space
   in addition to the existing raw file. Native verification/conversion is
   separate from the 30-GB acquisition comparison budget unless explicitly
   included in a revised budget.
4. Read converted frames in batches through a supported reader, with any new
   dependency optional and independently versioned. Check count, uint16 type,
   dimensions and orientation. Verify all pixels can be decoded. Preserve
   native camera metadata when available; document what conversion discards.
5. Compare decoded results with a known reference fixture or a small recording
   for which the vendor provides memory and file access to the same frames.
   Separate recordings of a scene cannot provide exact same-frame pixel truth.
   Until an independent original-pixel reference exists, report `READABLE`,
   not `SOURCE_MATCH_VERIFIED`.
6. Integrate the reader behind `verify_native_file(path, expected_settings)` in
   a file utility module. Invoke after camera closure and outside capture/sync
   timing. Extend the PCO trial report with reader version, coverage, pixel
   status, source-match status and ID status. Do not infer IDs from page order.
7. Keep direct SDK readback as an optional verified backend where supported;
   treat an unsupported backend as unavailable. Evaluate SDK upgrades in an
   isolated environment; do not silently replace the pinned camera package.

Supplier references:

- [PCO fileconversion manual](https://www.excelitas.com/file-download/download/public/103750?filename=pco_fileconversion_Manual.pdf)
- [PCO add-on software](https://www.excelitas.com/product/pco-add-software)

## Phase 3: tests and acceptance gates

- Offline: correctly decoded fixtures, wrong shape/type/count, a flipped pixel,
  a reordered/duplicated/missing block, wrong source digest, truncated input,
  unsupported reader, failed/timed-out converter and interrupted verification.
- Confirm bounded memory, exact MATLAB-compatible axis interpretation and
  read-only source handling. Tests must not need the camera SDK or converter.
- Benchmark verification off/on with identical mock/replay workload, then
  small reviewed physical 20-frame and 2,000-frame runs on the lab PC.
- Require exact input-to-HDF5 pixel matches and existing frame-ID checks.
  Native-file acceptance additionally needs an independent supported reader;
  external trigger accounting remains deferred to Section 8.
- Run sustained physical acquisition on final hardware before routine use.
  Report capture, write, periodic sync, final sync and verification durations
  independently; never hide verification or durability cost from total workflow
  time. Do not mark a partially verified recording as fully accepted.

## One candidate for the capture-versus-durable FPS gap

The observed HDF5 capture/close rate was about 443 fps on both drives, followed
by approximately 12 seconds of final sync on C: or 24 seconds on D:. The flush
request can be waiting for accumulated Windows cache writes, device cache
commit, SSD cache exhaustion/garbage collection or storage-driver/filter
latency. These timings alone do not identify which mechanism dominates.
Low free space and SSD thermal behaviour are also possible contributors.

Test bounded periodic durable checkpoints in 4D first. The HDF5 flush must
precede each OS fsync, and the final checkpoint remains mandatory. If it
improves total durable throughput without excessive write stalls, prototype
the same policy in the production writer thread. Record checkpoint latency,
FIFO errors, queue maximum and complete frame/pixel checks. A physical producer
cannot simply pause while an fsync stalls; the existing bounded queue must
bridge the stall, or acquisition must fail explicitly with a partial file.

If checkpoints merely move the same waiting time into acquisition, they have
not saved time. If device throughput is below the incoming stream, checkpoints
cannot fix that rate deficit. The next controlled comparison would be native
Windows write-through/unbuffered I/O in a separate diagnostic; alignment and
HDF5-driver support must be proven before adopting it. Python `buffering=0`
removes Python buffering but does not disable the Windows disk cache.

Do not disable durability or Windows cache-flush protection to make the reported
FPS look faster. Final sync and data integrity remain required.

[Microsoft explains buffered writes and flushes](https://learn.microsoft.com/en-us/windows/win32/fileio/flushing-system-buffered-i-o-data-to-disk)
and [write-through/unbuffered flags](https://devblogs.microsoft.com/oldnewthing/20210729-00/?p=105494)
as distinct mechanisms.
