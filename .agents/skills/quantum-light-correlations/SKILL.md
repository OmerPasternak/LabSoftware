---
name: quantum-light-correlations
description: Analyzes quantum photon statistics, second-order correlation g^(2)(tau), Hanbury Brown-Twiss coincidence counting, and quadrature squeezing from camera frames or time-tagger streams. Use when computing photon statistics, coincidence histograms, or quantum state non-classicality.
---

# Quantum Light & Correlation Analysis Guide

This skill covers the statistical algorithms and physical metrics used to characterize quantum states of light, high-order harmonic photon statistics, and non-classical correlations.

---

## 1. Second-Order Correlation Function $g^{(2)}(\tau)$

### Definition
For stationary optical fields:
$$g^{(2)}(\tau) = \frac{\langle I(t) I(t + \tau) \rangle}{\langle I(t) \rangle^2} = \frac{\langle : \hat{n}(t) \hat{n}(t+\tau) : \rangle}{\langle \hat{n} \rangle^2}$$

### Physical Interpretations:
* $g^{(2)}(0) = 1$: Coherent state (Poissonian statistics, classical laser light).
* $g^{(2)}(0) = 2$: Thermal / chaotic / amplified spontaneous emission (photon bunching).
* $g^{(2)}(0) < 1$: Non-classical light (sub-Poissonian photon statistics / photon antibunching / single photon state $g^{(2)}(0) \to 0$).
* $g^{(2)}(0) > 2$: Super-bunched light (e.g. squeezed vacuum, high-gain parametric down-conversion).

---

## 2. Hanbury Brown-Twiss Coincidence Algorithm

For discrete time-stamped events from single-photon detectors (APDs / SNSPDs) or time-taggers:

1. **Coincidence Windowing**:
   Events on Channel 1 ($t_1$) and Channel 2 ($t_2$) within window $\tau \in [-\Delta T, +\Delta T]$.
2. **Accidental Coincidence Correction**:
   If channel counts over measurement duration $T_{\text{total}}$ are $N_1$ and $N_2$, the background accidental coincidence rate is:
   $$N_{\text{acc}} = \frac{N_1 N_2 \Delta t_{\text{bin}}}{T_{\text{total}}}$$
3. **Normalized Coincidence**:
   $$g^{(2)}(\tau_k) = \frac{N_{12}(\tau_k)}{N_{\text{acc}}}$$

```python
import numpy as np

def compute_g2_discrete(t1_ps: np.ndarray, t2_ps: np.ndarray, 
                        bin_width_ps: float, max_delay_ps: float, 
                        total_time_s: float):
    """
    Computes normalized g^(2)(tau) from two sorted time-tagger event arrays.
    """
    bins = np.arange(-max_delay_ps, max_delay_ps + bin_width_ps, bin_width_ps)
    delays = []
    
    # Efficient windowed search
    idx2 = 0
    n2 = len(t2_ps)
    for t1 in t1_ps:
        while idx2 < n2 and t2_ps[idx2] < t1 - max_delay_ps:
            idx2 += 1
        curr = idx2
        while curr < n2 and t2_ps[curr] <= t1 + max_delay_ps:
            delays.append(t2_ps[curr] - t1)
            curr += 1
            
    hist, bin_edges = np.histogram(delays, bins=bins)
    
    # Normalization by accidental background
    bin_width_s = bin_width_ps * 1e-12
    n_accidental = (len(t1_ps) * len(t2_ps) * bin_width_s) / total_time_s
    
    g2 = hist / (n_accidental if n_accidental > 0 else 1.0)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    return bin_centers, g2
```

---

## 3. Spatial & Camera Frame Photon Statistics

When analyzing multi-pixel camera frames (sCMOS / EMCCD):

### Mandel $Q$ Parameter:
Quantifies departure from Poissonian statistics:
$$Q = \frac{\langle (\Delta n)^2 \rangle - \langle n \rangle}{\langle n \rangle} = \frac{\sigma_n^2 - \mu_n}{\mu_n}$$
* $Q = 0$: Poissonian (coherent state).
* $Q > 0$: Super-Poissonian (thermal / squeezed light).
* $-1 \le Q < 0$: Sub-Poissonian (quantum non-classical state).

### Quadrature Squeezing Variance
For field quadratures $\hat{X}_1$ and $\hat{X}_2$:
$$\Delta X_1^2 < \frac{1}{4} \quad \text{or} \quad \Delta X_2^2 < \frac{1}{4} \quad (\text{with } [\hat{X}_1, \hat{X}_2] = \frac{i}{2})$$
Expressed in dB below shot-noise:
$$\text{Squeezing (dB)} = -10 \log_{10} \left( \frac{\Delta X^2}{\Delta X_{\text{shot-noise}}^2} \right)$$

