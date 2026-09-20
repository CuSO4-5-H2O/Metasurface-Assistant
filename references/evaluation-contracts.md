# Evaluation Contracts

## Project profiles

`radiation_antenna` uses port matching and far-field radiation metrics.
`reflective_programmable` uses complex reflection response of a unit cell or
Floquet mode. `transmissive_programmable` uses the selected complex
transmission mode. `absorber` evaluates reflection, transmission, and
absorption together.

## Antenna matching and bandwidth

Convert complex S parameters with `20*log10(abs(S))`. A passing sample is one
whose dB value is at or below the configured threshold. Report every contiguous
passing interval, then select the widest interval. Never bridge an unqualified
gap just because both outer endpoints pass.
The -10 dB default is an antenna input-matching rule only. Reflective, transmissive, and absorptive profiles do not receive an automatic S11 pass/fail threshold; they use their declared complex reflection, transmission, absorption, phase, and amplitude metrics.

Report target band, threshold, widest interval, fractional bandwidth, minimum
value, worst value inside the interval, sample count, interpolation policy, and
the exact result file.

## Smoothness and spikes

Use local slope and second-difference metrics normalized by the actual frequency
step. Mark a candidate as `suspected_spike` when a point has a large residual
from the line joining its neighbors and exceeds a robust MAD-based score. A
suspected spike triggers local frequency refinement and, when it affects the
decision, an independent mesh check. A physical narrow resonance is reported, not erased.

The study runner keeps all raw CST exports for audit and records the current candidate in `state.json.selected_exports`; stale run IDs are not evaluated. A detected spike produces `needs_refinement` with a local frequency window and suggested step. The study-level evaluator aggregates these into `refinement_requests`.

When `spike_refinement.enabled=true`, `mesh_operations` contains an explicit
mesh operation, and a solved parameter-matched project copy is available, the
runner copies that exact candidate, dry-runs a local `define-frequency-range`
recipe plus the mesh operation, applies it, resolves, archives the new export,
and reevaluates it for at most `max_passes`. Missing mesh policy or solved
project identity yields `blocked_configuration` without a CST call. A spike
that persists after the budget is retained as a possible physical resonance;
it is never silently removed.

## Phase codebooks

For N ordered states, the nominal adjacent phase step is `360/N` degrees. Use
the circular error
`abs(((measured - target + 180) % 360) - 180)`.

For each frequency in the target band, compare adjacent states and the closing
state-to-state-0 edge. Report maximum and RMS phase error, valid continuous
bandwidth, total circular coverage, and state amplitude spread.

## Frequency and angular phase

For S-parameter phase, unwrap each state along frequency only after confirming
frequency ordering and reference impedance. Compare state differences at each
frequency, not only at the nominal design frequency.

For far field, select `E_theta`, `E_phi`, or an explicitly requested
Ludwig-3 component. Keep frequency, excitation, reference plane, theta/phi cut,
and polarization fixed. Unwrap along the requested angular cut and report valid
angle span, phase range, maximum step, and maximum deviation from the chosen
reference angle. Mask samples below the configured magnitude floor.
A Ludwig-3 request must declare `evaluation.ludwig3_reference_axis` as `x` or
`y`; it is derived from the jointly exported complex `E_theta` and `E_phi`.

## Gain and radiation

Only `Realized Gain`, `Gain`, or `Directivity` can support a gain claim.
Record frequency, excitation, coordinate convention, peak direction, beamwidth,
side-lobe level, efficiency when available, and cross-polarization. `Abs(E)`
is field magnitude, not dBi gain.

## Evidence status

Use `validated` only when the project/result tree or exported file exists and
the requested data were parsed. Use `needs_validation` when a solver returned
but the requested evidence is absent. Use `partial` when some states or
channels are valid and others are missing.


CST `farfield_grid` exports contain a theta-by-phi scalar grid rather than a
complex angular trace. For gain evaluation, select the configured fixed-phi
cut, preserve the grid frequency and coordinate convention, and calculate
peak realized gain, 3 dB beamwidth, and the maximum value outside the main
lobe. A grid without complex `E_theta`/`E_phi` data cannot support a phase
claim; report that phase channel as missing instead of inferring it from gain.

## Feed and bias networks

A feed-network result should expose input matching, forward insertion loss,
output amplitude balance, output phase balance, and isolation. A bias-network
result should separately record DC continuity/resistance, RF leakage, choke or
decoupling isolation, and via/parasitic evidence. These are network-level
checks and must not be replaced by the unit-cell S parameter score.

## Antenna matching gate

For radiation_antenna, a recognizable input match trace must contain a continuous interval satisfying the configured threshold, normally S11 <= -10 dB. If no such interval exists, the trace and study status are failed_constraints; far-field gain, beamwidth, or sidelobe results do not override the matching failure. Floquet Zmax/Zmin reflection is evaluated under the reflective-unit profile and must not be relabeled as antenna input matching.
