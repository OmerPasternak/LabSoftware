---
name: hhg-attosecond-optics
description: Provides physics formulas, conversions, and calibration workflows for High-Harmonic Generation (HHG), attosecond pump-probe delay lines, and flat-field XUV spectrometers. Use when calculating laser intensities, delay conversions, cut-off energies, or spectrometer calibrations.
---

# High-Harmonic Generation (HHG) & Attosecond Optics Formulas

This skill provides verified scientific formulas, physical constants, and calibration routines for HHG experiments, pump-probe delay lines, and XUV spectrometers.

---

## 1. Laser Intensity & Strong-Field Parameters

### Physical Constants (SI)
* Speed of light: $c = 2.99792458 \times 10^8\text{ m/s}$
* Electron charge: $e = 1.602176634 \times 10^{-19}\text{ C}$
* Electron mass: $m_e = 9.1093837 \times 10^{-31}\text{ kg}$
* Reduced Planck constant: $\hbar = 1.0545718 \times 10^{-34}\text{ J}\cdot\text{s} = 6.582119 \times 10^{-16}\text{ eV}\cdot\text{s}$

### Pondermotive Energy ($U_p$)
Pondermotive energy of a free electron oscillating in a laser field of peak electric field $E_0$ or intensity $I$:
$$U_p = \frac{e^2 E_0^2}{4 m_e \omega^2}$$

In convenient laboratory practical units:
$$U_p [\text{eV}] \approx 9.33 \times 10^{-14} \times I_0 [\text{W/cm}^2] \times (\lambda_0 [\mu\text{m}])^2$$

*Example*: For $\lambda_0 = 0.800\,\mu\text{m}$ (Ti:Sapphire) and peak intensity $I_0 = 1.5 \times 10^{14}\,\text{W/cm}^2$:
$$U_p = 9.33 \times 10^{-14} \times 1.5 \times 10^{14} \times (0.8)^2 \approx 8.96\,\text{eV}$$

### Three-Step Model Harmonic Cut-off Law
$$E_{\text{cutoff}} = I_p + 3.17\, U_p$$
Where $I_p$ is the ionization potential of the target gas (e.g., Argon $I_p = 15.76\,\text{eV}$, Neon $I_p = 21.56\,\text{eV}$, Helium $I_p = 24.59\,\text{eV}$).

---

## 2. Optical Delay vs. Attosecond Timing Calibration

When scanning an optical delay line with a linear motorized translation stage:

### Reflection Geometry (Michelson / Mach-Zehnder Retroreflector):
Moving the stage by physical distance $\Delta x$ changes the optical path by $2 \Delta x$:
$$\Delta \tau = \frac{2 \Delta x}{c}$$
* Practical rule of thumb: **$1\,\mu\text{m}$ physical stage motion $= 6.671\,\text{fs}$ time delay** (or **$1\,\text{fs} \approx 0.1499\,\mu\text{m}$**).

### Single-Pass Geometry:
$$\Delta \tau = \frac{\Delta x}{c}$$
* Practical rule of thumb: **$1\,\mu\text{m}$ physical stage motion $= 3.336\,\text{fs}$ time delay**.

```python
SPEED_OF_LIGHT_UM_PER_FS = 0.299792458  # um / fs

def stage_um_to_delay_fs(stage_delta_um: float, passes: int = 2) -> float:
    """Convert stage displacement in micrometers to optical delay in femtoseconds."""
    return (passes * stage_delta_um) / SPEED_OF_LIGHT_UM_PER_FS

def delay_fs_to_stage_um(delay_fs: float, passes: int = 2) -> float:
    """Convert optical delay in femtoseconds to stage displacement in micrometers."""
    return (delay_fs * SPEED_OF_LIGHT_UM_PER_FS) / passes
```

---

## 3. Flat-Field Grazing Incidence XUV Spectrometer Calibration

For variable line-spacing (VLS) concave grating or flat-field spectrometers:
$$d(\sin \alpha + \sin \beta) = m \lambda$$
Where $\alpha$ is the grazing incidence angle, $\beta$ is the diffraction angle, and $m$ is diffraction order (typically $m=1$).

### Pixel-to-Energy Mapping & Jacobian Correction:
When mapping camera pixel positions $x$ to wavelength $\lambda(x)$ and subsequently to photon energy $E(x) = \frac{hc}{\lambda(x)}$:
* Energy in eV: $E [\text{eV}] \approx \frac{1239.84}{\lambda [\text{nm}]}$
* **Jacobian Intensity Correction**:
  Because $dE = - \frac{hc}{\lambda^2} d\lambda$, the spectral intensity must be scaled to preserve integrated energy density:
  $$I_E(E) = I_\lambda(\lambda) \times \left| \frac{d\lambda}{dE} \right| = I_\lambda(\lambda) \times \frac{hc}{E^2} = I_\lambda(\lambda) \times \frac{\lambda^2}{hc}$$
  *Failing to apply this Jacobian distorts harmonic peak intensities towards the high-energy cut-off.*

