# Camera diagnostics and exposure-start delay characterization

Open **Diagnostics…** beside Browse/Open in Storage & Log. The separate window
contains X Pearson, Mark Harmonics, Harmonic g², and **Camera delay characterization**.
Saved-data analysis works as before and requires no camera connection.

For characterization, select the intended camera source and existing ROI/exposure
settings, then click **Camera delay characterization**. An idle, disconnected source
connects automatically; active live view stops first. Set **Frames per delay** in
the diagnostics window (default 2,000; minimum 2 for sample STDEV), independent of
the main scan controls. It sets the frame count for one measurement step at each
delay; the reference remains one frame. Its only interactive acquisition step is
drawing one harmonic rectangle on a freshly saved reference and clicking
**Use this window**. Closing selection cancels; STOP cancels selection or acquisition.

The characterization always uses External Exposure Start, regardless of the main
GUI trigger selection. Before the reference and every delay acquisition it verifies
the mode and enabled input, failing without recording if either is incorrect.
The procedure temporarily selects and verifies External Exposure Start and enables
the PCO HWIO-1 exposure-trigger input if needed, preserving its configured rising
or falling edge. The camera records one frame for each accepted external trigger
pulse. Supply one pulse for the reference and the selected frame count at each of
the 15 delay points; the GUI does not drive or pace the trigger source. It changes
only the camera's trigger-to-exposure delay, not a delay generator or optical stage.
It preserves exposure, ROI, readout mode and the trigger input's prior enable state.
Delay, trigger mode and input state are restored after each acquisition phase,
including cancellation or failure. Cleanup failures explicitly flag unknown camera
state and block further camera commands until disconnect/reconnect. Mock tests verify
the selected trigger mode and input-state lifecycle but cannot model electrical pulse
acceptance; no physical camera is exercised by automated development tests. The raw
HDF5 files record the active external-trigger input enable and edge polarity alongside
the trigger mode.

The scan takes five points across **0–100 ns**, then ten across **1900–2100 ns**.
Targets round to the SDK's supported timing grid; insufficient range/resolution
causes an explicit failure. The report and graph use verified actual delay values.
Each point gets a separate recorder session and schema-2.0 raw HDF5 file. The
delay remains fixed until every requested frame has arrived, its iterator has
closed, and the writer has finished. Before any next delay or restoration,
the physical driver checks both recorder inactivity and SDK sensor recording
state `off`. A failed/unknown stop blocks setting changes and marks the camera
state unknown. PCO 2.2.1 `record()` stops/deletes the previous recorder and creates
a fresh one, so an old recorder's queue cannot be consumed under the next delay.
External pulses between recordings are outside the acquisition; this is not
a gapless measurement across the delay transitions.

Each saved frame's JSON includes `acquisition_context`: run ID, delay-point
index, verified `actual_delay_ns`, trigger mode, and the assignment method.
The same snapshot is stored in the file's `frame_acquisition_context_json`
attribute. This is the constant camera setting for that isolated recording,
not a delay inferred from host arrival time or a per-frame electrical timing
measurement. Delay readback is checked again after recording. A mismatch or
failed stop prevents plotting/deletion and retains the data for inspection.

The existing intensity tool sums every raw pixel inside the rectangle for each frame,
then calculates its average and sample STDEV (N−1). Error bars show fluctuations
across individual frames, not standard error of the average. No background
subtraction, filtering or frame rejection is performed; raw files remain unchanged.

At startup the log estimates duration and raw disk usage from frame count,
exposure, ROI, a 50 MiB/s storage assumption, a 10 ms frame/readout floor and
1 second overhead per file. Selection time and unknown external-trigger waits
are additional, so this is a planning estimate rather than a timing guarantee.
Higher frame counts improve fluctuation sampling but require more time and disk.

After successful acquisition and camera restoration, the Python graph is saved
with two side-by-side plots, one per delay range. Once that PNG saves successfully,
the tool deletes this run's reference and delay HDF5 files, selection JSON, and
summary JSON, then opens the graph. A failed or cancelled run retains its data;
an existing PNG also prevents cleanup. The mock labels the graph SIMULATED; its
intensities do not model a physical dependence on delay.

The driver uses the installed PCO 2.2.1 `delay_time` property and device delay
descriptors, with readback checks of delay and unchanged exposure. See the
[vendor API manual](https://www.excelitas.com/file-download/download/public/103377?filename=pco_python_Manual.pdf).
Physical acceptance must verify trigger timing, delay resolution, frame identity,
and restoration on the connected camera before relying on characterization results.
