# Camera control validation

Target: Excelitas PCO pco.edge 5.5, USB 3.0, 2560 x 2160, 16-bit.

## Controls

Run `python -m hhg_control.ui.camera_gui`. Select Simulation (default), connect,
and press Start Live. Stop Live finishes the current frame. This preview repeats
single-frame acquisitions; it is not a full-rate continuous recorder and frames
between acquisitions are not captured. The timestamp beside the image is UTC host
receipt time, not sensor exposure time. Auto scale follows each frame; Full 16-bit
uses 0?65535 ADU. Scaling changes display only.

Stop acquisition before entering ROI bounds and pressing Apply Hardware ROI.
Coordinates are zero-based (x start, y start, x end, y end), upper bounds excluded.
Full Sensor resets to (0, 0, 2560, 2160). Actual camera readback is displayed.
Hardware ROI excludes other pixels from returned data; it does not block light.
The mock models horizontal steps of four, minimum 64 x 16, and vertical symmetry.
The real adapter reads constraints from the connected camera. Unsupported bounds
are rejected, not silently enlarged. HDF5 stores ROI coordinates and per-frame
metadata in `frame_metadata_json`; full sensor dimensions remain separate.

Real camera mode reports connection failures without substituting simulation.
Exposure is bounded by the lab-approved 0.5?10000 ms and the connected camera's
reported limits. Hardware limits may be narrower for the active shutter mode.
The SDK is used because the existing project already wraps PCO's camera API.
PyMoDAQ's plugin catalog was checked; no migration to a different framework is
introduced for these controls.

## Before deployment

This change has only been tested offline. Review the real adapter changes before
connecting hardware, as required by AGENTS.md. No real camera was opened in tests.

1. Confirm the existing LabVIEW camera VI's SDK calls, shutter mode, trigger mode,
   binning and any timestamp settings. This audit is still outstanding.
2. Close other applications holding the camera. Select Real camera explicitly.
3. Check reported model/serial and full-frame output against CamWare.
4. At a reviewed exposure, start/stop preview, repeat connection cycles, then test
   a supported ROI and full-sensor restoration. Compare returned sizes with CamWare.
5. Verify that invalid ROI/exposure values are rejected. Test connection loss and
   confirm recording stops. Acquisition waits have a finite timeout; Stop Live
   is graceful, not an emergency interruption of an exposure.
6. Verify HDF5 data and ROI offsets in MATLAB. Validate timing separately before
   synchronized experiments. Host receipt timestamps are insufficient for that.

Closing during acquisition requests a graceful stop and keeps the window open;
close again after the worker finishes. Real hardware performance, ROI restrictions
for the installed firmware, and external trigger operation remain unvalidated.

## Sources

- [Manufacturer datasheet](https://www.excelitas.com/assets/product/document/pcoedge-55-usb-datasheet.pdf)
- [PCO Python SDK manual](https://filehub.excelitas.com/api/public/dl/MMIlwdGz/PCO_MA_PCOPYTHON.pdf)
- [PyMoDAQ plugin catalog](https://github.com/PyMoDAQ/pymodaq_plugin_manager)
