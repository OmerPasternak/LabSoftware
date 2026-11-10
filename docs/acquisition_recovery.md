# Recovery of returned camera payloads

The acquisition raw-data policy still applies: pixels and frame identities are
never corrected, rescaled, cast, rejected silently, or assigned to a different
setpoint to hide a failure. Successful schema-2.0 acquisition files are unchanged.

## Failure and STOP behavior

The reader stops and joins its SDK-owning thread before recovery storage begins.
Already accepted writer batches finish normally where the writer still works.
Unreturned SDK-local frames, reader queues/carry, a returned batch not yet accepted
by the writer, and uncertain/queued batches after a writer failure are retained.
They are saved separately as `*.recovery.hdf5.partial`. Ordinary `*.h5`/`*.hdf5`
scan discovery cannot treat these files as measurements.

Continuous recording may have read ahead of the current metadata-only file.
Recovery tails are explicitly **unassigned**; they never enter a later setpoint
file. A failed continuous source is checked before another step callback.
Failed runs are not reported as complete. Existing partial HDF5 and partial
source-reference manifests remain available alongside the recovery file.

## Recovery format (camera-recovery-1.0)

The HDF5 root always has `complete=false`. It records `origin_file`, `reason`,
`step_attribution`, and `acquisition_context_json` (known settings from the
origin/cached recording). Context is provenance, not a new setpoint assignment.
There is deliberately no normal `/images` dataset.

Each `/payload_00000000`, `/payload_00000001`, ... group contains:

- `pixels`: the original array, with its original dtype, shape and logical pixel
  order. Malformed arrays are not converted to the schema-2.0 uint16 contract.
  A metadata-only unmatched SDK return has no pixels dataset.
- `metadata_json`: a complete ownership-time snapshot using the existing JSON
  conversion rules (NumPy scalars/arrays become JSON numbers/lists; times/paths
  become strings). Missing/mismatched metadata is retained rather than invented.
- `context_json`: original recording ordinal/range and ownership boundary.
  `may_overlap_partial=true` identifies an interrupted writer attempt whose
  pixels may also exist in the ordinary partial file. This is an explicitly
  uncertain duplicate copy, not an additional acquired frame.
- `pixels_sha256` and `metadata_sha256`: source references for the saved values.
- `record_complete`: whether that group's array and metadata finished writing.
  It does not make the acquisition complete or certify physical timing.

A failed write attempt is never overwritten. Retry appends a new attempt; use
recording context plus original IDs, not IDs alone, when investigating overlap
or recorder counter rollover. Never concatenate these groups into a new scan or
infer missing samples automatically.

## Storage failure and retry

Each recovery record is acknowledged only after HDF5 close and host fsync.
If recovery storage fails, current/later buffers remain owned by the original
`CameraScanManager`, including when its per-file writer goes out of scope.
The error explicitly reports that RAM retention is not durable. Keep the session
alive, restore storage, then call `manager.retry_pending_recovery()` on the I/O
worker while idle. Inspect `manager.pending_recovery_payloads` and
`manager.last_recovery` to confirm that nothing remains pending. New measurement,
preview and live acquisitions are blocked while pending recovery remains.
Unsupported object-valued arrays are retained in RAM; they are never pickled.

For direct driver use without a saving sequencer, the matching physical/mock
interface exposes `pending_acquisition_payloads` and
`recover_pending_payloads(receive)`. The callback accepts an owned payload;
ownership is released only after the callback succeeds. The driver issues no
filesystem operations and retains local failure evidence across close/reconnect.

No software can make unavailable storage durable or safely terminate a stuck
OS/HDF5 operation. These conditions remain explicit failures; no hardware-owning
thread is forcibly terminated. Sensor cadence and external-trigger acceptance
still require physical validation.
