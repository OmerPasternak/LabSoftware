---
name: pyvisa-scpi-motion
description: Guides robust PyVISA, serial (RS-232/USB), and SCPI communication for lab hardware, including motion controllers (Thorlabs, Newport, SmarAct) and lock-in amplifiers. Use when implementing hardware communication protocols, debugging timeouts, or handling bus errors.
---

# PyVISA & SCPI Motion Control Guide

This skill provides best practices, connection templates, and troubleshooting steps for communicating with lab instruments over PyVISA (USB, RS-232, GPIB, Ethernet) and vendor ASCII/binary protocols.

---

## 1. Resource Initialization & Clean Resource Management

Always configure termination characters and query timeouts explicitly upon opening any PyVISA resource:

```python
import pyvisa

rm = pyvisa.ResourceManager()
# Example resource names:
# USBTMC: "USB0::0x1AB1::0x04CE::DS1ZA123456789::INSTR"
# Serial COM: "ASRL3::INSTR" or "COM3"
# Ethernet/VXI-11: "TCPIP0::192.168.1.100::inst0::INSTR"
# Ethernet Raw Socket: "TCPIP0::192.168.1.100::5025::SOCKET"

inst = rm.open_resource("ASRL3::INSTR")
inst.timeout = 5000  # 5 seconds timeout
inst.read_termination = "\r\n"   # Set according to device manual (\n or \r\n)
inst.write_termination = "\r\n"
inst.baud_rate = 115200          # Serial only
```

### Essential Rules
1. **Never leave connection unclosed**: Wrap in `try...finally` or implement context managers (`__enter__` and `__exit__`).
2. **Always flush buffers on connect**: Clearing stale bytes prevents out-of-sync responses where command A gets the response of command B:
   ```python
   inst.clear()  # VISA clear or custom buffer flush
   ```
3. **Handle Query Timeouts Gracefully**: High-torque or long-distance stage moves may take seconds; increase timeout for motion completion or use polling on motion status rather than blocking on a single command.

---

## 2. Common Vendor Motion Controller Patterns

### Newport (CONEX-CC, ESP300/301, SMC100)
Newport controllers typically use ASCII commands addressed by axis ID:
* Move absolute: `1PA15.250` (Move axis 1 to 15.250 mm)
* Query position: `1TP?` $\to$ Returns `1TP15.250`
* Query motion state: `1TS?` $\to$ Returns status byte (check if in motion, ready, or disabled)
* Stop immediately: `1ST`
* Home: `1OR` (Execute homing search)

```python
def move_newport_axis(inst, axis: int, target_mm: float) -> None:
    inst.write(f"{axis}PA{target_mm:.4f}")

def get_newport_position(inst, axis: int) -> float:
    resp = inst.query(f"{axis}TP?")
    # Strip prefix e.g. "1TP"
    return float(resp.replace(f"{axis}TP", "").strip())
```

### Thorlabs (Kinesis / APT Protocol)
* Many Thorlabs controllers (KDC101, BSC201) communicate via a 6-byte packet protocol over FTDI virtual COM ports.
* Prefer using `pylablib` (`pylablib.devices.Thorlabs`) or the official `kinesis` Python wrapper when available, rather than bit-packing 6-byte headers manually.
* If using raw serial commands, configure: Baud `115200`, 8 data bits, 1 stop bit, no parity, RTS/CTS flow control enabled.

### Stanford Research Systems (SRS SR830 / SR860 Lock-in)
Standard SCPI-like ASCII syntax:
* Query signal magnitude (R): `inst.query("OUTP? 3")` (or `OUTR?`)
* Query X and Y: `inst.query("SNAP? 1,2")`
* Query time constant: `inst.query("OFLT?")`
* Set sensitivity: `inst.write(f"SENS {sens_index}")`

---

## 3. Reconnection & Error Handling Pattern

In high-power laser labs, EMI (electromagnetic interference) from pulsed laser discharge or flashlamps can cause intermittent USB dropouts. Implement retry decorators:

```python
import time
from functools import wraps
import pyvisa

def retry_on_visa_error(max_retries: int = 3, delay: float = 0.5):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(self, *args, **kwargs)
                except pyvisa.VisaIOError as err:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                    self.reconnect()
        return wrapper
    return decorator
```

