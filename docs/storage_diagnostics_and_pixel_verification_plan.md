# Storage diagnostics and pixel verification integration plan

This plan describes implementation work after the new diagnostics. Pixel
verification of physical acquisitions is not implemented by this document.
The synthetic 4D control already compares every saved pixel against its known
source and checks metadata IDs; it is not a physical camera acceptance test.

## Run the new diagnostics

Open `application tests/lab_computer_acceptance.ipynb`, restart its kernel after
updating code, run setup, then run the desired individual cell. All diagnostic
reports and logs go to files; no reports or plots are embedded in the notebook.
Use one destination per drive. Every requested drive is preflighted for the
complete retained payload before recording or writing. Files are retained in
new directories and research data is never overwritten or deleted.

### 4C: batches of 4, 16 and 64

Uses the existing configured camera and production HDF5 path. Physical runs
apply the already reviewed serial, ROI, exposure, shutter and Auto Sequence;
mock runs exercise the same orchestration. On each drive, it records
capture-only and saved timing for 2,000 frames at each batch size. The default
one repetition retains about 3.93 GB at 2560x128. The default cap is 8 GB.
Two repetitions reverse batch order on the second pass; set the cap to at least
9 GB for that profile, provided free-space preflight permits it.

The first destination's `BENCHMARK_ONLY_section_4c_*` folder contains the
combined `report.json` and `execution.log`. Each drive retains its data files.
Inspect:

- Capture-only FPS versus saved and durable FPS for each batch.
- HDF5 batch-write durations and periodic/final flush time.
- Producer queue waits, maximum occupancy and post-capture drain time.
- Frame count, consecutive IDs, file completion and final fsync time.
- Repetition variability before choosing a batch size for production.

Changing the batch size also changes the cadence of the current writer's
flush every 32 batches. This is intentionally a comparison of practical batch
profiles, not a test that independently attributes gains to chunk shape or
flush frequency. The production chunk layout is unchanged.

### 4D: identical data in BIN and HDF5

Never opens hardware, even if setup selects a physical camera. Uses a bounded
seeded random uint16 frame bank, with distinct 32-bit frame markers and
identical per-frame JSON metadata. BIN is little-endian, C-order
`[frame, y, x]`, with a JSON-lines sidecar; dimensions and byte order are in the
report. HDF5 uses `/images` and `/frame_metadata/json`, production chunk shape
and `_write_batch` serialization. It is labelled `diagnostic-1.0`, not research
schema 2.0, because this isolates synchronous storage operations and omits
production acquisition/queue orchestration.

Default payload is approximately 2 GB per format: 4 GB per drive. The output
cap cannot exceed 30 GB. Preparation and source hashing occur before write
timing. All pixels and JSON frame IDs are read back in bounded blocks and
checked after the mandatory final fsync; readback has separate timing.

The first drive runs HDF5 then BIN; the next reverses that order. One pass is
not enough to establish a robust sustained advantage. Small payloads can fit
in OS/SSD caches, even though the final sync is measured.

`COMPARE_PERIODIC_SYNC=True` adds another pair, syncing every 0.25 GB by
default and still syncing at the end. It doubles the default retained payload
to about 8 GB per drive. It affects only this synthetic diagnostic. Compare
total durable seconds/MB/s, not just a shorter final pause. An immediate second
fsync is recorded after the timed durability interval; a fast second call is
consistent with the first having drained outstanding writes, but does not
identify the specific driver or SSD mechanism by itself.

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
