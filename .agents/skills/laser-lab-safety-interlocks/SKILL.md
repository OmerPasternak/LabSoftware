---
name: laser-lab-safety-interlocks
description: Implements software safety boundaries, hardware interlocks, and fail-safe recovery procedures for stages, laser shutters, high-voltage MCPs, and vacuum systems. Use when defining stage travel limits, laser safety controls, or emergency stop handlers.
---

# Laser Lab Safety Interlocks & Safe States

This skill defines safety patterns, limits, and fail-safe recovery procedures required for unattended and attended operations in an ultrafast laser / HHG laboratory.

---

## 1. Hardware Travel & Velocity Boundary Rules

1. **Hard Limit vs. Soft Limit**:
   - Hardware limits: Physical limit switches on translation stages.
   - Software limits (`min_pos`, `max_pos`): Configured inside the driver to be strictly narrower than physical limits to prevent crashes into mechanical hard-stops.
2. **Never Allow Raw Bypass**:
   - Position targets must pass through `validate_position()` before any command is generated.
   - Relative moves (`move_relative(delta)`) must compute `target = current + delta` and validate `target` against absolute limits first.

```python
def move_relative(self, delta: float) -> None:
    current = self.get_position()
    target = current + delta
    self.validate_position(target)
    self.move_to(target)
```

---

## 2. Fail-Safe Recovery States

Every instrument or sequencer subsystem must implement a **Safe State** that is triggered on:
- Unhandled exceptions
- Lost communication (timeout or bus disconnection)
- User emergency abort
- Process termination (signals `SIGINT`, `SIGTERM`)

### Safe State Matrix:
| Subsystem | Safe State Action | Danger if Left Unchecked |
| :--- | :--- | :--- |
| **Laser Shutter** | Close immediately (`shutter.close()`) | Stray beam / laser burns to optics / sample degradation |
| **Delay Stages** | Stop motion immediately (`stage.stop()`) | Crashing into optical mounts or cables |
| **High Voltage (MCP / EMCCD)** | Ramp down bias voltage to 0 V | Sensor burnout if pressure spikes |
| **Gas Jet / Mass Flow** | Close pulsed valve / shut off gas | Flooding vacuum chamber, turbo pump trip |
| **Camera Sensor Cooling** | Keep fan running, avoid thermal shock | Condensation if chamber is vented while cold |

---

## 3. Vacuum & Interlock Guard Patterns

Before firing lasers into targets or powering detectors, check vacuum pressure:

```python
class InterlockViolationError(RuntimeError):
    """Raised when an environmental safety interlock fails."""
    pass

class SafetyManager:
    """Monitors experimental conditions and guards hazardous operations."""
    
    def __init__(self, max_allowed_pressure_mbar: float = 1e-4):
        self.max_allowed_pressure = max_allowed_pressure_mbar
        
    def check_vacuum(self, current_pressure_mbar: float) -> None:
        if current_pressure_mbar > self.max_allowed_pressure:
            raise InterlockViolationError(
                f"Vacuum pressure {current_pressure_mbar:.2e} mbar exceeds safety limit "
                f"{self.max_allowed_pressure:.2e} mbar! Aborting."
            )
            
    def emergency_abort(self, instruments: dict) -> None:
        """Executed during critical safety failures."""
        if "shutter" in instruments:
            try:
                instruments["shutter"].close()
            except Exception as e:
                pass
        if "stage" in instruments:
            try:
                instruments["stage"].stop()
            except Exception as e:
                pass
        if "high_voltage" in instruments:
            try:
                instruments["high_voltage"].set_voltage(0.0)
            except Exception as e:
                pass
```

