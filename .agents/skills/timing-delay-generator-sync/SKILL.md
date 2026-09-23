---
name: timing-delay-generator-sync
description: Configures digital delay/pulse generators (e.g., SRS DG645, Quantum Composers) to synchronize laser repetition rates, pulsed gas valve nozzles, camera exposure triggers, and optical choppers. Use when setting up experiment timing, trigger delays, or hardware synchronization.
---

# Digital Delay Generator & Laser Synchronization Guide

This skill covers the configuration and synchronization of digital delay generators (e.g., Stanford Research Systems DG645, Quantum Composers) to synchronize pulsed laser systems with experiment detectors and targets.

---

## 1. Typical Laser Lab Synchronization Sequence

For a kHz laser system (e.g., 1 kHz or 10 kHz repetition rate) operating with a pulsed gas valve (Even-Lavie / Parker valve) and an sCMOS camera:

```text
Laser Master Clock (1 kHz) --[Trigger In]--> DG645
                                              |
     +--- Channel AB: Gas Valve Pulse -------+ (Fires ~200-500 us BEFORE laser arrives)
     |
     +--- Channel CD: Camera Exposure Gate --+ (Opened 10 us before laser, closed after)
     |
     +--- Channel EF: Optical Chopper Ref ---+ (Sub-harmonic e.g. 500 Hz for pump on/off)
     |
     +--- Channel GH: Oscilloscope / Digitizer Trig
```

### Gas Nozzle Flight Time Offset:
Gas molecules traveling from the nozzle orifice at thermal speed ($\sim 500-1500\text{ m/s}$) take several hundred microseconds to reach the laser interaction zone.
$$\Delta t_{\text{trigger}} = \Delta t_{\text{flight}} + \Delta t_{\text{valve opening delay}} \approx 250 - 450\,\mu\text{s}$$
The delay generator must fire the gas valve **prior** to the laser arrival. In a periodic pulse train, this is achieved by delaying from the *preceding* laser trigger pulse:
$$T_{\text{delay}} = T_{\text{laser period}} - \Delta t_{\text{lead}}$$

---

## 2. Stanford Research Systems DG645 SCPI Programming

```python
import pyvisa

class DG645Driver:
    """Controls SRS DG645 Digital Delay Generator."""
    
    # Channel mappings
    T0 = 0
    AB = 1
    CD = 2
    EF = 3
    GH = 4
    
    def __init__(self, visa_address: str):
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(visa_address)
        self.inst.read_termination = "\r\n"
        self.inst.write_termination = "\r\n"
        
    def set_trigger_source(self, source: str = "EXT_RISING"):
        """
        source: 'INT', 'EXT_RISING', 'EXT_FALLING', 'SS' (Single shot)
        """
        src_map = {"INT": 0, "EXT_RISING": 1, "EXT_FALLING": 2, "SS": 5}
        self.inst.write(f"TSRC {src_map[source]}")
        
    def set_channel_delay(self, channel: int, delay_seconds: float, reference_channel: int = 0):
        """
        Sets delay of channel relative to reference_channel (default T0).
        """
        self.inst.write(f"DLAY {channel},{reference_channel},{delay_seconds:.9e}")
        
    def set_channel_amplitude(self, channel: int, amplitude_volts: float):
        """Sets output level for standard 50 Ohm TTL / High-Z."""
        self.inst.write(f"LAMP {channel},{amplitude_volts:.2f}")
```

---

## 3. Trigger Impedance & Signal Integrity

* **50 $\Omega$ vs. High-Z Termination**:
  * For sub-nanosecond jitter and clean square pulses, always use $50\,\Omega$ coaxial cables (RG-58 or RG-223).
  * Ensure the receiver (e.g. camera trigger input) matches the expected impedance. Driving a High-Z input with a $50\,\Omega$ calibrated output will double the pulse voltage (e.g., $2.5\,\text{V} \to 5\,\text{V}$).
* **Optocoupled vs. Direct TTL**:
  * Long trigger cables from high-power pulsed laser power supplies can carry ground loops and high-voltage spikes. Use opto-isolated trigger inputs on cameras and sensitive electronics.

