# Camera scan HDF5 format (schema 2.0)

Each completed scan step is one `.h5` file. Acquisition writes to a matching
`.h5.partial` file first and renames it after the camera, metadata, and file
close complete. A partial file is evidence of an interrupted or failed step;
readers should only treat completed `.h5` files as scan inputs.

```text
/
├── images                 uint16 [frame, y, x]
└── frame_metadata/
    └── json               one UTF-8 JSON record per frame
```

`/images` contains raw camera counts (ADU), without display color scaling. The
dataset attributes `dimension_order` and `data_dimension_ordering` describe the
Python axis order. Chunks stay within one frame and 1 MiB, and tile the frame
without padding at its edges.
New scans default to uncompressed HDF5 to avoid a CPU bottleneck; optional gzip
level 1 with shuffle remains available for slower scans. Both use schema 2.0,
and the `storage_compression` root attribute records the chosen setting. Frames
are acquired in bounded batches and passed to a bounded writer queue. A full
queue pauses synthetic acquisition until storage catches up. A physical camera
fails the step and retains a `.partial` file rather than silently dropping frames.

Root attributes include `schema_version`, `complete`, `frames_written`,
`experiment_name`, `run_id`, `scan_step_index`, `scan_parameter_name`,
`scan_parameter_value`, `exposure_time_s`, `roi_bounds`, camera identity,
readout mode, and an acquisition UTC timestamp. Each frame's JSON record also
contains its timestamp source: the camera SDK when available, or an explicitly
marked host/simulated fallback. A scan parameter's unit is currently part of
its display name, such as `Delay Stage (mm)`; a dedicated machine-readable
unit field should be added before automated cross-instrument analysis.

For MATLAB, inspect the dataset size and attributes with `h5info`, then read
`/images` with `h5read`. MATLAB and Python may present HDF5 dimensions in a
different order, so verify the resulting array shape against the stored axis
attributes before indexing frames. The `frame_metadata/json` values are UTF-8
JSON strings that can be decoded separately.

The older `/entry/...` layout described in some general lab examples is not
this camera schema. Do not silently mix the two layouts in one analysis path;
introduce a new schema version and reader migration if the storage hierarchy
changes.

## GUI measurement pipeline (4F/4G conclusions)

The measurement button calls `CameraScanManager.execute_scan`. This path uses
uncompressed HDF5 and batches of four frames by default, preserving the existing
one-file-per-step schema, naming, resume behavior and final-frame preview.
An explicitly configured gzip manager remains supported for slower workflows.
The underlying `acquire_and_save_step` method remains the camera-to-writer
primitive used by diagnostics; callers using it directly must arrange their
own durability/verification as the diagnostic suites already do.

For a GUI measurement, a dedicated reader owns one SDK iterator across its
files, including the final FIFO/error check and stop. It retrieves continuously into a bounded RAM queue;
hashing, HDF5 writes and closed-file work do not run in that reader. The GUI's
existing RAM budget is shared between this queue and a one-batch HDF5 queue.
Buffers allocate only as data arrives, and expanded buffers enforce the
existing physical-memory reserve. FIFO/recorder errors, frame-ID gaps, exhausted
RAM and physical queue overflow fail explicitly and retain partial files.

One frame is copied for the existing step-completion preview. Pixels are never
display-scaled or binned for storage. Exposure, ROI, shutter and trigger remain
the selected, verified settings. The current GUI labels metadata setpoints;
it explicitly selects `execute_scan(continuous=True)`, so camera reads can
advance ahead of the next file's metadata callback. Default API calls keep
`continuous=False`: a new reader starts after each step-start callback, preserving
the required setpoint boundary for future actuator scans. Never use continuous
metadata mode to assign frames across a physical instrument state change.
The camera remains connected; no constructor, reconnect or shutter reset is
introduced by this pipeline.

The consumer computes SHA-256 source hashes after the reader queue and before
HDF5 writing, using canonical little-endian uint16 pixels. Source references
are streamed to `<stem>.pixels.sha256.jsonl`, not accumulated as a whole-run
Python list. The header records shape and acquisition settings; each subsequent
line records a contiguous source range, digest and first/last SDK frame ID.
Interrupted manifests retain `.partial`. Keep the completed manifest beside
its HDF5 file if later source comparisons are needed.

Two independent spawned processes handle closed files: one fsyncs HDF5 and its
manifest; the other verifies every saved pixel, consecutive IDs, exact counts,
completion and acquisition settings. They receive paths/plain metadata only,
never a camera, Qt object or open HDF5 handle. Verification reads at most
64 MiB of pixels at a time (or one frame if larger). At most five files await
checks. At a file boundary it can wait for a verification slot. The continuous
GUI reader keeps draining into RAM during that wait and stops with an error if
the bounded queue fills; a gated actuator scan instead starts exposures only
after a slot is available. No measurement file is recycled or deleted.

Each file gets `<stem>.verification.json` with PENDING, VERIFIED, FAILED or
UNVERIFIED status. Verified reports retain source/reader/queue/write timings,
minimum available RAM, FIFO capacity and independent durable/verified rates.
Those rates begin at that file's preparation and include downstream queue waits.
With continuous read-ahead they describe per-file processing throughput, not
independent sensor cadence. Reader counters are for the whole recording;
writer counters are file-local. Overlapping stage times must not be summed. `complete=True` in
HDF5 still means capture/write completion;
the sidecar separately establishes durability request and source-pixel fidelity.
Existing step-completed GUI signals mean the file has closed. The existing
scan-finished signal is emitted only after every completed step passes sync
and verification. Errors use the existing GUI error path. STOP stops retrieval
and retains a partial step, then completes checks for already closed steps.
Frames already buffered ahead of the stopped file are counted as unsaved in
reader diagnostics; they are never relabelled as a completed measurement step.

Successful completion also logs `Durable FPS: <value>`: total synced frames in
the current scan segment divided by first-file acquisition preparation through
final-file sync completion. File transitions count; worker startup and the
verification tail do not. A continuation is labelled `resumed segment` and
excludes earlier segments and pauses. Calculating the rate adds no image work.

This preserves older MATLAB/Python readers: `/images`, `/frame_metadata/json`,
root metadata and schema version 2.0 are unchanged. Sidecar names do not match
the readers' `*.h5` scan-file pattern. Timing totals/maxima remain exact;
percentiles use at most the latest 4,096 batches to bound diagnostic RAM.

Fsync and cached readback do not prove persistence through power loss. Pixel
equality compares saved images with host source pixels; it cannot establish
sensor accuracy or one image per external trigger. The 4F/4G physical evidence
supports the scheduling strategy, but this integrated GUI path still needs a
short physical run followed by sustained acceptance on the actual data drive.
For very large individual steps, sync/readback starts after that step's file
closes; automatic file splitting would require an explicit reader/schema plan.
