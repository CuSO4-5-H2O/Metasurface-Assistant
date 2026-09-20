#!/usr/bin/env python3
"""Generate auditable mixed-variable candidates for CST evaluation with pymoo.

This module deliberately keeps electromagnetic evaluation outside pymoo. CST or
another trusted evaluator supplies measured objectives after each candidate is
solved. The generator can therefore be tested without opening CST and cannot
mistake a synthetic objective for a full-wave result.
"""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from typing import Any


class PymooUnavailable(RuntimeError):
    """Raised when the configured Python environment has no pymoo package."""


def pymoo_status() -> dict[str, Any]:
    """Return dependency status without importing pymoo at module import time."""

    try:
        pymoo = importlib.import_module("pymoo")
        importlib.import_module("numpy")
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"available": True, "version": getattr(pymoo, "__version__", None)}


def _require_pymoo() -> tuple[Any, Any, Any, Any, Any]:
    status = pymoo_status()
    if not status["available"]:
        raise PymooUnavailable(
            "pymoo is unavailable in the selected runtime; install the pinned "
            "pymoo dependency before starting a CST optimization campaign "
            f"({status.get('reason', 'unknown reason')})"
        )
    try:
        numpy = importlib.import_module("numpy")
        problem_module = importlib.import_module("pymoo.core.problem")
        sampling_module = importlib.import_module("pymoo.operators.sampling.rnd")
        return (
            numpy,
            problem_module.Problem,
            sampling_module.FloatRandomSampling,
            sampling_module.IntegerRandomSampling,
            sampling_module.BinaryRandomSampling,
        )
    except Exception as exc:
        raise PymooUnavailable(f"pymoo API is incompatible: {type(exc).__name__}: {exc}") from exc


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _array_state_variables(request: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raw = request.get("array_state", request.get("state_matrix"))
    if raw is None:
        return [], None
    if not isinstance(raw, dict):
        raise ValueError("array_state must be an object")
    try:
        rows = int(raw["rows"])
        cols = int(raw["cols"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("array_state requires integer rows and cols") from exc
    if rows < 1 or cols < 1:
        raise ValueError("array_state rows and cols must be positive")
    values = raw.get("values", raw.get("states"))
    if not isinstance(values, list) or not values:
        raise ValueError("array_state requires a non-empty values or states list")
    variables = [
        {
            "name": f"state[{row},{col}]",
            "type": "categorical",
            "values": list(values),
        }
        for row in range(rows)
        for col in range(cols)
    ]
    return variables, {"rows": rows, "cols": cols, "values": list(values)}


def _normalise_variables(request: dict[str, Any]) -> list[dict[str, Any]]:
    raw = request.get("variables", request.get("parameters", {}))
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = [
            {"name": name, **(spec if isinstance(spec, dict) else {})}
            for name, spec in raw.items()
        ]
    else:
        raise ValueError("pymoo parameters or variables must be a mapping or list")
    array_variables, _ = _array_state_variables(request)
    entries.extend(array_variables)
    variables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_spec in enumerate(entries):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"pymoo variable {index} must be an object")
        name = str(raw_spec.get("name", ""))
        if not name or name in seen:
            raise ValueError(f"pymoo variable {index} has a missing or duplicate name")
        kind = str(raw_spec.get("type", raw_spec.get("kind", "float"))).lower()
        kind = {
            "real": "float",
            "integer": "int",
            "choice": "categorical",
            "state": "categorical",
        }.get(kind, kind)
        if kind in {"float", "int"}:
            lower = _number(raw_spec.get("min"), f"{name}.min")
            upper = _number(raw_spec.get("max"), f"{name}.max")
            if lower >= upper:
                raise ValueError(f"{name}.min must be less than {name}.max")
            if kind == "int":
                if int(lower) != lower or int(upper) != upper:
                    raise ValueError(f"{name} integer bounds must be integral")
                lower, upper = int(lower), int(upper)
        elif kind == "binary":
            lower, upper = 0, 1
        elif kind == "categorical":
            values = raw_spec.get("values", raw_spec.get("allowed"))
            if not isinstance(values, list) or not values:
                raise ValueError(f"{name}.values must be a non-empty list for a categorical variable")
            if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
                raise ValueError(f"{name}.values must be unique")
            raw_spec = dict(raw_spec)
            raw_spec["values"] = values
            lower, upper = 0, len(values) - 1
        else:
            raise ValueError(f"{name}.type must be float, int, binary, or categorical")
        item = {"name": name, "type": kind, "min": lower, "max": upper}
        if kind == "categorical":
            item["values"] = raw_spec["values"]
        variables.append(item)
        seen.add(name)
    if not variables:
        raise ValueError("pymoo needs at least one variable")
    return variables


def _normalise_constraints(request: dict[str, Any]) -> list[dict[str, Any]]:
    raw = request.get("constraints", [])
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("pymoo constraints must be a list")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"pymoo constraint {index} must be an object")
        if "parameter" in item and ("min" in item or "max" in item):
            if "min" in item and "max" in item and item["min"] > item["max"]:
                raise ValueError(f"pymoo constraint {index} min must not exceed max")
            result.append(dict(item))
            continue
        if "sum" in item and isinstance(item["sum"], list) and ("min" in item or "max" in item):
            result.append(dict(item))
            continue
        raise ValueError(f"pymoo constraint {index} must define parameter bounds or a bounded sum")
    return result


def _constraint_violation(candidate: dict[str, Any], constraints: list[dict[str, Any]]) -> float:
    violation = 0.0
    for item in constraints:
        if "parameter" in item:
            value = candidate[str(item["parameter"])]
            if "min" in item:
                violation += max(0.0, float(item["min"]) - float(value))
            if "max" in item:
                violation += max(0.0, float(value) - float(item["max"]))
            continue
        total = sum(
            float(candidate[str(term["parameter"])]) * float(term.get("coefficient", 1.0))
            for term in item["sum"]
        )
        if "min" in item:
            violation += max(0.0, float(item["min"]) - total)
        if "max" in item:
            violation += max(0.0, total - float(item["max"]))
    return violation


def _decode_row(row: Any, variables: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for index, variable in enumerate(variables):
        raw = float(row[index])
        if variable["type"] == "float":
            value: Any = raw
        elif variable["type"] in {"int", "binary", "categorical"}:
            value = int(round(raw))
            value = max(int(variable["min"]), min(int(variable["max"]), value))
            if variable["type"] == "categorical":
                value = variable["values"][value]
        else:
            value = raw
        values[variable["name"]] = value
    return values


def _sample_column(
    numpy: Any,
    Problem: Any,
    sampler_class: Any,
    variable: dict[str, Any],
    count: int,
    seed: int,
) -> list[Any]:
    # Sampling one column at a time keeps categorical and integer legality
    # explicit even when a study combines them with continuous dimensions.
    numpy.random.seed(seed)
    problem = Problem(
        n_var=1,
        n_obj=1,
        xl=numpy.array([variable["min"]]),
        xu=numpy.array([variable["max"]]),
    )
    population = sampler_class().do(problem, n_samples=count)
    return numpy.asarray(population.get("X")).reshape(-1).tolist()


def generate_candidates(request: dict[str, Any]) -> dict[str, Any]:
    """Generate a legal, deterministic candidate manifest using pymoo samplers."""

    numpy, Problem, FloatRandomSampling, IntegerRandomSampling, BinaryRandomSampling = _require_pymoo()
    variables = _normalise_variables(request)
    constraints = _normalise_constraints(request)
    seed = int(request.get("seed", 1))
    count = int(request.get("candidate_count", request.get("population_size", 16)))
    if count < 1:
        raise ValueError("candidate_count must be positive")
    max_attempts = int(request.get("max_attempts", max(100, count * 20)))
    if max_attempts < count:
        raise ValueError("max_attempts must be at least candidate_count")
    samplers = {
        "float": FloatRandomSampling,
        "int": IntegerRandomSampling,
        "binary": BinaryRandomSampling,
        "categorical": IntegerRandomSampling,
    }
    columns = [
        _sample_column(
            numpy,
            Problem,
            samplers[variable["type"]],
            variable,
            max_attempts,
            seed + index,
        )
        for index, variable in enumerate(variables)
    ]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_index in range(max_attempts):
        candidate = _decode_row(
            [column[row_index] for column in columns],
            variables,
        )
        violation = _constraint_violation(candidate, constraints)
        if violation > 0.0:
            continue
        fingerprint = json.dumps(candidate, sort_keys=True, ensure_ascii=True)
        if bool(request.get("eliminate_duplicates", True)) and fingerprint in seen:
            continue
        seen.add(fingerprint)
        row_result = {
            "candidate_id": f"candidate_{len(candidates) + 1:03d}",
            "parameters": candidate,
            "constraint_violation": 0.0,
            "status": "pending_cst_evaluation",
        }
        array_spec = request.get("array_state", request.get("state_matrix"))
        if isinstance(array_spec, dict):
            rows = int(array_spec["rows"])
            cols = int(array_spec["cols"])
            row_result["state_matrix"] = [
                [candidate[f"state[{row},{col}]"] for col in range(cols)]
                for row in range(rows)
            ]
        candidates.append(row_result)
        if len(candidates) >= count:
            break
    result_status = (
        "candidates_ready"
        if len(candidates) == count
        else "insufficient_feasible_candidates"
    )
    return {
        "status": result_status,
        "engine": "pymoo",
        "strategy": "pymoo_mixed_variable_sampling",
        "pymoo": pymoo_status(),
        "seed": seed,
        "requested_candidate_count": count,
        "candidate_count": len(candidates),
        "variables": variables,
        "array_state": request.get("array_state", request.get("state_matrix")),
        "constraints": constraints,
        "candidates": candidates,
        "evaluation": {
            "status": "pending",
            "executor": "CST MCP or cst-runtime",
            "requires_parameter_matched_exports": True,
        },
    }


def write_candidate_manifest(request: dict[str, Any], output: Path) -> dict[str, Any]:
    result = generate_candidates(request)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
