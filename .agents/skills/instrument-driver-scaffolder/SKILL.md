---
name: instrument-driver-scaffolder
description: Scaffolds new instrument drivers for the HHG/attosecond laboratory following the Actuator vs. Detector architecture, safety boundary validation, and mock-first development pattern. Use when adding or refactoring lab hardware drivers.
---

# Instrument Driver Scaffolding Playbook

This skill guides the design and implementation of modular, reliable instrument drivers for experimental laser physics, strictly enforcing the project's **Actuator vs. Detector** abstraction and hardware-optional development principles.

---

## Core Principles

1. **Strict Separation of Concerns**:
   - **Driver Layer**: Uniform interface (`connect()`, `close()`, hardware getters/setters). No knowledge of scans or GUIs.
   - **Sequencer Layer**: Composes drivers into scan loops. No knowledge of hardware-specific buses.
   - **UI Layer**: Thin presentation layer talking only to the sequencer and driver state.

2. **Actuators vs. Detectors**:
   - **Actuators (Movers / Setters)**: Delay stages, mirror mounts, rotation stages, gas valves.
     - Contract methods: `move_to(target)`, `get_position()`, `is_moving()`, `stop()`, `home()`.
   - **Detectors (Sensors / Readers)**: Cameras, spectrometers, power meters, lock-in amplifiers, oscilloscopes.
     - Contract methods: `acquire()`, `set_exposure()` / `set_integration_time()`, `get_data()`.

3. **Mock-First Development**:
   - Every physical driver MUST have a corresponding `Mock<Instrument>` class implementing the exact same public interface and emitting realistic synthetic data.
   - Offline tests and sequencer logic must run against mocks without physical hardware.

4. **Strict Standard Scientific Units**:
   - Enforce unambiguous units at the interface boundaries:
     - Distance/Position: `mm` or `um` (explicitly documented, never raw motor counts/steps).
     - Optical Delay: `fs` (femtoseconds).
     - Time: `s` (seconds).
     - Wavelength: `nm`.
     - Energy: `eV`.
     - Voltage: `V`.

---

## 5-Step Workflow for New Instruments

### Step 1: Protocol & Safety Audit
- Determine physical bus: USB COM (RS-232), USBTMC, Ethernet (TCP/IP), PCIe, GPIB.
- Check communication protocol: SCPI ASCII vs. binary vendor DLL / C SDK.
- Record strict physical safety limits (minimum/maximum travel range, maximum velocity, laser shutter interlocks).

### Step 2: Ecosystem Survey
- Check existing open-source implementations: PyMoDAQ plugins (`pymodaq_plugins_*`), PyMeasure, QCoDeS.
- Reuse tested protocols where possible rather than rewriting low-level parsing.

### Step 3: Define Base Abstract Contract (`Base<Instrument>`)
- Inherit from `abc.ABC`.
- Define `@abstractmethod` signatures.
- Implement software limit validation directly in base class setters before invoking hardware hooks.

```python
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)

class BaseActuator(ABC):
    """Abstract base class for all actuators/positioners."""
    
    def __init__(self, min_position: float, max_position: float, unit: str = "mm"):
        self.min_position = min_position
        self.max_position = max_position
        self.unit = unit
        self.is_connected = False

    def validate_position(self, target: float) -> None:
        """Enforce strict hardware safety limits."""
        if not (self.min_position <= target <= self.max_position):
            raise ValueError(
                f"Target position {target} {self.unit} exceeds safe travel range "
                f"[{self.min_position}, {self.max_position}] {self.unit}"
            )

    @abstractmethod
    def connect(self) -> None:
        """Establish communication with the hardware."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Safely disconnect and return instrument to safe state."""
        pass

    @abstractmethod
    def move_to(self, target: float) -> None:
        """Move actuator to absolute target position."""
        pass

    @abstractmethod
    def get_position(self) -> float:
        """Query current position."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Immediately abort motion."""
        pass
```

### Step 4: Implement the Mock Class (`Mock<Instrument>`)
- Simulate realistic physical response:
  - Movement speed and transit delays (`time.sleep` or simulated clock).
  - Synthetic detector data (Gaussian beams, interference fringes, noise).

```python
import time
import numpy as np

class MockActuator(BaseActuator):
    """Simulated actuator for offline testing."""
    
    def __init__(self, min_position: float = 0.0, max_position: float = 50.0):
        super().__init__(min_position, max_position, unit="mm")
        self._position = 0.0
        self._is_moving = False

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def move_to(self, target: float) -> None:
        self.validate_position(target)
        self._is_moving = True
        time.sleep(0.01)  # Simulate travel
        self._position = target
        self._is_moving = False

    def get_position(self) -> float:
        return self._position

    def stop(self) -> None:
        self._is_moving = False
```

### Step 5: Implement Hardware Driver & Benchtop Validation
- Subclass the base class, initialize PyVISA or vendor SDK in `connect()`.
- Wrap hardware calls with try/except, ensuring fail-safe recovery on failure.
- Create a minimal benchtop scratch script in `tests/` to verify communication safely before automated scanning.

