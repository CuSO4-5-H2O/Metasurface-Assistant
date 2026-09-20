# Metasurface Theory Foundations

This file contains the theory and engineering rules distilled from the
literature used while preparing the skill. It is a static reference for
reasoning and evaluation. The skill does not query, control, synchronize, or
otherwise manage a reference manager.

Page numbers below refer to the cited publication PDF. They are traceability
pointers, not a substitute for reading the full paper when a geometry,
measurement condition, or device value is required.

## Core theory

### Equivalent surfaces and generalized transitions

A metasurface can be interpreted through equivalent electric and magnetic
surface currents, surface susceptibilities, or an effective sheet impedance.
Generalized sheet transition conditions connect the tangential field jumps to
these surface quantities. This is useful for explaining reflection, phase,
polarization conversion, and impedance matching, but a fitted sheet model must
not be treated as a full-wave finite-array result.

For simulation, retain the distinction between:

- unit-cell material/geometry response;
- periodic-cell response with Floquet modes and phase-shift boundaries;
- finite-array response including truncation, mutual coupling, feed, and bias
  structures.

### Periodicity, Floquet modes, and angle stability

Periodic boundary phase shifts represent the transverse phase progression of an
infinite periodic surface. Floquet modes must be selected so that all relevant
propagating co- and cross-polarized modes are included. For oblique incidence,
evaluate both TE/TM or the selected polarization basis over the declared
frequency and angle ranges.

A phase or amplitude result at normal incidence alone does not establish angular
stability. Metallic walls, symmetry, and coupling-control structures can change
the angular response, so the skill reports frequency drift, angle drift, and
polarization dependence separately.

### Coding and aperture phase

For a target direction, the ideal aperture phase is computed from the incident
and outgoing transverse wave vectors, plus a declared phase convention and
offset. The continuous phase is then quantized to the legal state phases. The
quantizer uses circular distance, so a phase near +180 degrees is close to one
near -180 degrees.

The skill treats phase quantization and device realization as separate
quantities:

- ideal phase: the phase required by the aperture or scattering objective;
- quantized phase: the selected legal codebook phase;
- realized phase: the CST complex response of that state.

The final array state matrix is not an exhaustive enumeration of all possible
cell combinations. It is generated from the ideal phase map and then refined
with bounded discrete or mixed-variable optimization.

### Reflection, transmission, and absorption

Use complex co-polarized and cross-polarized S/Floquet quantities with the
declared reference impedance and power normalization.

- Reflection power: R = |r|^2.
- Transmission power: T = |t|^2.
- Absorption, where the normalization is valid: A = 1 - R - T.

Reflective, transmissive, and absorptive profiles therefore need different
acceptance metrics. Antenna matching rules must not be applied to a passive
reflective or transmissive unit cell by default.

### Phase quantization and state balance

For a 1-bit codebook, the default adjacent phase target is 180 degrees with a
10-degree circular tolerance. For a 2-bit codebook, the default adjacent target
is 90 degrees with a 10-degree circular tolerance, including the closing
edge from the last state back to the first. N-state codebooks use the same
circular adjacent-edge rule and report full phase coverage.

Phase must be evaluated over the whole declared target band. The report includes
maximum error, RMS error, p95 error, qualified continuous bandwidth, and phase
dispersion. State magnitude is evaluated alongside phase; the default maximum
state-to-state amplitude spread is 1.5 dB unless the task supplies another
limit.

### Active devices, feeds, and bias

PIN devices are naturally represented by legal ON/OFF states and may use a
linear RLC equivalent or a validated SPICE model. Varactors are represented by
a declared capacitance-to-bias mapping, with series resistance, package
inductance, pad parasitics, and bias-network loading included when relevant.
A nonlinear device model is not replaced by an invented generic value.

The validation hierarchy is:

1. device or Design Studio circuit;
2. feed and bias network;
3. periodic or isolated unit cell;
4. finite array or complete antenna.

At each level compare frequency shift, loss, phase error, and gain against the
previous level. Feed networks require S11/S21, insertion loss, amplitude/phase
balance, and isolation checks. Bias networks require DC continuity, RF
leakage, choke/decoupling behavior, via parasitics, and the perturbation of
the unit-cell resonance.

### Radiation-type metasurface antennas

A radiation-type metasurface antenna must be evaluated as an integrated
radiating aperture, not as a reflectarray with an external-feed assumption.
Use realized gain, directivity, aperture efficiency, radiation efficiency,
beamwidth, sidelobes, and cross-polarization together with input matching.
When no absolute gain threshold is supplied, compare against the baseline and
label the result as relative rather than an absolute acceptance claim.

## Literature-derived engineering lessons

| Source | DOI | Evidence location | Engineering lesson | Classification |
| --- | --- | --- | --- | --- |
| Generalized sheet transition theory | 10.1109/MAP.2012.6230714 | PDF p. 1 | Use sheet transitions, susceptibilities, and effective-surface concepts for interpretation; do not substitute them for finite-array full-wave validation. | fact + transferable theory |
| Binary programmable surface | 10.1038/srep35692 | PDF p. 1 | A PIN-controlled unit cell can realize binary coding; keep legal ON/OFF states separate from array-level coding optimization. | fact + workflow rule |
| Transmission 2-bit surface | 10.1038/srep23731 | PDF p. 1 | Two-layer diode control can implement transmission-type 2-bit behavior; evaluate complex S21 or Floquet transmission modes. | fact + profile rule |
| Dual-polarized varactor coding | 10.1364/PRJ.537749 | PDF p. 1 and p. 5 | Two varactor sets can provide independent dual-polarized 2-bit control; validate phase, amplitude, and bias jointly. | fact + acceptance rule |
| Oblique-incidence stability | 10.1088/1361-6463/ad5020 | PDF p. 2 | TE/TM and oblique-incidence stability require angle and polarization sweeps; coupling-control walls can change that stability. | fact + diagnostic rule |
| Active absorptive reflectarray | 10.1109/TAP.2024.3356060 | PDF p. 1 and p. 5 | Near-180-degree state phase difference, gain bandwidth, gain, aperture efficiency, PIN control, and DC support are coupled design concerns; reported values are literature context, not universal thresholds. | fact + design reference |
| Direct-radiating programmable aperture | 10.1002/lpor.202200140 | PDF p. 1 | Direct-radiating programmable metasurfaces require aperture efficiency and direct-radiation metrics in addition to input matching. | fact + profile rule |

## Evidence boundary

A DOI and page pointer support the theory or reported design context above.
They do not authorize a numerical RLC, SPICE, bias curve, material loss,
absolute gain target, or geometry for a new project. Those values must come
from the current task, an identified datasheet, a full paper figure/table, or
a measured/calibrated model. If such a value is missing, the skill creates a
pending-model checklist and stops before an expensive solve.