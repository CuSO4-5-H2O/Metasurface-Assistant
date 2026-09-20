#!/usr/bin/env python3
"""Ideal aperture phase generation and N-state metasurface quantisation."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


LIGHT_SPEED_M_S = 299_792_458.0


def circular_error(measured_deg: float, target_deg: float) -> float:
    return abs((measured_deg - target_deg + 180.0) % 360.0 - 180.0)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    return float(value)


def _frequency_hz(study: dict[str, Any], array: dict[str, Any]) -> float:
    if "frequency_hz" in array:
        return _number(array["frequency_hz"], "array.frequency_hz")
    spec = study.get("frequency_spec", {})
    value = spec.get("target")
    if value is None:
        raise ValueError("array requires frequency_hz or frequency_spec.target")
    unit = str(spec.get("unit", "GHz")).lower()
    multiplier = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "thz": 1e12}.get(unit)
    if multiplier is None:
        raise ValueError(f"unsupported frequency unit: {unit}")
    return _number(value, "frequency_spec.target") * multiplier


def _spacing_m(array: dict[str, Any], axis: str) -> float:
    if f"d{axis}_m" in array:
        return _number(array[f"d{axis}_m"], f"array.d{axis}_m")
    if f"d{axis}_mm" in array:
        return _number(array[f"d{axis}_mm"], f"array.d{axis}_mm") * 1e-3
    spacing = array.get("spacing_m")
    if isinstance(spacing, dict) and axis in spacing:
        return _number(spacing[axis], f"array.spacing_m.{axis}")
    if isinstance(spacing, (int, float)):
        return _number(spacing, "array.spacing_m")
    raise ValueError(f"array requires d{axis}_m, d{axis}_mm, or spacing_m")


def _direction_components(theta_deg: float, phi_deg: float) -> tuple[float, float]:
    theta = math.radians(theta_deg)
    phi = math.radians(phi_deg)
    return math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi)


def _matrix(value: Any, rows: int, cols: int, name: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != rows:
        raise ValueError(f"{name} must have {rows} rows")
    result: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != cols:
            raise ValueError(f"{name} must have {rows}x{cols} entries")
        result.append([_number(item, name) for item in row])
    return result


def ideal_phase_grid(study: dict[str, Any]) -> list[list[float]]:
    array = study.get("array", {})
    if not isinstance(array, dict):
        raise ValueError("array must be an object")
    if isinstance(array.get("phase_deg"), list):
        rows = int(_number(array.get("rows", len(array["phase_deg"])), "array.rows"))
        cols = int(_number(array.get("cols", len(array["phase_deg"][0])), "array.cols"))
        return _matrix(array["phase_deg"], rows, cols, "array.phase_deg")
    rows = int(_number(array.get("rows"), "array.rows"))
    cols = int(_number(array.get("cols"), "array.cols"))
    if rows < 1 or cols < 1:
        raise ValueError("array.rows and array.cols must be positive")
    frequency = _frequency_hz(study, array)
    dx = _spacing_m(array, "x")
    dy = _spacing_m(array, "y")
    target_theta = _number(array.get("target_theta_deg", 0.0), "array.target_theta_deg")
    target_phi = _number(array.get("target_phi_deg", 0.0), "array.target_phi_deg")
    incident_theta = _number(array.get("incident_theta_deg", 0.0), "array.incident_theta_deg")
    incident_phi = _number(array.get("incident_phi_deg", 0.0), "array.incident_phi_deg")
    target_u, target_v = _direction_components(target_theta, target_phi)
    incident_u, incident_v = _direction_components(incident_theta, incident_phi)
    phase_sign = _number(array.get("phase_sign", -1.0), "array.phase_sign")
    offset = _number(array.get("phase_offset_deg", 0.0), "array.phase_offset_deg")
    coefficient = phase_sign * 360.0 * frequency / LIGHT_SPEED_M_S
    result: list[list[float]] = []
    for row in range(rows):
        y = (row - (rows - 1) / 2.0) * dy
        result_row: list[float] = []
        for col in range(cols):
            x = (col - (cols - 1) / 2.0) * dx
            result_row.append((offset + coefficient * (x * (target_u - incident_u) + y * (target_v - incident_v))) % 360.0)
        result.append(result_row)
    return result


def _state_phase(state: dict[str, Any], index: int, count: int) -> float:
    value = state.get("phase_deg", state.get("phase_target_deg"))
    if value is None:
        raise ValueError(f"state {state.get('id', index)} needs phase_deg for array quantisation")
    return float(value) % 360.0


def quantize_phase_grid(study: dict[str, Any]) -> dict[str, Any]:
    states = study.get("state_codebook", [])
    if isinstance(states, dict):
        states = states.get("states", [])
    if not isinstance(states, list) or len(states) < 2:
        raise ValueError("state_codebook needs at least two states")
    state_phases = [_state_phase(state, index, len(states)) for index, state in enumerate(states)]
    phases = ideal_phase_grid(study)
    matrix: list[list[str]] = []
    index_matrix: list[list[int]] = []
    quantized: list[list[float]] = []
    errors: list[list[float]] = []
    for row in phases:
        state_row: list[str] = []
        index_row: list[int] = []
        quantized_row: list[float] = []
        error_row: list[float] = []
        for target in row:
            state_index = min(range(len(states)), key=lambda item: circular_error(state_phases[item], target))
            state_row.append(str(states[state_index].get("id", states[state_index].get("bits", state_index))))
            index_row.append(state_index)
            quantized_row.append(state_phases[state_index])
            error_row.append(circular_error(state_phases[state_index], target))
        matrix.append(state_row)
        index_matrix.append(index_row)
        quantized.append(quantized_row)
        errors.append(error_row)
    flat_errors = [value for row in errors for value in row]
    counts = {str(state.get("id", state.get("bits", index))): 0 for index, state in enumerate(states)}
    for row in matrix:
        for state_id in row:
            counts[state_id] = counts.get(state_id, 0) + 1
    return {
        "status": "validated",
        "state_count": len(states),
        "state_phases_deg": state_phases,
        "ideal_phase_deg": phases,
        "state_matrix": matrix,
        "state_index_matrix": index_matrix,
        "quantized_phase_deg": quantized,
        "phase_error_deg": errors,
        "max_quantization_error_deg": max(flat_errors, default=0.0),
        "rms_quantization_error_deg": math.sqrt(sum(value * value for value in flat_errors) / len(flat_errors)) if flat_errors else 0.0,
        "state_counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    study = json.loads(args.input.read_text(encoding="utf-8"))
    result = quantize_phase_grid(study)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
