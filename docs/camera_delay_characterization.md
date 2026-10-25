# Camera diagnostics and exposure-start delay characterization

Open **Diagnostics…** beside Browse/Open in Storage & Log. The separate window
contains X Pearson, Mark Harmonics, Harmonic g², and **Camera delay characterization**.
Saved-data analysis works as before and requires no camera connection.

For characterization, select the intended camera source and existing ROI/exposure
settings, then click **Camera delay characterization**. An idle, disconnected source
connects automatically; active live view stops first. The tool uses one measurement
step of 2,000 frames at each delay, independent of the main scan controls. Its only interactive acquisition
step is drawing one harmonic rectangle on a freshly saved reference and clicking
**Use this window**. Closing selection cancels; STOP cancels selection or acquisition.

The procedure temporarily selects External Exposure Start, so supply the camera's
usual external trigger pulses. It changes only the camera's trigger-to-exposure
delay, not a delay generator or optical stage. It preserves exposure, ROI and
readout mode. Delay and trigger mode are restored after each acquisition phase,
including cancellation or failure. Cleanup failures explicitly flag unknown camera
state and block further camera commands until disconnect/reconnect. No physical
camera is exercised by automated development tests.

The scan takes five points across **0–100 ns**, then ten across **1900–2100 ns**.
Targets round to the SDK's supported timing grid; insufficient range/resolution
causes an explicit failure. The report and graph use verified actual delay values.
Each point gets a separate recorder session and schema-2.0 raw HDF5 file. The
existing intensity tool sums every raw pixel inside the rectangle for each frame,
then calculates its average and sample STDEV (N−1). Error bars show fluctuations
across individual frames, not standard error of the average. No background
subtraction, filtering or frame rejection is performed; raw files remain unchanged.

At startup the log estimates duration and raw disk usage from frame count,
exposure, ROI, a 50 MiB/s storage assumption, a 10 ms frame/readout floor and
1 second overhead per file. Selection time and unknown external-trigger waits
are additional, so this is a planning estimate rather than a timing guarantee.
Higher frame counts improve fluctuation sampling but require more time and disk.

After successful acquisition and camera restoration, a Python window pops up with
two side-by-side plots, one per delay range. A separate `camera_delay_<run>.json`
report and PNG graph live in the current storage directory alongside all raw
files and the selected rectangle JSON. Successful point reports and incomplete raw
files survive STOP/failure; incomplete runs do not open a success graph. The mock
exercises the whole workflow and labels the graph SIMULATED; its intensities do
not model a physical dependence on delay.

The driver uses the installed PCO 2.2.1 `delay_time` property and device delay
descriptors, with readback checks of delay and unchanged exposure. See the
[vendor API manual](https://www.excelitas.com/file-download/download/public/103377?filename=pco_python_Manual.pdf).
Physical acceptance must verify trigger timing, delay resolution, frame identity,
and restoration on the connected camera before relying on characterization results.
