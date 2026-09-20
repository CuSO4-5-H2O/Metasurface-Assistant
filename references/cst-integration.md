# CST Integration

The workflow layer is `cst-simulation-workflow`; the coordination layer is
`cst-ai-optimization-toolkit`; the execution layers are native CST MCP and
the runtime CLI.

Use native MCP tools for live project control and Design Studio operations:
`cst_detect_tool`, `cst_project_info_tool`, `cst_list_results_tool`,
`cst_read_1d_result_tool`, `cst_add_diode_spice_model_tool`,
`cst_run_circuit_cosimulation_tool`, `cst_sweep_preview_tool`, and
`cst_sweep_run_tool`.

Use the exact MCP tool `run-metasurface-study` for the unified study pipeline.
It delegates to this skill's `run_metasurface_study.py`, defaults to plan-only,
and requires both `command="run"` and `execute=true` before execution. The MCP
adapter must remain thin so CLI and MCP share identical preflight, result
identity, recovery, evaluation, and report behavior.

The native sweep adapter must write each candidate with `StoreDoubleParameter` outside `AddToHistory`, then call `model3d.Rebuild()` through the Python modeler API before saving and solving. A parameter update embedded in a history rebuild can be silently rejected, leaving the old geometry under new parameter labels. Treat a sweep as valid only when the per-case manifest is complete, the exported result identity matches the candidate parameters, and at least one geometry fingerprint (for example, a parameterized solid bounding box) changes as expected.

Use the workspace's bundled Python entry when `uv` is unavailable:

```powershell
.venv\Scripts\python.exe -m cst_runtime health-check --auto-fix false
.venv\Scripts\python.exe -m cst_runtime inspect-project --project-path <working.cst>
.venv\Scripts\python.exe -m cst_runtime prepare-experiment --args-file <args.json>
.venv\Scripts\python.exe -m cst_runtime run-experiment --args-file <args.json>
```

Every live candidate must use a run copy, save before solving, export requested
results, validate parameter identity, and close the project/session. Far-field
export is the final CST operation before closing the results session.

Use `export-farfield-complex-cut` for angular phase evidence. It exports linear
complex `E_theta` and `E_phi` from `FarfieldCalculator.GetList` using the
`Re`, `Im`, `Abs`, and `Phase` channels, verifies their numerical consistency,
and records the fixed phi, frequency, run ID, polarization basis, and reference
plane. Never call `GetListItem` in this workflow.

For far-field recovery, create the runtime-owned run copy before the solve and
keep the same CST session identity through monitor export. A solved project
that was created only by a late SaveAs may reopen without the active result
session, so inspect-farfield-monitors or export-farfield-grid can report a
GUI-open failure even though an .ffm file exists. Record the runtime project
path, Design Environment identity, monitor name, selected field component,
frequency, and export path in the study manifest. If the session is lost,
reopen the runtime-owned copy, verify the monitor and result-tree identity,
then re-export; do not infer complex far-field phase from a scalar gain grid or
from an orphan .ffm file.

The CST MCP bridge resolves the canonical runtime skill from the configured
runtime root when its vendor package contains schemas but no `scripts/` folder.
The resolved path and candidates are included in `detect_runtime` output.

## Session preflight

A passing Python import check is not proof that CST can run unattended. Before
an expensive solve, launch or connect to a Design Environment and verify that
its PID appears in `running_design_environments()`. If the executable remains
alive but no endpoint becomes connectable, classify the run as
`blocked_session_startup`; preserve the study state and do not submit another
solver job.

Interactive license selection, missing licensed features, and invalid license
configuration are terminal preflight failures for unattended execution. Do not
automate around a license dialog or alter license files. Resume only after an
authorized license is configured and the same connectability probe passes.

## Failure classification

- "No DEs found to connect to": the native MCP execution layer is not
  connected. Launch/connect a Design Environment, or use the isolated runtime
  path; do not count the case as a solver result.
- "The rebuild operation cannot be used inside a structure macro": do not put
  `Rebuild` in an `AddToHistory` block. Apply parameters directly, call
  `model3d.Rebuild()` through the Python modeler API, and verify a geometry
  fingerprint before solving. Preserve the failed case and manifest if the
  fingerprint does not change; then use isolated `prepare-experiment` on a
  copied project or repair the parameterized history.
- A solver exit without a matching result export is incomplete. Require a
  result-tree item, export file, and parameter identity before evaluating the
  candidate.
- `gui_open_project_failed` during far-field inspection/export means the
  saved project could not restore an active CST result session. Preserve the
  project and log, retry from a runtime-owned copy created before solving, and
  verify the monitor/result tree in the same session. An existing `.ffm` file
  is not sufficient evidence for complex far-field phase.
Floquet projects may expose their complex traces as
`1D Results\S-Parameters\SZmax(1),Zmax(1)` rather than the standard `S1,1`
name. The configured CST runtime accepts both forms and preserves the Floquet
path in the exported JSON; the export filename includes the run ID. Do not
rename a Floquet trace to S11 unless the project port semantics justify that
mapping.
