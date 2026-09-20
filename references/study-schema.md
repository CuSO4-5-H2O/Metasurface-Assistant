# Study Schema

The study file is JSON so it can be archived with the run.
`scripts/metasurface_study.template.json` is the minimal starting point.

Required fields:

- `profile`: `radiation_antenna`, `reflective_programmable`,
  `transmissive_programmable`, or `absorber`.
- `source_project`: a concrete `.cst` path for a live run.
- `output_dir`: a concrete run directory.
- `frequency_spec`: `{start, stop, unit, target}`.
- `state_codebook`: a list of states with `id`, `bits`, and `parameters`.
- `evaluation`: result tree paths and thresholds.

Useful optional fields are `state_backend`, `solver`,
`editable_parameters`, `budget`, `device_evidence`, and `array`.
State parameter maps must use CST parameter names and numeric values. A state
that changes topology must set `topology_changes: true`. A state using a
SPICE netlist must include its source path or inline model reference; no
physical default is synthesized.

The runner writes `study_plan.json`, `states/<state_id>/state.json`, runtime
args files, and `study_summary.json`. The plan is valid even when no solver
is run.



For post-run aggregation, evaluation.codebook_treepath selects the complex
trace used to compare states. scripts/metasurface_metrics.py evaluate-study
scans states/<state_id>/exports/*.json, preserves CST parameter identity, and
writes a state-level result plus a no-gap codebook bandwidth summary.

Optional v1.0 execution fields:

- device_model: {"kind":"rlc_series","R":...,"L":...,"C":...} or a
  SPICE object with source_path, netlist, or inline_netlist.
- array: rows, cols, state phase values, or target/incident directions
  plus element spacing for ideal aperture phase generation.
- integration_levels: ordered device, feed/bias, unit-cell, and finite-array
  metric files; the report computes deltas between adjacent levels.
- evaluation.threshold_sources: optional per-threshold provenance strings.
  Explicit values without a source are labeled `study_configuration`, derived
  phase spacing is labeled `derived_from_state_count`, and conservative
  defaults are labeled as defaults in `acceptance_thresholds`.
- spike_refinement: `{enabled, max_passes, mesh_operations}`. Automatic local
  reruns require an explicit mesh recipe and the solved candidate project path.
  Every pass works on a new isolated copy, dry-runs before applying, preserves
  raw exports, and blocks optimization until the suspected spike is resolved.
- baseline_evaluation, candidate_evaluations, and optimization.objectives

optimization.engine may be cst_runtime_optimization or pymoo. For pymoo,
optimization.parameters may be either a mapping of name to {type, min, max}, or
a list of objects with name, type, lower, and upper. It accepts float, int,
binary, and categorical variables; binary variables default to 0/1 and
categorical variables require a non-empty values list. Optional constraints
contain parameter bounds or bounded sums. The runner writes
optimization/pymoo-candidates.json, but every candidate still needs a CST solve
and parameter-matched export before it can be evaluated.
For array-level discrete search, set optimization.array_state to an object with
rows, cols, and either values/states. If the values are omitted, the runner
derives them from state_codebook. Each candidate includes both the flattened
parameter mapping and a state_matrix; it is still a finite candidate set and
never an exhaustive N^cells enumeration.
Use `baseline_evaluation` and `candidate_evaluations` to enable traceable
baseline-versus-candidate ranking.
- After execution, metasurface_report.py writes study_report.json with the
  state codebook, threshold provenance, device parameter sources, feed/bias
  results, best-structure summary, projects and exports, failed states, code
  maps, level deltas, convergence history, mesh evidence, and remaining risks.
- mesh_convergence: task_path, at least two independent mesh_levels, objective, tolerance, and optional result paths; the runner delegates this to the resumable CST runtime pipeline.
Network-specific optional fields:

- `feed_network` and `bias_network` may point to a standalone CST project or
  provide `metrics_file`, `sparameter_treepaths`, `dc_continuity`,
  `dc_resistance_ohm`, and `choke_isolation_db`. Their results are evaluated
  separately from the unit-cell codebook.
- Each state may carry `bias`, `device_state`, `topology_changes`, and a
  state-specific `device_model`. A missing device evidence path or missing
  RLC/SPICE value produces a `device_model_completeness` checklist in
  `preflight_gates`, sets `execution_allowed=false`, and blocks an expensive
  solve. The runner also writes `device-model-completion.json` with null-valued
  RLC/SPICE, evidence, and state-mapping fields to complete without inventing
  device data. Malformed model kinds and negative/non-numeric RLC values remain
  hard validation errors.
- `device_evidence` is a list of source paths and short claims. It is an audit
  pointer, not a replacement for the actual CST model or exported result.

## Classification and antenna gate

Optional classification records the physical evidence class, such as periodic_floquet_unit or finite_radiating_antenna. A Floquet Zmax/Zmin reflection result may be evaluated as a reflective unit, but it must not satisfy the radiation-antenna gate. For antenna studies, radiation_antenna_gate records whether a driven feed port, recognizable input S11, matched far-field export, and mesh evidence are all present. remaining_validation records unresolved gates and is copied into the generated report.

## Optimization preflight gate

For a radiation_antenna profile, the runner emits preflight_gates and
optimization_allowed in study_plan.json. The plan is blocked when
evaluation.sparameter_treepaths contains no recognizable driven-feed input
path such as S1,1/S11. Floquet SZmax/Zmax and SZmin/Zmin reflection paths are
intentionally excluded. A declared radiation_antenna_gate.status of pending,
needs_validation, failed, or not_satisfied also blocks optimization. The state
plan can still be inspected, but run_isolated records blocked_preflight and
never invokes the optimization runtime until the gate is resolved.
