# Inspect saved camera scans

The current camera scan format is HDF5 schema 2.0. Each completed `.h5` file
contains one scan step. Raw image frames have shape `[frame, y, x]` and units
of camera counts (ADU).

From the repository root, run:

```powershell
python -m scripts.inspect_camera_scan data --csv scan_summary.csv --mean-dir mean_frames
```

The input can also be a single completed `.h5` file. The script checks the
schema and completion markers, then prints the frame count, image size, and
mean intensity for each step. It reads frames in small batches. `.h5.partial`
files are skipped because they represent interrupted acquisitions.

The optional CSV contains one row per scan step, including its setpoint,
exposure in seconds, frame count, and mean intensity in ADU. The optional
`.npy` files contain 2D `float64` mean frames in `[y, x]` order and ADU units:

```python
import numpy as np

mean_frame = np.load("mean_frames/example_mean.npy")
print(mean_frame.shape, mean_frame[100, 100])
```

To inspect one frame's JSON metadata, including its timestamp source:

```python
from pathlib import Path
from scripts.inspect_camera_scan import read_frame_metadata

record = read_frame_metadata(Path("data/example.h5"), frame_index=0)
print(record)
```

Frame indices start at zero. Scan parameter units are currently embedded in
the parameter name; check that name before comparing setpoints across files.
