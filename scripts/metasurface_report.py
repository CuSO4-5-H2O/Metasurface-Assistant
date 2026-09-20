#!/usr/bin/env python3
"""Build a traceable JSON report for a metasurface study run."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from metasurface_metrics import evaluate_study_directory


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_map(level: dict[str, Any]) -> dict[str, float]:
    values = level.get("metrics", level)
    if not isinstance(values, dict):
        return {}
    return {
        str(name): float(value)
        for name, value in values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    }


def level_deltas(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: dict[str, float] | None = None
    previous_name: str | None = None
    for index, level in enumerate(levels):
        name = str(level.get("name", f"level_{index}"))
        metrics = _metric_map(level)
        delta = {}
        if previous is not None:
            delta = {key: metrics[key] - previous[key] for key in metrics.keys() & previous.keys()}
        result.append(
            {
                "name": name,
                "metrics": metrics,
                "relative_to": previous_name,
                "delta": delta,
                "metrics_file": level.get("metrics_file"),
            }
        )
        previous = metrics
        previous_name = name
    return result


def _candidate_row(path: Path) -> dict[str, Any]:
    payload = _load(path)
    objectives = payload.get("objectives", {}) if isinstance(payload, dict) else {}
    if not isinstance(objectives, dict):
        objectives = {}
    if isinstance(payload, dict) and "score" in payload and "score" not in objectives:
        objectives["score"] = payload["score"]
    return {
        "id": path.stem,
        "path": str(path),
        "objectives": objectives,
        "status": payload.get("status") if isinstance(payload, dict) else None,
    }


def _failure_log_rows(study_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((study_dir / "states").glob("**/failure.log")):
        try:
            payload = _load(path)
        except (OSError, json.JSONDecodeError):
            payload = {"status": "failure_log_unreadable", "path": str(path)}
        if not isinstance(payload, dict):
            payload = {"status": "failure_log_invalid", "payload": payload}
        payload = dict(payload)
        payload.setdefault("path", str(path))
        payload.setdefault("state_id", path.parent.name)
        rows.append(payload)
    return rows

def compare_candidates(
    baseline: Path | None,
    candidates: list[Path],
    directions: dict[str, str] | None = None,
) -> dict[str, Any]:
    rows = []
    if baseline and baseline.exists():
        row = _candidate_row(baseline)
        row["role"] = "baseline"
        rows.append(row)
    for path in candidates:
        if path.exists():
            row = _candidate_row(path)
            row["role"] = "candidate"
            rows.append(row)
    directions = directions or {}
    if not rows:
        return {"status": "needs_validation", "candidates": []}
    objective_names = sorted({name for row in rows for name in row["objectives"]})
    best_id = None
    ranking_objective = objective_names[0] if objective_names else None
    if ranking_objective:
        available = [row for row in rows if isinstance(row["objectives"].get(ranking_objective), (int, float))]
        if available:
            reverse = directions.get(ranking_objective, "maximize") != "minimize"
            best_id = sorted(available, key=lambda row: row["objectives"][ranking_objective], reverse=reverse)[0]["id"]
    return {
        "status": "validated" if best_id is not None else "needs_validation",
        "ranking_objective": ranking_objective,
        "direction": directions.get(ranking_objective, "maximize") if ranking_objective else None,
        "best_candidate_id": best_id,
        "candidates": rows,
    }


def _deduplicate_failure_cases(rows: list[dict[str, Any]], study_dir: Path | None = None) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        state_id = str(row.get("state_id", ""))
        status = str(row.get("status", ""))
        evidence = row.get("path") or row.get("failure_log") or row.get("error") or row.get("reason") or ""
        if evidence and study_dir is not None and not Path(str(evidence)).is_absolute():
            evidence = str((study_dir / str(evidence)).resolve())
        else:
            evidence = str(evidence)
        key = (state_id, status, evidence.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _find_best_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    for key in ("best_result", "study_best"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and (
            candidate.get("params") is not None or candidate.get("value") is not None
        ):
            return candidate
    for key in ("campaign", "result", "optimization"):
        candidate = _find_best_result(value.get(key))
        if candidate is not None:
            return candidate
    return None


def best_structure_summary(
    comparison: dict[str, Any],
    optimization_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    best_id = comparison.get("best_candidate_id")
    if best_id is not None:
        row = next(
            (item for item in comparison.get("candidates", []) if item.get("id") == best_id),
            None,
        )
        return {
            "status": "validated",
            "source": "candidate_evaluation",
            "candidate_id": best_id,
            "parameters": None,
            "objectives": row.get("objectives", {}) if row else {},
            "path": row.get("path") if row else None,
        }
    runtime_best = _find_best_result(optimization_summary or {})
    if runtime_best is not None:
        return {
            "status": "validated",
            "source": "optimization_runtime",
            "candidate_id": runtime_best.get("trial_number"),
            "parameters": runtime_best.get("params"),
            "objectives": {"value": runtime_best.get("value")},
            "path": (optimization_summary or {}).get("project_path"),
        }
    return {
        "status": "needs_validation",
        "source": None,
        "candidate_id": None,
        "parameters": None,
        "objectives": {},
        "path": None,
    }

def build_report(study_dir: Path, output: Path) -> dict[str, Any]:
    plan_path = study_dir / "study_plan.json"
    summary_path = study_dir / "study_summary.json"
    plan = _load(plan_path) if plan_path.exists() else {}
    summary = _load(summary_path) if summary_path.exists() else {}
    evaluation_path = study_dir / "study_evaluation.json"
    if evaluation_path.exists():
        evaluation = _load(evaluation_path)
    else:
        evaluation = evaluate_study_directory(study_dir, evaluation_path)

    failed_cases = []
    for state in summary.get("states", []) if isinstance(summary, dict) else []:
        if state.get("status") not in {"validated", "success"}:
            failed_cases.append(state)

    level_entries = []
    for raw in plan.get("integration_levels", []) if isinstance(plan, dict) else []:
        level = dict(raw)
        metrics_file = level.get("metrics_file")
        if metrics_file:
            path = Path(metrics_file)
            if path.exists():
                loaded = _load(path)
                level["metrics"] = loaded.get("metrics", loaded) if isinstance(loaded, dict) else {}
        level_entries.append(level)

    evaluation_failures = [item for item in evaluation.get("states", []) if item.get("status") not in {None, "validated", "success"}]
    failed_cases.extend(evaluation_failures)
    failed_cases.extend(_failure_log_rows(study_dir))
    failed_cases = _deduplicate_failure_cases(failed_cases, study_dir)

    optimization = plan.get("optimization", {}) if isinstance(plan, dict) else {}
    directions = {
        str(item.get("name")): str(item.get("direction", "maximize"))
        for item in optimization.get("objectives", [])
        if isinstance(item, dict) and item.get("name")
    }
    baseline_value = plan.get("baseline_evaluation") if isinstance(plan, dict) else None
    candidate_values = plan.get("candidate_evaluations", []) if isinstance(plan, dict) else []
    baseline_path = Path(baseline_value) if baseline_value else None
    candidate_paths = [Path(value) for value in candidate_values if isinstance(value, str)]
    comparison = compare_candidates(baseline_path, candidate_paths, directions)

    optimization_summary_path = study_dir / "optimization" / "optimization-summary.json"
    optimization_summary = _load(optimization_summary_path) if optimization_summary_path.exists() else None
    export_paths = [str(path) for path in sorted((study_dir / "states").glob("**/exports/*.json"))]
    project_paths = [
        {
            "state_id": case.get("state_id"),
            "path": case.get("project_path"),
            "exists": bool(case.get("project_path") and Path(case["project_path"]).exists()),
        }
        for case in plan.get("cases", [])
        if isinstance(case, dict)
    ]
    state_codebook = [
        {
            "id": case.get("state_id"),
            "bits": case.get("bits"),
            "parameters": case.get("parameters", {}),
            "device_model": case.get("device_model"),
        }
        for case in plan.get("cases", [])
        if isinstance(case, dict)
    ]
    report = {
        "status": evaluation.get("status") if evaluation.get("status") in {"validated", "partial"} else "needs_validation",
        "profile": plan.get("profile"),
        "classification": plan.get("classification"),
        "backend": plan.get("backend"),
        "source_project": plan.get("source_project"),
        "frequency_spec": plan.get("frequency_spec"),
        "evaluation": evaluation,
        "radiation_antenna_gate": plan.get(
            "radiation_antenna_gate",
            evaluation.get("radiation_antenna_gate"),
        ),
        "preflight_gates": plan.get("preflight_gates"),
        "optimization_allowed": plan.get("optimization_allowed"),
        "smoke_gates": summary.get("smoke_gates"),
        "array_code_map": plan.get("array_code_map"),
        "state_codebook": state_codebook,
        "acceptance_thresholds": plan.get("acceptance_thresholds", {}),
        "parameter_sources": plan.get("device_evidence", []),
        "feed_network": plan.get("feed_network"),
        "bias_network": plan.get("bias_network"),
        "integration_levels": level_deltas(level_entries),
        "baseline_comparison": comparison,
        "best_structure": best_structure_summary(comparison, optimization_summary),
        "convergence_history": plan.get("convergence_history", summary.get("convergence_history")),
        "optimization": summary.get("optimization"),
        "mesh_convergence": summary.get("mesh_convergence"),
        "spike_refinement": summary.get("spike_refinement"),
        "failed_cases": failed_cases,
        "paths": {
            "plan": str(plan_path) if plan_path.exists() else None,
            "summary": str(summary_path) if summary_path.exists() else None,
            "evaluation": str(evaluation_path),
            "projects": project_paths,
            "exports": export_paths,
            "optimization_summary": str(optimization_summary_path) if optimization_summary_path.exists() else None,
            "mesh_convergence_result": str(study_dir / "mesh_convergence_result.json") if (study_dir / "mesh_convergence_result.json").exists() else None,
            "spike_refinement_root": str(study_dir / "refinements") if (study_dir / "refinements").exists() else None,
        },
        "remaining_validation": list(plan.get("remaining_validation", [])) + [
            "Run a full-wave candidate when the study contains no validated exports." if not export_paths else "",
            "Confirm mesh convergence and result identity for every selected candidate.",
        ],
    }
    report["remaining_validation"] = list(dict.fromkeys(
        item for item in report["remaining_validation"] if item
    ))
    report["remaining_risks"] = list(report["remaining_validation"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_report(args.study_dir, args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
