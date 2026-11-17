# Switching camera readout mode

Switching Rolling Shutter / Global Shutter requires a physical camera reboot.
The last displayed frame stays visible during the operation. The status identifies
reboot/restoration and waiting for the first new frame. Settings and the display
color interval are preserved; the first new frame refreshes intensity metrics
immediately. No startup frames are hidden, discarded or altered.

The driver snapshots exposure, ROI, trigger mode, programmable camera delay,
available hardware I/O lines (enable, electrical type, polarity, filter, selection
and parameters), and compatible SDK configuration (timestamp, acquisition mode,
metadata, binning and noise-filter setting). It restores and verifies these after
reconnect. This preserves existing settings; it does not enable new corrections.
A failed restoration locks acquisition instead of silently using camera defaults.
Pixel clock/rate is mode-specific and stays selected by the firmware; copying the
rolling pixel clock into global mode is not appropriate. Exposure must satisfy
the target mode limits; Global Shutter remains capped at 100 ms.

`[READOUT TIMING]` in Storage & Log reports settings snapshot, reboot command/close,
reboot wait, reconnect and restore/verification durations, in seconds. A separate
entry measures total time until the first displayed frame, including stopping
live view and waiting for an external trigger. The existing 4.5-second reboot
wait has not been shortened: fake-SDK tests cannot establish an earlier hardware
ready time. Reopening the vendor Camera also resets defaults and arms the device.

A saturated display is not sufficient evidence of sensor saturation. Check the
raw ADU metrics and the selected color interval. These are separate from shutter
timing. Global Shutter uses a different sensor timing sequence; identical external
trigger settings do not establish that a short optical pulse falls inside its
exposure. Physical validation is still required for trigger acceptance, exposure
timing, startup transients and total switching latency.

Vendor references: [pco.edge manual, sections 6.2 and 7.2](https://www.excelitas.com/file-download/download/public/102439?filename=pco_edge_3_1__4_2__4_2_LT_and_5_5_User_Manual.pdf).
