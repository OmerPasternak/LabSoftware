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
Python axis order. Its chunks contain one frame and at most 512 × 512 pixels;
gzip level 1 with shuffle keeps standard HDF5 compression while limiting CPU
time during acquisition. Frames are acquired and written in bounded batches.

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
