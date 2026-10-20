# Acquired data are immutable

This is a non-negotiable experiment requirement. Save every acquired pixel
exactly as returned by the camera SDK, with its frame order and metadata.
Never alter acquired data to improve appearance, speed, detection, statistics,
or apparent acquisition success.

## Acquisition and storage

- Never apply background/dark subtraction, smoothing, filtering, normalization,
  gain scaling, clipping, interpolation, binning, masking, pixel replacement,
  bit-depth reduction, lossy compression, frame averaging or frame rejection to
  the acquired payload. Do not silently enable vendor correction features.
- Keep the original values of every frame. Do not drop, reorder, fabricate or
  repair frames to hide an error. Preserve failed/partial data and report it.
- Buffering, copying, batching, lossless storage and checksums are allowed only
  when all pixel values and frame identities are preserved exactly.
- Explicitly configured ROI, exposure, trigger and readout are hardware settings;
  record them faithfully. They do not authorize changes to returned pixels.
- Never weaken frame-ID, pixel-integrity or completion checks for performance.

## Display and explicitly requested analysis

The user may request derived plots/statistics (for example, an integrated
spectrum, Pearson correlation or SI-corrected g2). These must read source files
without modifying them, operate on separate working arrays, and save results
separately with their methods, settings and reference provenance. Never replace
raw data with a derived result or feed analysis corrections into acquisition.
Display scaling/downsampling must likewise never mutate acquisition buffers.
Do not introduce additional preprocessing without the user's explicit request.

Physical harmonic detection uses its original y-summed analysis path; the new
spatial background-rejection step is simulation-only. Synthetic fixtures and
simulation-specific algorithms must never be substituted into physical data.

Any future code change must preserve this boundary. If an optimization needs
changed pixel values or discarded frames, reject that optimization and report
the limitation instead. Offline tests establish software behavior; physical
validation is still needed to establish device/SDK correctness.
