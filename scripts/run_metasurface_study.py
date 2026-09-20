#!/usr/bin/env python3
"""Plan and run bounded, auditable multi-state CST metasurface studies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from metasurface_array import quantize_phase_grid
from metasurface_metrics import evaluate_study_directory
from pymoo_candidate_engine import PymooUnavailable, write_candidate_manifest


PROFILES = {
    "radiation_antenna",
    "reflective_programmable",
    "transmissive_programmable",
    "absorber",
}
BACKENDS = {"auto", "isolated", "native_sweep"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("study must be a JSON object")
    return value


def _require_e_drive(path: Path, field: str) -> None:
    if os.name == "nt" and path.drive.upper() != "E:":
        raise ValueError(f"{field} must be on E: for CST production artifacts: {path}")


def _validate_frequency_spec(study: dict[str, Any]) -> None:
    spec = study.get("frequency_spec")
    if not isinstance(spec, dict):
        raise ValueError("frequency_spec must be an object")
    for key in ("start", "stop"):
        value = spec.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"frequency_spec.{key} must be numeric")
        if value <= 0:
            raise ValueError(f"frequency_spec.{key} must be positive")
    if spec["start"] >= spec["stop"]:
        raise ValueError("frequency_spec.start must be less than stop")
    target = spec.get("target")
    if target is not None and not spec["start"] <= target <= spec["stop"]:
        raise ValueError("frequency_spec.target must be inside start/stop")


def _validate_spike_refinement(study: dict[str, Any]) -> None:
    config = study.get("spike_refinement")
    if config is None:
        return
    if not isinstance(config, dict):
        raise ValueError("spike_refinement must be an object")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("spike_refinement.enabled must be boolean")
    max_passes = config.get("max_passes", 1)
    if isinstance(max_passes, bool) or not isinstance(max_passes, int) or max_passes <= 0:
        raise ValueError("spike_refinement.max_passes must be a positive integer")
    operations = config.get("mesh_operations", [])
    if not isinstance(operations, list):
        raise ValueError("spike_refinement.mesh_operations must be a list")
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or not str(operation.get("tool", "")).strip():
            raise ValueError(f"spike_refinement.mesh_operations[{index}] needs a tool")
        if not isinstance(operation.get("args", {}), dict):
            raise ValueError(f"spike_refinement.mesh_operations[{index}].args must be an object")
    if enabled and not any("mesh" in str(item.get("tool", "")).lower() for item in operations):
        raise ValueError("enabled spike_refinement requires an explicit mesh operation")


def _validate_device_model(model: Any, label: str) -> None:
    if model is None:
        return
    if not isinstance(model, dict):
        raise ValueError(f"{label} must be an object")
    kind = str(model.get("kind", model.get("type", ""))).lower()
    if kind in {"rlc", "rlc_series", "series_rlc"}:
        for name in ("R", "L", "C"):
            if name not in model:
                continue
            value = model[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{label}.{name} must be a non-negative number")
        return
    if kind in {"spice", "spice_netlist", "nonlinear"}:
        source = model.get("source_path", model.get("source"))
        if source:
            source_path = Path(str(source))
            _require_e_drive(source_path, f"{label}.source_path")
        return
    raise ValueError(f"{label}.kind must be rlc_series or spice")


def _device_model_issues(model: Any, label: str) -> list[str]:
    """Return recoverable completeness issues after structural validation."""
    if model is None:
        return []
    _validate_device_model(model, label)
    kind = str(model.get("kind", model.get("type", ""))).lower()
    issues: list[str] = []
    if kind in {"rlc", "rlc_series", "series_rlc"}:
        missing = [name for name in ("R", "L", "C") if name not in model]
        if missing:
            issues.append(f"{label} RLC model missing: {', '.join(missing)}")
    else:
        source_keys = ("source", "source_path", "netlist", "inline_netlist")
        if not any(model.get(key) for key in source_keys):
            issues.append(f"{label} SPICE model needs source_path, netlist, or inline_netlist")
        source = model.get("source_path", model.get("source"))
        if source and not Path(str(source)).exists():
            issues.append(f"{label} source path does not exist: {source}")
    return issues


def _has_device_evidence(study: dict[str, Any], models: list[dict[str, Any]]) -> bool:
    evidence = study.get("device_evidence")
    if isinstance(evidence, list) and evidence:
        return True
    if isinstance(evidence, dict) and evidence:
        return True
    evidence_fields = ("evidence_source", "datasheet", "doi", "citation", "reference")
    return bool(models) and all(any(model.get(field) for field in evidence_fields) for model in models)


def _device_model_preflight(study: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
    declared_models: list[tuple[str, dict[str, Any]]] = []
    top_level = study.get("device_model")
    if isinstance(top_level, dict):
        declared_models.append(("device_model", top_level))
    for state in states:
        model = state.get("device_model", state.get("model"))
        if isinstance(model, dict):
            declared_models.append((f"state {state['id']}.device_model", model))

    if not declared_models:
        return {
            "name": "device_model_completeness",
            "status": "not_applicable",
            "issues": [],
            "required_for_solve": False,
        }

    issues: list[str] = []
    models = [model for _, model in declared_models]
    for label, model in declared_models:
        issues.extend(_device_model_issues(model, label))
    if not _has_device_evidence(study, models):
        issues.append(
            "device_evidence is missing; provide a user configuration, datasheet, or traceable paper source"
        )

    if len(states) > 1 and top_level is not None:
        for state in states:
            has_state_model = isinstance(state.get("device_model", state.get("model")), dict)
            has_state_mapping = bool(state.get("parameters")) or any(
                state.get(field) is not None for field in ("bias", "device_state", "spice_netlist")
            )
            if not has_state_model and not has_state_mapping:
                issues.append(
                    f"state {state['id']} has no parameter, bias, device_state, or state-specific model mapping"
                )

    return {
        "name": "device_model_completeness",
        "status": "blocked" if issues else "ready",
        "issues": issues,
        "required_for_solve": True,
    }


def _states(study: dict[str, Any]) -> list[dict[str, Any]]:
    value = study.get("state_codebook", [])
    if isinstance(value, dict):
        value = value.get("states", [])
    if not isinstance(value, list) or not value:
        raise ValueError("state_codebook must contain at least one state")
    states: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"state_codebook[{index}] must be an object")
        state = dict(raw)
        state_id = str(state.get("id", state.get("bits", index)))
        if state_id in seen:
            raise ValueError(f"duplicate state id: {state_id}")
        parameters = state.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(f"state {state_id} parameters must be an object")
        for name, parameter in parameters.items():
            if isinstance(parameter, bool) or not isinstance(parameter, (int, float)):
                raise ValueError(f"state {state_id} parameter {name} must be numeric")
        _validate_device_model(state.get("device_model", state.get("model")), f"state {state_id}.device_model")
        state["id"] = state_id
        state["parameters"] = parameters
        seen.add(state_id)
        states.append(state)
    return states


def _model_requires_isolated(model: Any) -> bool:
    if not isinstance(model, dict):
        return False
    kind = str(model.get("kind", model.get("type", ""))).lower()
    return kind in {"spice", "spice_netlist", "nonlinear"}

def _choose_backend(study: dict[str, Any], states: list[dict[str, Any]]) -> str:
    requested = study.get("state_backend", "auto")
    if requested not in BACKENDS:
        raise ValueError(f"state_backend must be one of {sorted(BACKENDS)}")
    if requested != "auto":
        return requested
    if _model_requires_isolated(study.get("device_model")):
        return "isolated"
    for state in states:
        if (
            state.get("topology_changes")
            or state.get("spice_netlist")
            or state.get("independent_project")
            or _model_requires_isolated(state.get("device_model", state.get("model")))
        ):
            return "isolated"
    return "native_sweep"


def _acceptance_thresholds(study: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
    evaluation = study.get("evaluation", {})
    declared_sources = evaluation.get("threshold_sources", study.get("threshold_sources", {}))
    if not isinstance(declared_sources, dict):
        raise ValueError("evaluation.threshold_sources must be an object")

    def entry(name: str, default: Any, *, applicable: bool = True) -> dict[str, Any]:
        explicitly_set = name in evaluation and evaluation.get(name) is not None
        value = evaluation.get(name, default) if applicable else None
        source = declared_sources.get(name)
        if source is None:
            if not applicable:
                source = "not_applicable"
            elif explicitly_set:
                source = "study_configuration"
            elif default is None:
                source = "not_provided"
            else:
                source = "built_in_conservative_default"
        return {"value": value, "source": source}

    phase_default = 360.0 / len(states) if len(states) > 1 else None
    thresholds = {
        "s11_threshold_db": entry(
            "s11_threshold_db",
            -10.0,
            applicable=study.get("profile") == "radiation_antenna",
        ),
        "phase_target_deg": entry("phase_target_deg", phase_default),
        "phase_tolerance_deg": entry("phase_tolerance_deg", 10.0),
        "amplitude_spread_db": entry("amplitude_spread_db", 1.5),
        "farfield_magnitude_floor_db": entry("farfield_magnitude_floor_db", -30.0),
        "gain_target_dbi": entry("gain_target_dbi", None),
    }
    if evaluation.get("phase_target_deg") is None and phase_default is not None:
        thresholds["phase_target_deg"]["source"] = "derived_from_state_count"
    return thresholds


def _is_input_match_treepath(path: Any) -> bool:
    normalized = str(path).lower().replace(" ", "")
    # Floquet Zmax/Zmin reflections are unit-cell responses, not a driven antenna input.
    if any(token in normalized for token in ("zmax(", "zmin(", "floquet")):
        return False
    return any(token in normalized for token in ("s11", "s1,1", "s(1,1)", "s[1,1]"))


def _preflight_gates(study: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    solve_blocking_reasons: list[str] = []
    optimization_blocking_reasons: list[str] = []
    device_gate = _device_model_preflight(study, states)
    gates.append(device_gate)
    if device_gate["status"] == "blocked":
        solve_blocking_reasons.extend(device_gate["issues"])
        optimization_blocking_reasons.extend(device_gate["issues"])
    if study.get("profile") == "radiation_antenna":
        paths = study.get("evaluation", {}).get("sparameter_treepaths", [])
        input_paths = [path for path in paths if _is_input_match_treepath(path)]
        declared = study.get("radiation_antenna_gate")
        declared_status = str(declared.get("status", "")).lower() if isinstance(declared, dict) else ""
        gate_status = "ready" if input_paths else "needs_validation"
        if not input_paths:
            optimization_blocking_reasons.append(
                "radiation_antenna requires a recognizable driven-feed input S11 tree path; "
                "Floquet Zmax/Zmin reflection does not satisfy this gate"
            )
        if declared_status and declared_status not in {"ready", "validated", "passed"}:
            gate_status = declared_status
            optimization_blocking_reasons.append(
                f"radiation_antenna_gate is {declared_status}; resolve the declared antenna evidence gate"
            )
        gates.append(
            {
                "name": "driven_feed_input_s11",
                "status": gate_status,
                "treepaths": input_paths,
                "required_for_optimization": True,
            }
        )
    blocking_reasons = list(dict.fromkeys(solve_blocking_reasons + optimization_blocking_reasons))
    return {
        "status": "blocked" if blocking_reasons else "ready",
        "execution_allowed": not solve_blocking_reasons,
        "optimization_allowed": not optimization_blocking_reasons,
        "gates": gates,
        "blocking_reasons": blocking_reasons,
        "solve_blocking_reasons": solve_blocking_reasons,
        "optimization_blocking_reasons": optimization_blocking_reasons,
    }


def _payload_has_frequency_samples(payload: dict[str, Any]) -> bool:
    for key in ("frequency", "xdata", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
    return False


def _post_smoke_gates(
    study: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    if study.get("profile") != "radiation_antenna":
        return {"status": "ready", "blocking_reasons": [], "checked_states": []}

    cases = {
        str(case.get("state_id")): case
        for case in plan.get("cases", [])
        if isinstance(case, dict)
    }
    missing: list[str] = []
    checked: list[str] = []
    input_paths = [
        path
        for path in study.get("evaluation", {}).get("sparameter_treepaths", [])
        if _is_input_match_treepath(path)
    ]
    for item in results:
        state_id = str(item.get("state_id"))
        checked.append(state_id)
        if item.get("status") != "validated":
            missing.append(f"{state_id}: smoke run did not validate")
            continue
        case = cases.get(state_id, {})
        run = item.get("run", {})
        selected = run.get("selected_exports", []) if isinstance(run, dict) else []
        found = False
        for relative in selected if isinstance(selected, list) else []:
            export_path = Path(str(relative))
            if not export_path.is_absolute():
                export_path = Path(case.get("state_dir", "")) / export_path
            try:
                raw = json.loads(export_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            treepath = raw.get("treepath", raw.get("tree_path", ""))
            if (
                _is_input_match_treepath(treepath)
                and (not input_paths or str(treepath) in {str(path) for path in input_paths})
                and _payload_has_frequency_samples(raw)
            ):
                found = True
                break
        if not found:
            missing.append(f"{state_id}: no non-empty driven-feed input S11 export was selected")
    return {
        "status": "blocked" if missing else "ready",
        "blocking_reasons": missing,
        "checked_states": checked,
        "input_s11_treepaths": input_paths,
    }


def validate_study(study: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if study.get("profile") not in PROFILES:
        raise ValueError(f"profile must be one of {sorted(PROFILES)}")
    source = Path(study.get("source_project", ""))
    output = Path(study.get("output_dir", ""))
    if source.suffix.lower() != ".cst":
        raise ValueError("source_project must be a concrete .cst path")
    if output == Path(""):
        raise ValueError("output_dir is required")
    _require_e_drive(source, "source_project")
    _require_e_drive(output, "output_dir")
    if not source.exists():
        raise FileNotFoundError(f"source_project does not exist: {source}")
    _validate_frequency_spec(study)
    _validate_spike_refinement(study)
    _validate_device_model(study.get("device_model"), "device_model")
    states = _states(study)
    evaluation = study.get("evaluation", {})
    if not isinstance(evaluation, dict):
        raise ValueError("evaluation must be an object")
    treepaths = evaluation.get("sparameter_treepaths", [])
    if not isinstance(treepaths, list) or not treepaths or not all(isinstance(item, str) and item for item in treepaths):
        raise ValueError("evaluation.sparameter_treepaths must be a non-empty string list")
    return states, _choose_backend(study, states)


def _native_sweep_request(study: dict[str, Any], cases: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    evaluation = study.get("evaluation", {})
    return {
        "project_path": str(Path(study["source_project"])),
        "parameters": {
            name: sorted({case["parameters"][name] for case in cases if name in case["parameters"]})
            for name in sorted({name for case in cases for name in case["parameters"]})
        },
        "mode": "cartesian",
        "max_cases": study.get("budget", {}).get("max_cases", len(cases)),
        "output_dir": str(output / "native_sweep"),
        "run_solver": True,
        "export_touchstone": True,
        "result_tree_paths": evaluation.get("sparameter_treepaths", []),
        "close_after_case": True,
        "continue_on_error": False,
        "overwrite": False,
    }

def build_plan(study: dict[str, Any], output: Path) -> dict[str, Any]:
    states, backend = validate_study(study)
    source = Path(study["source_project"])
    solver = dict(study.get("solver", {}))
    evaluation = dict(study.get("evaluation", {}))
    cases: list[dict[str, Any]] = []
    for state in states:
        state_dir = output / "states" / state["id"]
        project = state_dir / "projects" / "working.cst"
        cases.append(
            {
                "state_id": state["id"],
                "bits": state.get("bits"),
                "parameters": state["parameters"],
                "device_model": state.get("device_model", state.get("model", study.get("device_model"))),
                "project_path": str(project),
                "state_dir": str(state_dir),
                "prepare_args": {
                    "project_path": str(project),
                    "param_name": next(iter(state["parameters"]), ""),
                    "param_value": next(iter(state["parameters"].values()), 0.0),
                    "names": list(state["parameters"]),
                    "values": list(state["parameters"].values()),
                },
                "run_args": {
                    "project_path": str(project),
                    "sparameter_treepaths": evaluation["sparameter_treepaths"],
                    "farfield_names": solver.get("farfield_names", []),
                    "farfield_plot_mode": solver.get("farfield_plot_mode", "Realized Gain"),
                    "farfield_theta_step": solver.get("farfield_theta_step", 2.0),
                    "farfield_phi_step": solver.get("farfield_phi_step", 2.0),
                    "timeout_seconds": solver.get("timeout_seconds", 3600),
                },
            }
        )
    plan: dict[str, Any] = {
        "status": "plan_only",
        "profile": study["profile"],
        "source_project": str(source),
        "output_dir": str(output),
        "frequency_spec": study["frequency_spec"],
        "backend": backend,
        "state_count": len(cases),
        "cases": cases,
        "native_sweep_request": _native_sweep_request(study, cases, output),
        "evaluation": evaluation,
        "device_model": study.get("device_model"),
        "budget": study.get("budget", {}),
        "optimization": study.get("optimization", {}),
        "acceptance_thresholds": _acceptance_thresholds(study, states),
    }
    for field in (
        "classification",
        "radiation_antenna_gate",
        "remaining_validation",
        "device_evidence",
        "feed_network",
        "bias_network",
        "spike_refinement",
    ):
        if study.get(field) is not None:
            plan[field] = study[field]
    if study.get("array") is not None:
        plan["array_code_map"] = quantize_phase_grid(study)
    if study.get("integration_levels") is not None:
        plan["integration_levels"] = study["integration_levels"]
    plan["preflight_gates"] = _preflight_gates(study, states)
    plan["execution_allowed"] = plan["preflight_gates"]["execution_allowed"]
    plan["optimization_allowed"] = plan["preflight_gates"]["optimization_allowed"]
    mesh_request = _mesh_convergence_request(study, plan)
    if mesh_request is not None:
        plan["mesh_convergence_request"] = mesh_request
    optimization_request = _optimization_request(study, plan)
    if optimization_request is not None:
        plan["optimization_request"] = optimization_request
    return plan


def _optimization_request(study: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    optimization = study.get("optimization")
    if optimization in (None, {}, False):
        return None
    if not isinstance(optimization, dict):
        raise ValueError("optimization must be an object")
    parameters = optimization.get("parameters", study.get("editable_parameters", {}))
    if isinstance(parameters, list):
        parameter_items = []
        for index, item in enumerate(parameters):
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError(f"optimization.parameters[{index}] must contain a name")
            parameter_items.append((str(item["name"]), item))
    elif isinstance(parameters, dict) and parameters:
        parameter_items = [(str(name), bounds) for name, bounds in parameters.items()]
    else:
        raise ValueError("optimization.parameters must contain bounded parameters")
    parameter_specs: dict[str, dict[str, Any]] = {}
    for name, bounds in parameter_items:
        if not isinstance(bounds, dict):
            raise ValueError(f"optimization parameter {name} must be an object")
        parameter_type = str(bounds.get("type", "float")).lower()
        if parameter_type not in {"float", "int", "binary", "categorical"}:
            raise ValueError(f"optimization parameter {name}.type must be float, int, binary, or categorical")
        if parameter_type in {"float", "int"}:
            lower = bounds.get("min", bounds.get("lower"))
            upper = bounds.get("max", bounds.get("upper"))
            if lower is None or upper is None:
                raise ValueError(f"optimization parameter {name} needs min/max or lower/upper")
            if lower >= upper:
                raise ValueError(f"optimization parameter {name} lower bound must be less than upper bound")
        elif parameter_type == "binary":
            lower, upper = 0, 1
        else:
            values = bounds.get("values", bounds.get("allowed"))
            if not isinstance(values, list) or not values:
                raise ValueError(f"optimization parameter {name}.values must be a non-empty list")
            lower, upper = 0, len(values) - 1
        parameter_specs[name] = {
            "type": parameter_type,
            "min": lower,
            "max": upper,
        }
        if parameter_type == "categorical":
            parameter_specs[name]["values"] = values
    storage = Path(optimization.get("study_storage", Path(plan["output_dir"]) / "studies" / "optimization.db"))
    _require_e_drive(storage, "optimization.study_storage")
    source_project = Path(optimization.get("project_path", plan["source_project"])).resolve()
    if not source_project.exists():
        raise FileNotFoundError(f"optimization.project_path does not exist: {source_project}")
    _require_e_drive(source_project, "optimization.project_path")
    objective = optimization.get("objective", {"type": "bandwidth", "below_db": -10.0})
    objective_type = str(objective.get("type", "")).lower() if isinstance(objective, dict) else ""
    direction = str(
        optimization.get(
            "direction",
            "maximize" if objective_type in {"bandwidth", "gain_max"} else "minimize",
        )
    )
    if direction not in {"minimize", "maximize"}:
        raise ValueError("optimization.direction must be minimize or maximize")
    runtime_project = Path(plan["output_dir"]) / "optimization" / "projects" / "working.cst"
    campaign = {
        "project_path": str(runtime_project),
        "study_storage": str(storage),
        "study_name": str(optimization.get("study_name", "metasurface_optimization")),
        "objective": objective,
        "max_trials": int(optimization.get("max_trials", plan.get("budget", {}).get("max_trials", 10))),
        "target_value": optimization.get("target_value"),
        "target_operator": optimization.get("target_operator", ">=" if direction == "maximize" else "<="),
        "no_improvement_limit": int(optimization.get("no_improvement_limit", 3)),
        "max_consecutive_failures": int(optimization.get("max_consecutive_failures", 2)),
        "sampler": optimization.get("sampler", "tpe"),
        "direction": direction,
    }
    if campaign["max_trials"] < 1:
        raise ValueError("optimization.max_trials must be positive")
    engine = str(optimization.get("engine", optimization.get("optimizer", "cst_runtime_optimization"))).lower()
    if engine not in {"cst_runtime_optimization", "pymoo", "pymoo_candidates"}:
        raise ValueError("optimization.engine must be cst_runtime_optimization or pymoo")
    request: dict[str, Any] = {
        "engine": engine,
        "parameters": parameters,
        "study_parameters": parameter_specs,
        "source_project": str(source_project),
        "campaign": campaign,
        "probe_required": len(parameters) >= 4,
    }
    if engine in {"pymoo", "pymoo_candidates"}:
        request["pymoo"] = {
            "variables": parameter_specs,
            "constraints": optimization.get("constraints", []),
            "candidate_count": int(optimization.get("candidate_count", optimization.get("population_size", campaign["max_trials"]))),
            "population_size": int(optimization.get("population_size", campaign["max_trials"])),
            "seed": int(optimization.get("seed", 1)),
            "max_attempts": int(optimization.get("max_attempts", max(100, campaign["max_trials"] * 20))),
            "eliminate_duplicates": bool(optimization.get("eliminate_duplicates", True)),
        }
        array_state = optimization.get("array_state", optimization.get("state_matrix"))
        if isinstance(array_state, dict):
            array_state = dict(array_state)
            if "values" not in array_state and "states" not in array_state:
                codebook = study.get("state_codebook", [])
                if isinstance(codebook, dict):
                    codebook = codebook.get("states", [])
                if isinstance(codebook, list):
                    array_state["values"] = [
                        str(state.get("id", state.get("bits", index)))
                        for index, state in enumerate(codebook)
                        if isinstance(state, dict)
                    ]
            request["pymoo"]["array_state"] = array_state
    if len(parameters) >= 4:
        request["probe"] = {
            "project_path": str(runtime_project),
            "parameters": parameters,
            "study_storage": campaign["study_storage"],
            "study_name": campaign["study_name"],
            "max_probes": int(optimization.get("max_probes", 12)),
            "include_center": bool(optimization.get("include_center", True)),
            "objective": objective,
        }
    if not plan.get("optimization_allowed", True):
        request["status"] = "blocked_preflight"
        request["blocked_by"] = plan.get("preflight_gates", {}).get("blocking_reasons", [])
    return request

def _mesh_convergence_request(study: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    spec = study.get("mesh_convergence")
    if spec in (None, {}, False):
        return None
    if not isinstance(spec, dict):
        raise ValueError("mesh_convergence must be an object")
    task_path = Path(spec.get("task_path", "")).resolve()
    if not task_path.is_dir():
        raise FileNotFoundError(f"mesh_convergence.task_path does not exist: {task_path}")
    _require_e_drive(task_path, "mesh_convergence.task_path")
    levels = spec.get("mesh_levels")
    if not isinstance(levels, list) or len(levels) < 2:
        raise ValueError("mesh_convergence.mesh_levels must contain at least two levels")
    required = ("steps_per_wave_near", "steps_per_wave_far", "steps_per_box_near", "steps_per_box_far")
    for index, level in enumerate(levels):
        if not isinstance(level, dict):
            raise ValueError(f"mesh_convergence.mesh_levels[{index}] must be an object")
        for name in required:
            value = level.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"mesh_convergence.mesh_levels[{index}].{name} must be positive")
    evaluation = study.get("evaluation", {})
    return {
        "task_path": str(task_path),
        "mesh_levels": levels,
        "objective": spec.get(
            "objective",
            {"type": "bandwidth", "below_db": evaluation.get("s11_threshold_db", -10.0)},
        ),
        "tolerance": float(spec.get("tolerance", 0.2)),
        "min_levels": int(spec.get("min_levels", 2)),
        "sparameter_treepaths": spec.get("sparameter_treepaths", evaluation.get("sparameter_treepaths", [])),
        "farfield_names": spec.get("farfield_names", study.get("solver", {}).get("farfield_names", [])),
        "timeout_seconds": int(spec.get("timeout_seconds", study.get("solver", {}).get("timeout_seconds", 3600))),
        "poll_interval_seconds": float(spec.get("poll_interval_seconds", 10.0)),
    }

def _runtime_python(study: dict[str, Any]) -> Path:
    candidates = []
    if study.get("runtime_python"):
        candidates.append(Path(study["runtime_python"]))
    if study.get("runtime_workspace"):
        candidates.append(Path(study["runtime_workspace"]) / ".venv" / "Scripts" / "python.exe")
    candidates.append(Path(r"E:\Codex\toolkits\CST-Optimization-Workspace\.venv\Scripts\python.exe"))
    candidates.append(Path(sys.executable))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("no usable CST runtime Python was found")


def _copy_project(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_companion = source.with_suffix("")
    destination_companion = destination.with_suffix("")
    if source_companion.is_dir():
        shutil.copytree(source_companion, destination_companion, dirs_exist_ok=True)


def _invoke(runtime_python: Path, workspace: Path, tool: str, args_path: Path) -> dict[str, Any]:
    command = [str(runtime_python), "-m", "cst_runtime", tool, "--args-file", str(args_path)]
    timeout_seconds = None
    try:
        raw_args = json.loads(args_path.read_text(encoding="utf-8"))
        if tool == "run-experiment" and raw_args.get("timeout_seconds") is not None:
            timeout_seconds = max(1.0, float(raw_args["timeout_seconds"])) + 30.0
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        raw_args = {}
    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "tool": tool,
            "timeout_seconds": timeout_seconds,
            "stdout": exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
        }
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = {"status": "error", "stdout": completed.stdout, "stderr": completed.stderr}
    parsed.setdefault("returncode", completed.returncode)
    return parsed


def _run_optimization(study: dict[str, Any], plan: dict[str, Any], output: Path) -> dict[str, Any]:
    request = plan.get("optimization_request")
    if not isinstance(request, dict):
        return {"status": "not_requested"}
    if not plan.get("optimization_allowed", True) or request.get("status") == "blocked_preflight":
        return {
            "status": "blocked_preflight",
            "reason": "optimization preflight gate is unresolved",
            "blocked_by": request.get("blocked_by", plan.get("preflight_gates", {}).get("blocking_reasons", [])),
        }
    if request.get("engine") in {"pymoo", "pymoo_candidates"}:
        optimization_dir = output / "optimization"
        optimization_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = optimization_dir / "pymoo-candidates.json"
        pymoo_request = dict(request.get("pymoo", {}))
        try:
            result = write_candidate_manifest(pymoo_request, manifest_path)
        except PymooUnavailable as exc:
            result = {
                "status": "blocked_dependency",
                "engine": "pymoo",
                "reason": str(exc),
                "manifest_path": str(manifest_path),
                "evaluation": {"status": "not_started"},
            }
        except (ValueError, TypeError) as exc:
            result = {
                "status": "failed_candidate_generation",
                "engine": "pymoo",
                "reason": str(exc),
                "manifest_path": str(manifest_path),
            }
        if not manifest_path.exists():
            manifest_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        result["manifest_path"] = str(manifest_path)
        result["cst_evaluation_required"] = True
        (optimization_dir / "optimization-summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    campaign = request["campaign"]
    optimization_dir = output / "optimization"
    optimization_dir.mkdir(parents=True, exist_ok=True)
    runtime_project = Path(campaign["project_path"])
    _copy_project(Path(request["source_project"]), runtime_project)
    runtime_python: Path | None = None
    workspace: Path | None = None

    def invoke(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        args_path = optimization_dir / f"{name}.json"
        args_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = _invoke(runtime_python, workspace, name, args_path)
        (optimization_dir / f"{name}-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    create_args = {
        "storage_path": campaign["study_storage"],
        "study_name": campaign["study_name"],
        "parameters": json.dumps(request["study_parameters"], ensure_ascii=False),
        "direction": campaign["direction"],
        "directions": [],
        "value_names": [],
        "constraints": [],
        "sampler": campaign["sampler"],
        "n_startup_trials": int(study.get("optimization", {}).get("n_startup_trials", min(10, campaign["max_trials"]))),
    }
    create = invoke("create-study", create_args)
    if create.get("status") != "success":
        return {
            "status": "failed_create_study",
            "runtime_python": str(runtime_python),
            "project_path": str(runtime_project),
            "create": create,
        }

    probe = None
    if request.get("probe_required"):
        probe_payload = dict(request["probe"])
        probe = invoke("run-probe-phase", probe_payload)
        if probe.get("status") not in {"success", "warning"}:
            return {
                "status": "failed_probe",
                "runtime_python": str(runtime_python),
                "project_path": str(runtime_project),
                "create": create,
                "probe": probe,
            }

    campaign_args = {
        key: value
        for key, value in campaign.items()
        if key in {
            "project_path",
            "study_storage",
            "study_name",
            "objective",
            "max_trials",
            "target_value",
            "target_operator",
            "no_improvement_limit",
            "max_consecutive_failures",
            "sampler",
        }
    }
    campaign_result = invoke("run-optimization-campaign", campaign_args)
    status = "validated" if campaign_result.get("status") == "success" else "partial"
    result = {
        "status": status,
        "runtime_python": str(runtime_python),
        "project_path": str(runtime_project),
        "create": create,
        "probe": probe,
        "campaign": campaign_result,
    }
    (optimization_dir / "optimization-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result

def _spike_refinement_plan(
    study: dict[str, Any],
    plan: dict[str, Any],
    evaluation: dict[str, Any],
    output: Path,
    pass_index: int = 1,
) -> dict[str, Any]:
    requests = evaluation.get("refinement_requests", [])
    if not isinstance(requests, list) or not requests:
        return {"status": "not_required", "pass": pass_index, "actions": []}
    config = study.get("spike_refinement", {})
    if not isinstance(config, dict):
        raise ValueError("spike_refinement must be an object")
    mesh_operations = config.get("mesh_operations", [])
    if not isinstance(mesh_operations, list) or not all(isinstance(item, dict) for item in mesh_operations):
        raise ValueError("spike_refinement.mesh_operations must be a list of operations")
    has_mesh_operation = any("mesh" in str(item.get("tool", "")).lower() for item in mesh_operations)
    blocking_reasons: list[str] = []
    if not config.get("enabled", False):
        blocking_reasons.append("spike_refinement.enabled must be true for automatic reruns")
    if not has_mesh_operation:
        blocking_reasons.append("spike_refinement.mesh_operations must include an explicit mesh operation")

    cases = {str(case.get("state_id")): case for case in plan.get("cases", []) if isinstance(case, dict)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for request in requests:
        if isinstance(request, dict) and request.get("state_id") is not None:
            grouped.setdefault(str(request["state_id"]), []).append(request)
    actions: list[dict[str, Any]] = []
    pass_root = output / "refinements" / f"pass_{pass_index:03d}"
    for state_id, state_requests in sorted(grouped.items()):
        case = cases.get(state_id)
        if case is None:
            blocking_reasons.append(f"refinement request references unknown state: {state_id}")
            continue
        source_project = Path(str(case.get("project_path", "")))
        if not source_project.is_file():
            blocking_reasons.append(
                f"state {state_id} has no solved project copy for isolated spike refinement: {source_project}"
            )
            continue
        starts = [float(item["frequency_start"]) for item in state_requests if item.get("frequency_start") is not None]
        stops = [float(item["frequency_stop"]) for item in state_requests if item.get("frequency_stop") is not None]
        if not starts or not stops:
            blocking_reasons.append(f"state {state_id} refinement request has no usable frequency window")
            continue
        state_dir = pass_root / "states" / state_id
        project_path = state_dir / "projects" / "working.cst"
        operations = [
            {
                "tool": "define-frequency-range",
                "args": {"start_freq": min(starts), "end_freq": max(stops)},
            },
            *mesh_operations,
        ]
        recipe_base = {
            "project_path": str(project_path),
            "operations": operations,
            "recipe_name": f"spike_refinement_pass_{pass_index:03d}_{state_id}",
            "run_solver": False,
            "objective": study.get("optimization", {}).get("objective", {"type": "s11_min_db"}),
        }
        run_args = dict(case.get("run_args", {}))
        run_args["project_path"] = str(project_path)
        run_args["sparameter_treepaths"] = sorted(
            {str(item.get("treepath")) for item in state_requests if item.get("treepath")}
        ) or run_args.get("sparameter_treepaths", [])
        actions.append(
            {
                "state_id": state_id,
                "source_project": str(source_project),
                "project_path": str(project_path),
                "state_dir": str(state_dir),
                "parameters": case.get("parameters", {}),
                "device_model": case.get("device_model"),
                "requests": state_requests,
                "dry_run_args": {**recipe_base, "dry_run": True},
                "apply_args": {**recipe_base, "dry_run": False},
                "run_args": run_args,
            }
        )
    return {
        "status": "blocked_configuration" if blocking_reasons else "ready",
        "pass": pass_index,
        "actions": actions,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
        "frequency_unit": plan.get("frequency_spec", {}).get("unit"),
        "preserve_raw_spike": True,
    }


def _run_spike_refinement(
    study: dict[str, Any],
    plan: dict[str, Any],
    initial_evaluation: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    config = study.get("spike_refinement", {})
    max_passes = int(config.get("max_passes", 1)) if isinstance(config, dict) else 1
    if max_passes <= 0:
        raise ValueError("spike_refinement.max_passes must be positive")
    runtime_python = _runtime_python(study)
    workspace = Path(study.get("runtime_workspace", output))
    current_evaluation = initial_evaluation
    current_plan = plan
    pass_results: list[dict[str, Any]] = []
    for pass_index in range(1, max_passes + 1):
        refinement_plan = _spike_refinement_plan(
            study, current_plan, current_evaluation, output, pass_index
        )
        plan_path = output / "refinements" / f"pass_{pass_index:03d}" / "spike-refinement-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(refinement_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        if refinement_plan["status"] == "not_required":
            return {
                "status": "validated" if pass_results else "not_required",
                "passes": pass_results,
                "remaining_requests": [],
            }
        if refinement_plan["status"] != "ready":
            return {
                "status": "blocked_configuration",
                "passes": pass_results,
                "blocked_by": refinement_plan.get("blocking_reasons", []),
                "plan_path": str(plan_path),
                "remaining_requests": current_evaluation.get("refinement_requests", []),
            }

        if runtime_python is None:
            runtime_python = _runtime_python(study)
            workspace = Path(study.get("runtime_workspace", output))

        refinement_cases: list[dict[str, Any]] = []
        action_results: list[dict[str, Any]] = []
        for action in refinement_plan["actions"]:
            source_project = Path(str(action["source_project"]))
            project_path = Path(action["project_path"])
            _copy_project(source_project, project_path)
            state_dir = Path(action["state_dir"])
            state_dir.mkdir(parents=True, exist_ok=True)
            dry_path = state_dir / "apply-design-recipe-dry.json"
            live_path = state_dir / "apply-design-recipe-live.json"
            run_path = state_dir / "run-experiment.json"
            dry_path.write_text(json.dumps(action["dry_run_args"], indent=2), encoding="utf-8")
            live_path.write_text(json.dumps(action["apply_args"], indent=2), encoding="utf-8")
            run_path.write_text(json.dumps(action["run_args"], indent=2), encoding="utf-8")
            assert workspace is not None
            dry_result = _invoke(runtime_python, workspace, "apply-design-recipe", dry_path)
            if dry_result.get("status") != "success":
                action_results.append({"state_id": action["state_id"], "status": "failed_dry_run", "result": dry_result})
                continue
            apply_result = _invoke(runtime_python, workspace, "apply-design-recipe", live_path)
            if apply_result.get("status") != "success":
                action_results.append({"state_id": action["state_id"], "status": "failed_apply", "result": apply_result})
                continue
            run_result = _invoke(runtime_python, workspace, "run-experiment", run_path)
            case = {
                "state_id": action["state_id"],
                "bits": None,
                "parameters": action["parameters"],
                "device_model": action.get("device_model"),
                "project_path": action["project_path"],
                "state_dir": action["state_dir"],
                "run_args": action["run_args"],
            }
            selected = (
                _record_selected_exports(case, run_result, action["parameters"])
                if run_result.get("status") == "success"
                else []
            )
            status = "validated" if run_result.get("status") == "success" and selected else "failed_run"
            action_results.append(
                {
                    "state_id": action["state_id"],
                    "status": status,
                    "dry_run": dry_result,
                    "apply": apply_result,
                    "run": run_result,
                }
            )
            refinement_cases.append(case)

        pass_root = plan_path.parent
        pass_plan = dict(plan)
        pass_plan["output_dir"] = str(pass_root)
        pass_plan["cases"] = refinement_cases
        pass_plan["spike_refinement_parent"] = str(output)
        (pass_root / "study_plan.json").write_text(
            json.dumps(pass_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        current_evaluation = evaluate_study_directory(pass_root, pass_root / "study_evaluation.json")
        pass_results.append(
            {
                "pass": pass_index,
                "plan_path": str(plan_path),
                "actions": action_results,
                "evaluation": current_evaluation,
            }
        )
        all_actions_valid = bool(action_results) and all(
            item.get("status") == "validated" for item in action_results
        )
        if not all_actions_valid:
            return {
                "status": "partial",
                "reason": "one or more spike-refinement actions failed",
                "passes": pass_results,
                "remaining_requests": current_evaluation.get("refinement_requests", []),
            }
        if not current_evaluation.get("refinement_requests"):
            return {"status": "validated", "passes": pass_results, "remaining_requests": []}
        current_plan = pass_plan
    return {
        "status": "partial",
        "reason": "spike persisted after the configured refinement budget; retain it as a possible physical resonance",
        "passes": pass_results,
        "remaining_requests": current_evaluation.get("refinement_requests", []),
    }


def _run_mesh_convergence(study: dict[str, Any], plan: dict[str, Any], output: Path) -> dict[str, Any]:
    request = plan.get("mesh_convergence_request")
    if not isinstance(request, dict):
        return {"status": "not_requested"}
    runtime_python = _runtime_python(study)
    workspace = Path(study.get("runtime_workspace", output))
    args_path = output / "mesh_convergence_args.json"
    result_path = output / "mesh_convergence_result.json"
    args_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _invoke(runtime_python, workspace, "run-mesh-convergence", args_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "validated" if result.get("status") == "success" and result.get("validated", True) is not False else "partial",
        "runtime_python": str(runtime_python),
        "args_path": str(args_path),
        "result_path": str(result_path),
        "result": result,
    }

def _write_plan_artifacts(plan: dict[str, Any]) -> None:
    device_gate = next(
        (
            gate
            for gate in plan.get("preflight_gates", {}).get("gates", [])
            if gate.get("name") == "device_model_completeness"
        ),
        None,
    )
    if device_gate and device_gate.get("status") == "blocked":
        completion = {
            "status": "pending_device_model",
            "issues": device_gate.get("issues", []),
            "instructions": [
                "Fill values only from the current user configuration, device datasheet, or traceable paper.",
                "Record units, reference plane, legal range, and the source for every state.",
                "Map every state to CST parameters, bias/device_state, or a state-specific model.",
            ],
            "rlc_template": {
                "kind": "rlc_series",
                "R": None,
                "L": None,
                "C": None,
                "units": {"R": "ohm", "L": "nH", "C": "pF"},
            },
            "spice_template": {
                "kind": "spice",
                "source_path_or_inline_netlist": None,
                "simulator_assumptions": None,
            },
            "device_evidence_template": [{"source": None, "location": None, "claim": None}],
            "state_mapping_template": [
                {
                    "id": case["state_id"],
                    "parameters_or_bias": None,
                    "device_model": case.get("device_model"),
                }
                for case in plan["cases"]
            ],
        }
        completion_path = Path(plan["output_dir"]) / "device-model-completion.json"
        completion_path.parent.mkdir(parents=True, exist_ok=True)
        completion_path.write_text(json.dumps(completion, indent=2), encoding="utf-8")
    for case in plan["cases"]:
        state_dir = Path(case["state_dir"])
        state_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "state_id": case["state_id"],
            "bits": case.get("bits"),
            "parameters": case["parameters"],
            "device_model": case.get("device_model"),
            "project_path": case["project_path"],
            "source_project": plan["source_project"],
            "backend": plan["backend"],
            "run_args": case["run_args"],
        }
        (state_dir / "state.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")




def _export_path(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        for key in ("path", "file", "export_path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _select_current_exports(
    run: dict[str, Any],
    state_dir: Path,
    treepaths: list[str],
    expected_parameters: dict[str, Any] | None = None,
) -> list[str]:
    """Select newest parameter-matched results while retaining raw history."""
    candidates: list[Path] = []
    for key in ("sparameter_exported", "farfield_exported"):
        values = run.get(key, [])
        if isinstance(values, (str, dict)):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            path_value = _export_path(value)
            if path_value:
                path = Path(path_value)
                if path.is_file() and path not in candidates:
                    candidates.append(path)
    explicit = _export_path(run.get("s11_export_path"))
    if explicit:
        path = Path(explicit)
        if path.is_file() and path not in candidates:
            candidates.append(path)

    def metadata(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def run_number(raw: dict[str, Any]) -> int:
        try:
            return int(raw.get("run_id", -1))
        except (TypeError, ValueError):
            return -1

    records = [(path, metadata(path)) for path in candidates]
    if expected_parameters:
        records = [
            (path, raw)
            for path, raw in records
            if isinstance(raw.get("parameter_combination"), dict)
            and _parameters_match(expected_parameters, raw["parameter_combination"])
        ]
    selected: list[Path] = []
    requested = {str(value) for value in treepaths}
    for treepath in requested:
        matches = [
            (path, raw)
            for path, raw in records
            if str(raw.get("treepath", raw.get("tree_path", ""))) == treepath
        ]
        if matches:
            selected.append(max(matches, key=lambda item: run_number(item[1]))[0])
    if not selected and explicit:
        explicit_path = Path(explicit)
        if any(path == explicit_path for path, _ in records):
            selected.append(explicit_path)
    if not selected:
        grouped: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
        for path, raw in records:
            treepath = str(raw.get("treepath", raw.get("tree_path", "")))
            quantity = str(raw.get("quantity", ""))
            key = (treepath, quantity)
            prior = grouped.get(key)
            if prior is None or run_number(raw) > run_number(prior[1]):
                grouped[key] = (path, raw)
        selected.extend(path for path, _ in grouped.values())

    unique: list[str] = []
    for path in selected:
        try:
            relative = path.resolve().relative_to(state_dir.resolve())
            value = relative.as_posix()
        except ValueError:
            value = str(path)
        if value not in unique:
            unique.append(value)
    return unique


def _record_selected_exports(
    case: dict[str, Any], run: dict[str, Any], expected_parameters: dict[str, Any]
) -> list[str]:
    state_dir = Path(case["state_dir"])
    manifest_path = state_dir / "state.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, json.JSONDecodeError):
            manifest = {}
    selected = _select_current_exports(
        run,
        state_dir,
        list(case.get("run_args", {}).get("sparameter_treepaths", [])),
        expected_parameters,
    )
    archived_selected: list[str] = []
    exports_dir = state_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    for value in selected:
        source_path = Path(value) if Path(value).is_absolute() else state_dir / value
        try:
            relative = source_path.resolve().relative_to(state_dir.resolve())
            archived_selected.append(relative.as_posix())
            continue
        except ValueError:
            pass
        destination = exports_dir / source_path.name
        if destination.exists() and destination.resolve() != source_path.resolve():
            run_id = run.get("run_id", "current")
            destination = exports_dir / f"{source_path.stem}_run{run_id}{source_path.suffix}"
        shutil.copy2(source_path, destination)
        archived_selected.append(destination.relative_to(state_dir).as_posix())
    selected = list(dict.fromkeys(archived_selected))
    # CST farfield exports may omit parameter metadata; attach the exact
    # candidate map to the archived copy before identity evaluation.
    for relative in selected:
        export_path = state_dir / relative
        try:
            raw = json.loads(export_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "parameter_combination" not in raw:
                raw["parameter_combination"] = dict(expected_parameters)
                raw["state_id"] = case.get("state_id")
                raw["selection_run_id"] = run.get("run_id")
                export_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    manifest["selected_exports"] = selected
    manifest["expected_parameter_combination"] = dict(expected_parameters)
    manifest["raw_export_count"] = len(list((state_dir / "exports").glob("*.json")))
    manifest["selection_run_id"] = run.get("run_id")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    run["selected_exports"] = selected
    return selected

def _parameters_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if set(expected) != set(actual):
        return False
    for name, expected_value in expected.items():
        actual_value = actual[name]
        try:
            if abs(float(expected_value) - float(actual_value)) > 1e-9 * max(
                1.0, abs(float(expected_value)), abs(float(actual_value))
            ):
                return False
        except (TypeError, ValueError):
            if expected_value != actual_value:
                return False
    return True


def ingest_native_manifest(
    study: dict[str, Any], plan: dict[str, Any], output: Path, manifest_path: Path
) -> dict[str, Any]:
    """Archive native MCP exports into the state-oriented report layout."""
    manifest = _load(manifest_path)
    expected_source = Path(plan["source_project"]).resolve()
    actual_source = Path(manifest.get("source_project", "")).resolve()
    if actual_source != expected_source:
        return {
            "status": "failed_identity",
            "backend": "native_sweep",
            "manifest": str(manifest_path),
            "expected_source_project": str(expected_source),
            "actual_source_project": str(actual_source),
        }

    state_specs = [case for case in plan.get("cases", []) if isinstance(case, dict)]
    archived: list[dict[str, Any]] = []
    used_state_ids: set[str] = set()
    for case in manifest.get("cases", []):
        if not isinstance(case, dict):
            continue
        parameters = case.get("parameters", {})
        matched = next(
            (
                spec
                for spec in state_specs
                if _parameters_match(spec.get("parameters", {}), parameters)
                and spec.get("state_id") not in used_state_ids
            ),
            None,
        )
        state_id = str(matched.get("state_id")) if matched else str(case.get("case_id", "unknown"))
        if state_id in used_state_ids:
            state_id = f"{state_id}_{case.get('case_id', 'case')}"
        used_state_ids.add(state_id)
        state_dir = output / "states" / state_id
        exports_dir = state_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        state_manifest = {
            "state_id": state_id,
            "parameters": parameters,
            "expected_parameter_combination": case.get("expected_parameter_combination", parameters),
            "backend": "native_sweep",
            "native_case_id": case.get("case_id"),
            "source_project": str(expected_source),
            "native_manifest": str(manifest_path),
            "project_path": case.get("project_path"),
        }
        (state_dir / "state.json").write_text(
            json.dumps(state_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        status = str(case.get("status", "error"))
        copied_exports = []
        errors = []
        solved_project = case.get("project_path")
        for export in case.get("result_exports", []):
            if not isinstance(export, dict) or not export.get("file"):
                errors.append("result export entry is missing file")
                continue
            source_export = Path(str(export["file"]))
            if not source_export.is_file():
                errors.append(f"missing result export: {source_export}")
                continue
            try:
                payload = _load(source_export)
                if not isinstance(payload, dict):
                    raise ValueError("result export is not a JSON object")
                if "parameter_combination" not in payload:
                    payload["parameter_combination"] = dict(case.get("expected_parameter_combination", parameters))
                if not solved_project and payload.get("case_project"):
                    solved_project = payload.get("case_project")
                payload["native_case_id"] = case.get("case_id")
                destination = exports_dir / source_export.name
                destination.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                copied_exports.append(str(destination))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{source_export}: {exc}")
        state_manifest["project_path"] = solved_project
        (state_dir / "state.json").write_text(
            json.dumps(state_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if status != "success" or errors or not copied_exports:
            failure = {
                "status": "native_case_failed" if status == "success" else status,
                "state_id": state_id,
                "native_case_id": case.get("case_id"),
                "parameters": parameters,
                "errors": errors,
                "native_case": case,
            }
            (state_dir / "failure.log").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            status = "error"
        archived.append(
            {
                "state_id": state_id,
                "native_case_id": case.get("case_id"),
                "status": "validated" if status == "success" and copied_exports else status,
                "parameters": parameters,
                "project_path": solved_project,
                "exports": copied_exports,
            }
        )
    overall = "validated" if archived and all(item["status"] == "validated" for item in archived) else "partial"
    return {
        "status": overall,
        "backend": "native_sweep",
        "manifest": str(manifest_path),
        "states": archived,
        "native_summary": {
            "completed_cases": manifest.get("completed_cases"),
            "failed_cases": manifest.get("failed_cases"),
        },
    }


def _parameter_values_from_prepare(prepare: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, entry in prepare.get("parameters", {}).items():
        if isinstance(entry, dict) and "value" in entry:
            values[str(name)] = entry["value"]
        elif not isinstance(entry, dict):
            values[str(name)] = entry
    if not values:
        raise RuntimeError("prepare-experiment did not return a complete parameter snapshot")
    return values


def run_isolated(study: dict[str, Any], plan: dict[str, Any], output: Path) -> dict[str, Any]:
    if not plan.get("execution_allowed", True):
        return {
            "status": "blocked_preflight",
            "backend": "isolated",
            "reason": "state solves were not started because the device model is incomplete or untraceable",
            "blocked_by": plan.get("preflight_gates", {}).get("solve_blocking_reasons", []),
            "states": [],
            "optimization": None,
            "mesh_convergence": None,
        }
    source = Path(plan["source_project"])
    runtime_python = _runtime_python(study)
    workspace = Path(study.get("runtime_workspace", output))
    results = []
    for case in plan["cases"]:
        case_dir = Path(case["state_dir"])
        case_dir.mkdir(parents=True, exist_ok=True)
        project = Path(case["project_path"])
        _copy_project(source, project)
        prepare_path = case_dir / "prepare-experiment.json"
        run_path = case_dir / "run-experiment.json"
        prepare_path.write_text(json.dumps(case["prepare_args"], indent=2), encoding="utf-8")
        run_path.write_text(json.dumps(case["run_args"], indent=2), encoding="utf-8")
        prepare = _invoke(runtime_python, workspace, "prepare-experiment", prepare_path)
        (case_dir / "prepare-result.json").write_text(json.dumps(prepare, indent=2), encoding="utf-8")
        if prepare.get("status") != "success":
            (case_dir / "failure.log").write_text(json.dumps(prepare, indent=2), encoding="utf-8")
            results.append({"state_id": case["state_id"], "status": "failed_prepare", "result": prepare})
            continue
        try:
            expected_parameters = _parameter_values_from_prepare(prepare)
        except RuntimeError as exc:
            failure = {"status": "error", "error_type": "missing_parameter_snapshot", "message": str(exc)}
            (case_dir / "failure.log").write_text(json.dumps(failure, indent=2), encoding="utf-8")
            results.append({"state_id": case["state_id"], "status": "failed_prepare", "result": failure})
            continue
        run = _invoke(runtime_python, workspace, "run-experiment", run_path)
        if run.get("status") == "success":
            selected = _record_selected_exports(case, run, expected_parameters)
            if not selected:
                run["status"] = "error"
                run["error_type"] = "no_parameter_matched_exports"
                run["message"] = "No exported result has the complete current parameter combination."
        (case_dir / "run-result.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        if run.get("status") != "success":
            (case_dir / "failure.log").write_text(json.dumps(run, indent=2), encoding="utf-8")
        results.append(
            {
                "state_id": case["state_id"],
                "status": "validated" if run.get("status") == "success" else "failed_run",
                "prepare": prepare,
                "run": run,
            }
        )
    initial_evaluation = None
    spike_refinement = {"status": "not_evaluated", "reason": "study plan artifact is unavailable"}
    if (output / "study_plan.json").exists():
        initial_evaluation = evaluate_study_directory(output, output / "study_evaluation.json")
        spike_refinement = _run_spike_refinement(study, plan, initial_evaluation, output)
    smoke_gates = _post_smoke_gates(study, plan, results)
    if plan.get("optimization_request"):
        if not plan.get("optimization_allowed", True):
            optimization = {
                "status": "blocked_preflight",
                "reason": "optimization was not started because a required preflight gate is unresolved",
                "blocked_by": plan.get("preflight_gates", {}).get("blocking_reasons", []),
            }
        elif smoke_gates["status"] != "ready":
            optimization = {
                "status": "blocked_smoke_gate",
                "reason": "optimization was not started because the smoke run did not produce a usable input S11 export",
                "blocked_by": smoke_gates["blocking_reasons"],
            }
        elif spike_refinement.get("status") not in {"validated", "not_required"}:
            optimization = {
                "status": "blocked_spike_refinement",
                "reason": "optimization was not started because a suspected spike has not passed local frequency and mesh recomputation",
                "blocked_by": spike_refinement.get("blocked_by", spike_refinement.get("remaining_requests", [])),
            }
        else:
            optimization = _run_optimization(study, plan, output)
    else:
        optimization = None
    mesh_convergence = _run_mesh_convergence(study, plan, output) if plan.get("mesh_convergence_request") else None
    all_states_valid = bool(results) and all(item["status"] == "validated" for item in results)
    optimization_valid = optimization is None or optimization.get("status") == "validated"
    mesh_valid = mesh_convergence is None or mesh_convergence.get("status") == "validated"
    refinement_valid = spike_refinement.get("status") in {"validated", "not_required", "not_evaluated"}
    return {
        "status": "validated" if all_states_valid and optimization_valid and mesh_valid and refinement_valid else "partial",
        "backend": "isolated",
        "runtime_python": str(runtime_python),
        "states": results,
        "initial_evaluation": initial_evaluation,
        "spike_refinement": spike_refinement,
        "smoke_gates": smoke_gates,
        "optimization": optimization,
        "mesh_convergence": mesh_convergence,
    }


def _build_report(study: dict[str, Any], output: Path) -> dict[str, Any]:
    runtime_python = _runtime_python(study)
    report_script = Path(__file__).with_name("metasurface_report.py")
    report_path = output / "study_report.json"
    completed = subprocess.run(
        [str(runtime_python), str(report_script), "--study-dir", str(output), "--output", str(report_path)],
        cwd=str(output),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {"status": "error", "stdout": completed.stdout, "stderr": completed.stderr}
    result["path"] = str(report_path)
    result["returncode"] = completed.returncode
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--study", required=True, type=Path)
        sub.add_argument("--output", required=True, type=Path)
        sub.add_argument("--execute", action="store_true")
        sub.add_argument("--native-manifest", type=Path, default=None, help="ingest a completed cst_sweep_run manifest")
    args = parser.parse_args()
    study = _load(args.study)
    plan = build_plan(study, args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    _write_plan_artifacts(plan)
    plan_path = args.output / "study_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.command == "plan" or not args.execute:
        print(json.dumps({"status": "plan_only", "plan": str(plan_path)}, ensure_ascii=False))
        return 0
    if not plan.get("execution_allowed", True):
        summary = {
            "status": "blocked_preflight",
            "reason": "state solves were not started because a required solve preflight gate is unresolved",
            "blocked_by": plan.get("preflight_gates", {}).get("solve_blocking_reasons", []),
            "plan": str(plan_path),
        }
    elif plan["backend"] == "native_sweep":
        if plan.get("optimization_request"):
            summary = {
                "status": "blocked_preflight",
                "reason": "native_sweep state execution does not run geometry optimization; use isolated backend",
                "optimization_request": plan["optimization_request"],
            }
        elif args.native_manifest is not None:
            summary = ingest_native_manifest(study, plan, args.output, args.native_manifest)
            if summary.get("status") in {"validated", "partial"}:
                initial_evaluation = evaluate_study_directory(
                    args.output,
                    args.output / "study_evaluation.json",
                )
                native_projects = {
                    str(item.get("state_id")): item.get("project_path")
                    for item in summary.get("states", [])
                    if isinstance(item, dict) and item.get("project_path")
                }
                refinement_plan = dict(plan)
                refinement_plan["cases"] = [
                    {
                        **case,
                        "project_path": native_projects.get(str(case.get("state_id")), case.get("project_path")),
                    }
                    for case in plan.get("cases", [])
                ]
                spike_refinement = _run_spike_refinement(
                    study,
                    refinement_plan,
                    initial_evaluation,
                    args.output,
                )
                summary["initial_evaluation"] = initial_evaluation
                summary["spike_refinement"] = spike_refinement
                if spike_refinement.get("status") not in {"validated", "not_required"}:
                    summary["status"] = "partial"
        else:
            summary = {
                "status": "plan_only",
                "reason": "native_sweep_requires_cst_mcp",
                "mcp_request": plan["native_sweep_request"],
            }
        summary["plan"] = str(plan_path)
    else:
        summary = run_isolated(study, plan, args.output)
        summary["plan"] = str(plan_path)
    summary_path = args.output / "study_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["report"] = _build_report(study, args.output)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] in {"validated", "partial", "plan_only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
