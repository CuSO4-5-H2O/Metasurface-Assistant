---
name: metasurface-assistant
description: >
  Use this skill for active or programmable metasurfaces, metasurface antennas,
  RIS, reflectarrays, coding states, PIN/varactor devices, bias and feed
  networks, CST multi-state simulation, phase-codebook evaluation, and bounded
  electromagnetic optimization.
metadata:
  version: "1.0.0"
---

# Metasurface Assistant v1.0

Use this skill when the request involves a metasurface or antenna whose design
must be evaluated or optimized from CST results. It is the domain layer on top
of `cst-simulation-workflow`, `cst-ai-optimization-toolkit`,
`cst-runtime-cli`, and the CST MCP.

## Routing

1. Classify the project as `radiation_antenna`, `reflective_programmable`,
   `transmissive_programmable`, or `absorber`. Do not apply antenna S11
   rules to a reflective surface automatically.
2. Inspect the source project and work on a run copy. Run a health check and a
   dry-run recipe before any solver call.
3. Inspect parameters, result-tree items, far-field monitors, ports, solver,
   mesh, and the active state/device model. Resolve ambiguous tree paths before
   optimizing.
4. Use native CST MCP for live Design Studio, circuit, geometry, solver, and
   native parameter-sweep actions. Use the runtime CLI for auditable runs,
   exports, studies, and optimization campaigns.
5. Evaluate exported complex data with `scripts/metasurface_metrics.py` before
   selecting a candidate. A solver exit code is never sufficient evidence.
6. Keep legal device states explicit. Validate an isolated unit-cell response
   before validating selected array coding patterns with a full-wave run. Do not
   enumerate every array combination.
7. Preserve failed candidates, logs, state manifests, parameter combinations,
   and result ownership. Never use the newest or largest CST run ID as a proxy
   for the current candidate.
8. Preserve every raw export for audit, but evaluate only the explicit
   `state.json.selected_exports` set. Treat `needs_refinement` and
   `failed_constraints` as non-acceptable candidate states.
## Required references

- Read [evaluation-contracts.md](references/evaluation-contracts.md) for metric
  selection, phase circular error, continuous bandwidth, spike diagnosis, and
  far-field phase rules.
- Read [active-devices-and-bias.md](references/active-devices-and-bias.md) for
  PIN/varactor/SPICE, feed, bias, and four-level validation.
- Read [study-schema.md](references/study-schema.md) before creating a
  `metasurface_study.json` file.
- Read [cst-integration.md](references/cst-integration.md) when calling CST MCP
  or the runtime bridge.
- Read [theory-foundations.md](references/theory-foundations.md) for the
  distilled metasurface theory and literature-derived engineering rules. This
  skill does not operate a reference manager; use only the static conclusions here.

## Entry points

For deterministic post-processing:

```powershell
python scripts/metasurface_metrics.py evaluate-results --input <results.json> --output <evaluation.json>
python scripts/metasurface_metrics.py evaluate-study --study-dir <study_dir> --output <study_evaluation.json>
python scripts/metasurface_metrics.py compare-backends --reference <native.json> --candidate <isolated.json> --output <comparison.json>
python scripts/metasurface_report.py --study-dir <study_dir> --output <study_report.json>
```

For fixed-frequency angular phase, invoke the runtime tool
`export-farfield-complex-cut`, then evaluate its JSON as `farfield`. The export
contains both complex `E_theta` and `E_phi`, run identity, reference plane, and
an internal Re/Im versus Abs/Phase consistency check.

For a state campaign plan or isolated runtime campaign:

```powershell
python scripts/run_metasurface_study.py plan --study <metasurface_study.json> --output <study_dir>
python scripts/run_metasurface_study.py run --study <metasurface_study.json> --output <study_dir> --execute
```

The `native_sweep` backend emits a validated MCP request for
`cst_sweep_preview_tool`/`cst_sweep_run_tool`; it does not silently
downgrade to an untracked loop. The active MCP adapter must report
`StoreDoubleParameter outside history + modeler.Rebuild` with
`explicit_rebuild=true`. If it reports the legacy `StoreParameters`
method, reject that sweep as stale and use the isolated backend or a
directly audited copied-project run until the service reloads.
The `isolated` backend creates one project copy per state and invokes
confirmed runtime commands with args files.

Every native or isolated candidate also needs a geometry fingerprint
(for example, a parameterized solid bounding box) before solving;
parameter labels alone are insufficient evidence.

After `cst_sweep_run_tool` completes, pass its `sweep_manifest.json` with
`--native-manifest` to archive parameter-matched exports under the study
state directories and run the same identity/evaluation/report checks. Without
a manifest, native execution remains an explicit plan-only MCP handoff.

## Optimization engine

For bounded discrete, mixed-variable, or multi-objective geometry searches, load the pymoo skill. Set optimization.engine to pymoo when the study needs an auditable mixed-variable candidate manifest. The adapter supports float, integer, binary, categorical/state, and bounded-sum constraints, records a deterministic seed, and writes optimization/pymoo-candidates.json. Candidates remain pending_cst_evaluation until CST produces parameter-matched exports; candidate generation alone is never reported as an electromagnetic optimum. Keep CST runtime as the execution, checkpoint, result-identity, and audit layer. Do not start a long pymoo campaign until the input-match preflight, geometry fingerprint, smoke solve, and mesh policy are valid. If pymoo is unavailable in the selected runtime, stop with blocked_dependency and install the pinned dependency before solving.

## Default acceptance policy

- Antenna matching: largest continuous band with `S11 <= -10 dB` unless the
  task supplies another threshold.
- One-bit phase: 180 degrees target, default tolerance 10 degrees.
- Two-bit phase: 90 degrees adjacent target, default tolerance 10 degrees.
- State amplitude spread: default 1.5 dB maximum.
- Far-field phase: compare the selected complex polarization component only
  where magnitude is above the configured floor; report angular ripple rather
  than treating phase at a field null as meaningful.
- Gain: use realized gain, gain, or directivity. If no absolute target exists,
  compare against the baseline and mark the result as lacking an absolute
  acceptance threshold.

Device values are never invented. A missing RLC, SPICE model, bias curve, or
parasitic value sets `execution_allowed=false`, blocks an expensive solve, and
produces `device-model-completion.json` with null-valued fields to complete.

When evaluation returns `refinement_requests`, use the configured
`spike_refinement` loop. Require an explicit mesh operation and the exact solved
candidate project, run the generated recipe dry first, and block optimization
until the local frequency and mesh rerun removes the numerical spike or the
bounded pass budget records it as a possible physical resonance.


For CST Floquet projects, pass the native `SZmax(i),Zmax(j)` tree path when that is what the result tree exposes; the configured runtime supports it alongside standard `S1,1` paths. For CST `farfield_grid` exports, the evaluator derives the selected fixed-phi theta cut for gain metrics and does not infer complex phase from a scalar gain grid.

When the study contains a standalone feed or bias network, evaluate its network payload separately and keep its metrics in `feed_network`, `bias_network`, or an integration-level metrics file. Do not let a unit-cell codebook score hide feed loss, branch imbalance, RF leakage, missing DC continuity, or choke/via issues.

For radiation_antenna, the runner applies an optimization preflight gate.
A recognizable driven-feed input S11 path is mandatory; periodic Floquet
Zmax/Zmin reflection is not an antenna match. Unresolved antenna evidence
sets optimization_allowed=false, records blocked_preflight, and prevents
the optimization runtime from starting.
