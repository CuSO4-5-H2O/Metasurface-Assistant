# Active Devices, Feed, and Bias Validation

This reference is the engineering contract for programmable metasurfaces. It does not provide universal PIN or varactor values. Device values must come from the current device datasheet, a user-provided model, or a traceable paper. If the source or a required model value is missing, emit the `device_model_completeness` preflight checklist, keep the study inspectable, and stop before an expensive solve.

## Device models

- A linear equivalent model may be declared as a series or parallel RLC with explicit units and a state table. Keep package parasitics and the reference plane in the study record.
- A nonlinear or SPICE model must include `source_path`, `netlist`, or `inline_netlist`, plus the simulator and circuit assumptions needed to reproduce it. The runner routes these cases to `isolated` unless the user explicitly overrides the backend.
- PIN states normally require at least ON and OFF values for series resistance, junction capacitance, and package inductance. A varactor codebook requires a bias-to-capacitance relation or a per-state RLC table. Do not infer these values from the desired phase separation.
- For each state, record the CST parameter map, device model identity, bias value, and legal range. A state is invalid when any of these is absent or inconsistent with the source project.

## Four-level debug order

1. **Device / Design Studio circuit**: verify DC operating point or circuit impedance, RLC/SPICE state switching, current direction, and convergence.
2. **Feed and bias network**: verify input matching, insertion loss, phase and amplitude balance, isolation between branches, DC continuity, RF leakage, choke/decoupling behavior, and via parasitics.
3. **Periodic unit cell**: compare the complex reflected/transmitted response of every legal state over frequency and incidence angle. Report resonance shifts, loss, amplitude spread, and circular phase error against the target.
4. **Finite array / antenna**: evaluate input match, realized gain, radiation efficiency, beamwidth, sidelobe level, cross-polarization, scan loss, and the selected state map. Attribute changes to the previous level rather than hiding them in one final score.

Each level should have a `metrics_file` containing named scalar metrics and an optional `evidence` list with CST result paths. The report computes deltas between adjacent levels; missing levels are reported as validation gaps.

## Backend selection

`auto` chooses `isolated` if the study or any state declares a SPICE/nonlinear device model, topology changes, `spice_netlist`, or `independent_project`; otherwise it chooses `native_sweep`. The user may override either backend, but the report must retain the override and the reason.

`native_sweep` is appropriate when topology and monitors are invariant and only numeric parameters change. `isolated` is required when state changes alter the circuit topology, use independent projects, need failure isolation, or must be audited separately.

## Acceptance rules

- A two-state codebook defaults to 180 degrees plus or minus 10 degrees; a four-state codebook checks adjacent 90 degree steps, circular closure, and total phase coverage.
- State amplitude spread defaults to 1.5 dB. A phase pass at a deep field null is not meaningful; use the configured magnitude floor.
- Test the device-only and feed/bias-only levels before accepting a full-wave array candidate. A solver exit code alone never validates a state.

## Array coding

Compute the ideal aperture phase from the requested beam or wavefront, quantize it to the validated state codebook, and simulate only the requested code maps or optimization candidates. Do not claim exhaustive search over all array states.
