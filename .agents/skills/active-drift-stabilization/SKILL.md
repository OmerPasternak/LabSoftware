---
name: active-drift-stabilization
description: Implements closed-loop PID active stabilization for laser pointing (quadrant photodiode/camera to piezo mirror) and interferometer phase drift (HeNe fringe tracking to piezo stage). Use when developing drift-correction loops, PID controllers, or long-term scan stabilizers.
---

# Active Drift Stabilization & PID Feedback Loops

This skill covers closed-loop software control for beam pointing stabilization and interferometer phase locking in ultrafast and attosecond experiments.

---

## 1. Experimental Drift Modes in HHG

1. **Spatial Pointing Drift**:
   Thermal expansion of optics and air currents cause the laser focus to wander from the gas jet nozzle, dramatically reducing harmonic yield.
   - Sensor: 4-quadrant photodiode (QPD) or secondary CMOS beam profiler.
   - Actuator: Piezo-motorized mirror mount (e.g., Newport Picomotor, Thorlabs Polaris).
2. **Interferometer Phase Drift**:
   Vibrations and temperature gradients cause nanometer-scale path length fluctuations in Mach-Zehnder or delay lines ($10\text{ nm} \sim 33\text{ as}$ optical delay jitter).
   - Sensor: Co-propagating HeNe (632.8 nm) reference beam fringe pattern photodiode / lock-in.
   - Actuator: Fast piezo translation stage / mirror.

---

## 2. Robust Discrete PID Controller Implementation

```python
import time

class DiscretePID:
    """Discrete PID controller with anti-windup and output clamping."""
    
    def __init__(self, kp: float, ki: float, kd: float, 
                 output_limits: tuple[float, float],
                 setpoint: float = 0.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.min_out, self.max_out = output_limits
        self.setpoint = setpoint
        
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = time.time()
        
    def update(self, current_value: float) -> float:
        now = time.time()
        dt = max(now - self._last_time, 1e-4)
        
        error = self.setpoint - current_value
        
        # Proportional term
        p_term = self.kp * error
        
        # Integral term with anti-windup clamping
        self._integral += error * dt
        i_term = self.ki * self._integral
        
        # Derivative term
        d_term = self.kd * (error - self._last_error) / dt
        
        output = p_term + i_term + d_term
        
        # Output clamp to prevent over-driving piezo actuators
        clamped_output = max(self.min_out, min(self.max_out, output))
        if output != clamped_output:
            # Prevent integral windup when saturated
            self._integral -= error * dt
            
        self._last_error = error
        self._last_time = now
        return clamped_output

    def reset(self):
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = time.time()
```

---

## 3. Stabilization Interleaving During Long Scans

In long pump-probe scans, active stabilization should be interleaved between scan steps:

```python
def acquire_scan_with_stabilization(scan_manager, stabilizer, steps):
    for target in steps:
        # 1. Pause feedback loop during physical movement
        stabilizer.pause()
        scan_manager.stage.move_to(target)
        
        # 2. Re-enable feedback or settle for 100-200 ms to lock position
        stabilizer.settle_and_lock(duration_s=0.2)
        
        # 3. Acquire science frame while locked
        data = scan_manager.camera.acquire()
        scan_manager.save_step(data)
```

