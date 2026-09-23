---
name: pyqt-pyqtgraph-streaming
description: Designs high-performance, non-blocking lab GUIs with PyQt/PySide and pyqtgraph for real-time camera streaming, live scan progress, and thread-safe hardware control. Use when building or updating lab user interfaces, live viewers, or scan controls.
---

# Real-Time PyQt & PyQtGraph Lab GUI Guide

This skill provides design patterns for building high-speed, non-blocking instrumentation GUIs capable of streaming camera feeds at 50–100 fps and running long scans without UI lag.

## Current camera GUI

The camera GUI places compact color-scale controls over the image's top-right
corner and uses a custom Matplotlib ROI outline. GO starts live acquisition,
RUNNING is status-only, and STOP ends live acquisition. A camera source may be
changed only while idle; the previous source is closed in a worker before the
next GO connects the new selection. Its acquisition worker owns a persistent
ring buffer and waits on a thread event for each GUI repaint before offering
another frame. The canvas reuses its image and timestamp artists, downsamples to display size,
and blits only the changing plot area. A local offscreen render-only check on
full-sensor arrays measured about 24 fps; this is not a physical-camera or
end-to-end throughput guarantee. Benchmark the complete mock and hardware
paths before raising the target rate, and use pyqtgraph if the required rate
exceeds what this display can sustain.

---

## 1. Thread Separation Architecture

**Rule**: The Qt GUI Main Thread must NEVER block on hardware calls (e.g. `stage.move_to()` or `camera.acquire()`).

```text
[Main GUI Thread]  <===== Qt Signals =====>  [Acquisition / Sequencer Worker Thread]
  - Renders UI widgets                          - Waits for camera frame
  - Updates pyqtgraph image                     - Commands stage motion
  - Handles user button clicks                  - Writes to HDF5 file
```

### Worker Pattern with `moveToThread`:
```python
from PyQt5.QtCore import QObject, QThread, pyqtSignal
import numpy as np

class AcquisitionWorker(QObject):
    frame_ready = pyqtSignal(np.ndarray)
    finished = pyqtSignal()
    
    def __init__(self, camera_driver):
        super().__init__()
        self.camera = camera_driver
        self._running = False
        
    def start_stream(self):
        self._running = True
        while self._running:
            frame = self.camera.acquire()
            self.frame_ready.emit(frame)
        self.finished.emit()
        
    def stop(self):
        self._running = False
```

Wiring in the main window:
```python
self.worker_thread = QThread()
self.worker = AcquisitionWorker(self.camera)
self.worker.moveToThread(self.worker_thread)

self.worker_thread.started.connect(self.worker.start_stream)
self.worker.frame_ready.connect(self.update_live_display)
self.worker.finished.connect(self.worker_thread.quit)
```

---

## 2. Low-Overhead Rendering with PyQtGraph

Never use Matplotlib for live streaming $>5\text{ fps}$. Use `pyqtgraph.ImageView` or `pyqtgraph.ImageItem`:

```python
import pyqtgraph as pg

class LiveCameraWidget(pg.GraphicsLayoutWidget):
    def __init__(self):
        super().__init__()
        self.plot_item = self.addPlot()
        self.image_item = pg.ImageItem()
        self.plot_item.addItem(self.image_item)
        
        # Optimize performance: autoLevels=False avoids recalculating min/max every frame
        self.image_item.setAutoDownsample(True)
        
    def update_frame(self, frame_2d: np.ndarray):
        # pyqtgraph expects (X, Y); if frame is (Y, X), use transpose or lut
        self.image_item.setImage(frame_2d, autoLevels=False)
```

---

## 3. Responsive Emergency Abort

When the user clicks "ABORT SCAN":
1. Immediately set an atomic flag: `self.abort_requested = True`.
2. Emit an immediate signal to the hardware worker to call `stage.stop()` and `shutter.close()`.
3. Disable scan controls in the UI to prevent re-triggering while shutting down.

