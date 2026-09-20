#!/usr/bin/env python3
"""Deterministic metrics for CST-exported metasurface and antenna data."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    return float(value)


def _series(obj: dict[str, Any], names: Iterable[str]) -> list[float] | None:
    for name in names:
        value = obj.get(name)
        if isinstance(value, list):
            return [_as_float(v) for v in value]
    return None


def _complex_value(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return _as_float(value.get("real", value.get("re", 0.0))), _as_float(
            value.get("imag", value.get("im", 0.0))
        )
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _as_float(value[0]), _as_float(value[1])
    if isinstance(value, str):
        text = value.strip().strip("()").replace("i", "j")
        try:
            parsed = complex(text)
            return float(parsed.real), float(parsed.imag)
        except ValueError:
            pass
    return _as_float(value), 0.0


def _normalise_trace(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        rows = payload
        freq: list[float] = []
        real: list[float] = []
        imag: list[float] = []
        for row in rows:
            if isinstance(row, dict):
                freq.append(_as_float(row.get("frequency", row.get("freq", row.get("x")))))
                re, im = _complex_value(row.get("complex", row.get("value", row.get("y", 0.0))))
            else:
                if len(row) < 2:
                    raise ValueError("data rows must contain frequency and a complex response")
                freq.append(_as_float(row[0]))
                if len(row) >= 3:
                    try:
                        re, im = _as_float(row[1]), _as_float(row[2])
                    except (TypeError, ValueError):
                        # CST exports [frequency, complex response, reference impedance].
                        re, im = _complex_value(row[1])
                else:
                    re, im = _complex_value(row[1])
            real.append(re)
            imag.append(im)
        return {"frequency": freq, "real": real, "imag": imag, "phase_valid": True}

    if not isinstance(payload, dict):
        raise ValueError("trace must be an object or row list")
    if str(payload.get("format", "")).lower() == "farfield_grid":
        return None
    if isinstance(payload.get("data"), list):
        return _normalise_trace(payload["data"])
    if isinstance(payload.get("xdata"), list) and isinstance(payload.get("ydata"), list):
        pairs = [_complex_value(row) for row in payload["ydata"]]
        count = min(len(payload["xdata"]), len(pairs))
        return {
            "frequency": [_as_float(value) for value in payload["xdata"][:count]],
            "real": [pair[0] for pair in pairs[:count]],
            "imag": [pair[1] for pair in pairs[:count]],
            "phase_valid": True,
        }

    freq = _series(payload, ("frequency", "frequencies", "freq", "x", "frequency_hz"))
    if freq is None:
        raise ValueError("trace is missing frequency/freq/x")
    complex_values = payload.get("complex")
    if isinstance(complex_values, list):
        pairs = [_complex_value(value) for value in complex_values]
        real, imag = zip(*pairs) if pairs else ([], [])
        return {
            "frequency": freq[: len(pairs)],
            "real": list(real),
            "imag": list(imag),
            "phase_valid": True,
        }

    component_values = None
    for name in ("e_theta", "E_theta", "etheta", "Etheta", "e_phi", "E_phi", "ephi", "Ephi"):
        value = payload.get(name)
        if isinstance(value, list):
            component_values = value
            break
    components = payload.get("components")
    if component_values is None and isinstance(components, dict):
        for name in ("e_theta", "E_theta", "etheta", "Etheta", "e_phi", "E_phi", "ephi", "Ephi"):
            value = components.get(name)
            if isinstance(value, list):
                component_values = value
                break
    if component_values is not None:
        pairs = [_complex_value(value) for value in component_values]
        count = min(len(freq), len(pairs))
        return {
            "frequency": freq[:count],
            "real": [pair[0] for pair in pairs[:count]],
            "imag": [pair[1] for pair in pairs[:count]],
            "phase_valid": True,
        }

    for prefix in ("e_theta", "E_theta", "etheta", "Etheta", "e_phi", "E_phi", "ephi", "Ephi"):
        component_real = _series(payload, (f"{prefix}_real", f"{prefix}_re"))
        component_imag = _series(payload, (f"{prefix}_imag", f"{prefix}_im"))
        if component_real is not None and component_imag is not None:
            count = min(len(freq), len(component_real), len(component_imag))
            return {
                "frequency": freq[:count],
                "real": component_real[:count],
                "imag": component_imag[:count],
                "phase_valid": True,
            }

    real = _series(payload, ("real", "re", "real_part"))
    imag = _series(payload, ("imag", "im", "imaginary", "imag_part"))
    explicit_complex = real is not None and imag is not None
    phase = _series(payload, ("phase_deg", "phase", "phase_degrees"))
    magnitude_db = _series(payload, ("db", "magnitude_db", "mag_db", "dB"))
    if real is None:
        magnitude = _series(payload, ("magnitude", "abs", "amplitude"))
        if magnitude is not None:
            real = magnitude
            imag = [0.0] * len(magnitude)
        elif magnitude_db is not None:
            magnitude = [10.0 ** (value / 20.0) for value in magnitude_db]
            if phase is None:
                real = magnitude
                imag = [0.0] * len(magnitude)
            else:
                real = [mag * math.cos(math.radians(ph)) for mag, ph in zip(magnitude, phase)]
                imag = [mag * math.sin(math.radians(ph)) for mag, ph in zip(magnitude, phase)]
        else:
            raise ValueError("trace is missing real/imag, complex, magnitude, or dB values")
    if imag is None:
        imag = [0.0] * len(real)
    count = min(len(freq), len(real), len(imag))
    return {
        "frequency": freq[:count],
        "real": real[:count],
        "imag": imag[:count],
        "phase_valid": explicit_complex or phase is not None or payload.get("complex") is not None,
    }


def _magnitude_db(real: list[float], imag: list[float]) -> list[float]:
    return [20.0 * math.log10(max(math.hypot(re, im), 1e-15)) for re, im in zip(real, imag)]


def _phase_deg(real: list[float], imag: list[float]) -> list[float]:
    return [math.degrees(math.atan2(im, re)) for re, im in zip(real, imag)]


def circular_error(measured_deg: float, target_deg: float) -> float:
    return abs((measured_deg - target_deg + 180.0) % 360.0 - 180.0)


def compare_backend_exports(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    *,
    complex_abs_tolerance: float = 1e-3,
    magnitude_db_tolerance: float = 0.02,
    phase_deg_tolerance: float = 0.1,
    frequency_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare two parameter-matched complex traces from different backends."""
    reference_trace = _normalise_trace(reference)
    candidate_trace = _normalise_trace(candidate)
    if reference_trace is None or candidate_trace is None:
        raise ValueError("backend comparison requires complex one-dimensional traces")

    reference_frequency = reference_trace["frequency"]
    candidate_frequency = candidate_trace["frequency"]
    same_length = len(reference_frequency) == len(candidate_frequency)
    compared_count = min(len(reference_frequency), len(candidate_frequency))
    frequency_differences = [
        abs(reference_frequency[index] - candidate_frequency[index])
        for index in range(compared_count)
    ]
    max_frequency_difference = max(frequency_differences, default=0.0)
    frequency_axis_matches = same_length and max_frequency_difference <= frequency_tolerance

    complex_differences: list[float] = []
    magnitude_differences: list[float] = []
    phase_differences: list[float] = []
    reference_magnitude = _magnitude_db(reference_trace["real"], reference_trace["imag"])
    candidate_magnitude = _magnitude_db(candidate_trace["real"], candidate_trace["imag"])
    reference_phase = _phase_deg(reference_trace["real"], reference_trace["imag"])
    candidate_phase = _phase_deg(candidate_trace["real"], candidate_trace["imag"])
    for index in range(compared_count):
        complex_differences.append(
            math.hypot(
                reference_trace["real"][index] - candidate_trace["real"][index],
                reference_trace["imag"][index] - candidate_trace["imag"][index],
            )
        )
        magnitude_differences.append(abs(reference_magnitude[index] - candidate_magnitude[index]))
        phase_differences.append(circular_error(candidate_phase[index], reference_phase[index]))

    parameter_identity = _parameter_identity(
        reference.get("parameter_combination"),
        candidate.get("parameter_combination"),
    )
    max_complex_difference = max(complex_differences, default=0.0)
    max_magnitude_difference = max(magnitude_differences, default=0.0)
    max_phase_difference = max(phase_differences, default=0.0)
    rms_complex_difference = math.sqrt(
        sum(value * value for value in complex_differences) / max(1, len(complex_differences))
    )
    rms_phase_difference = math.sqrt(
        sum(value * value for value in phase_differences) / max(1, len(phase_differences))
    )
    within_tolerance = (
        compared_count > 0
        and frequency_axis_matches
        and parameter_identity["status"] == "matched"
        and max_complex_difference <= complex_abs_tolerance
        and max_magnitude_difference <= magnitude_db_tolerance
        and max_phase_difference <= phase_deg_tolerance
    )
    return {
        "status": "validated" if within_tolerance else "failed_consistency",
        "within_tolerance": within_tolerance,
        "sample_count": compared_count,
        "reference_sample_count": len(reference_frequency),
        "candidate_sample_count": len(candidate_frequency),
        "frequency_axis_matches": frequency_axis_matches,
        "max_frequency_difference": max_frequency_difference,
        "parameter_identity": parameter_identity,
        "max_complex_abs_difference": max_complex_difference,
        "rms_complex_abs_difference": rms_complex_difference,
        "max_magnitude_difference_db": max_magnitude_difference,
        "max_phase_difference_deg": max_phase_difference,
        "rms_phase_difference_deg": rms_phase_difference,
        "tolerances": {
            "complex_abs": complex_abs_tolerance,
            "magnitude_db": magnitude_db_tolerance,
            "phase_deg": phase_deg_tolerance,
            "frequency": frequency_tolerance,
        },
    }


def _unwrap_deg(values: list[float]) -> list[float]:
    if not values:
        return []
    unwrapped = [values[0]]
    for value in values[1:]:
        delta = (value - unwrapped[-1] + 180.0) % 360.0 - 180.0
        unwrapped.append(unwrapped[-1] + delta)
    return unwrapped


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _mad(values: list[float]) -> float:
    centre = _median(values)
    return _median([abs(value - centre) for value in values])


def _contiguous_intervals(
    frequency: list[float],
    values: list[float],
    threshold: float,
    band: tuple[float, float] | None,
) -> list[dict[str, Any]]:
    if not frequency:
        return []
    order = sorted(range(len(frequency)), key=frequency.__getitem__)
    f = [frequency[i] for i in order]
    y = [values[i] for i in order]
    steps = [b - a for a, b in zip(f, f[1:]) if b > a]
    median_step = _median(steps) or 1.0
    low, high = band if band is not None else (f[0], f[-1])
    intervals: list[dict[str, Any]] = []
    start: int | None = None
    for i, (freq_i, value_i) in enumerate(zip(f, y)):
        passing = low <= freq_i <= high and value_i <= threshold
        gap = i > 0 and f[i] - f[i - 1] > 1.5 * median_step
        if passing and start is None:
            start = i
        if start is not None and (not passing or gap or i == len(f) - 1):
            end = i if passing and i == len(f) - 1 and not gap else i - 1
            if end >= start:
                segment = y[start : end + 1]
                intervals.append(
                    {
                        "start": f[start],
                        "stop": f[end],
                        "width": f[end] - f[start],
                        "min_db": min(segment),
                        "max_db": max(segment),
                        "samples": len(segment),
                    }
                )
            start = None
    return intervals


def smoothness_metrics(frequency: list[float], values: list[float], absolute_threshold: float = 3.0) -> dict[str, Any]:
    if len(frequency) < 3:
        return {"status": "insufficient_samples", "spike_count": 0}
    order = sorted(range(len(frequency)), key=frequency.__getitem__)
    f = [frequency[i] for i in order]
    y = [values[i] for i in order]
    slopes = [
        (y[i + 1] - y[i]) / max(f[i + 1] - f[i], 1e-15)
        for i in range(len(f) - 1)
    ]
    residuals: list[float] = []
    for i in range(1, len(f) - 1):
        weight = (f[i] - f[i - 1]) / max(f[i + 1] - f[i - 1], 1e-15)
        expected = y[i - 1] + weight * (y[i + 1] - y[i - 1])
        residuals.append(abs(y[i] - expected))
    noise_floor = max(absolute_threshold / 12.0, 1e-6)
    scale = max(1.4826 * _mad(residuals), noise_floor)
    spike_indices = [
        i + 1 for i, residual in enumerate(residuals) if residual >= absolute_threshold or residual / scale >= 6.0
    ]
    refinement_request = None
    if spike_indices:
        low_index = max(0, min(spike_indices) - 2)
        high_index = min(len(f) - 1, max(spike_indices) + 2)
        step = min(
            (right - left for left, right in zip(f, f[1:]) if right > left),
            default=0.0,
        )
        refinement_request = {
            "status": "local_frequency_refinement_required",
            "frequency_start": f[low_index],
            "frequency_stop": f[high_index],
            "suggested_step": step / 4.0 if step else None,
            "source_indices": spike_indices,
        }
    curvature = [abs(slopes[i + 1] - slopes[i]) for i in range(len(slopes) - 1)]
    return {
        "status": "needs_refinement" if spike_indices else "ok",
        "spike_count": len(spike_indices),
        "spike_indices": spike_indices,
        "noise_floor_db": noise_floor,
        "max_local_residual": max(residuals, default=0.0),
        "slope_p95": _percentile([abs(value) for value in slopes], 0.95),
        "curvature_p95": _percentile(curvature, 0.95),
        "diagnosis": "suspected_spike_requires_refinement" if spike_indices else "no_suspected_spike",
        "refinement_request": refinement_request,
    }


def evaluate_sparameter(
    payload: Any,
    threshold_db: float | None = -10.0,
    band: tuple[float, float] | None = None,
) -> dict[str, Any]:
    trace = _normalise_trace(payload)
    magnitude_db = _magnitude_db(trace["real"], trace["imag"])
    intervals = (
        _contiguous_intervals(trace["frequency"], magnitude_db, threshold_db, band)
        if threshold_db is not None
        else []
    )
    best = max(intervals, key=lambda item: item["width"], default=None)
    result = {
        "threshold_db": threshold_db,
        "frequency": trace["frequency"],
        "magnitude_db": magnitude_db,
        "passing_intervals": intervals,
        "widest_interval": best,
        "meets_threshold": (best is not None) if threshold_db is not None else None,
        "fractional_bandwidth_percent": (
            100.0 * (best["stop"] - best["start"]) / ((best["start"] + best["stop"]) / 2.0)
            if best and best["start"] + best["stop"]
            else None
        ),
        "smoothness": smoothness_metrics(trace["frequency"], magnitude_db),
        "phase_deg": _phase_deg(trace["real"], trace["imag"]) if trace["phase_valid"] else None,
        "status": (
            "needs_validation"
            if not trace["frequency"]
            else "failed_constraints"
            if threshold_db is not None and best is None
            else "validated"
        ),
    }
    if isinstance(payload, dict):
        for key in ("treepath", "run_id", "parameter_combination", "title", "xlabel", "ylabel"):
            if key in payload:
                result[key] = payload[key]
    return result

def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def evaluate_codebook(
    states: list[dict[str, Any]],
    band: tuple[float, float] | None = None,
    phase_tolerance_deg: float = 10.0,
    amplitude_spread_db: float = 1.5,
) -> dict[str, Any]:
    if len(states) < 2:
        return {"status": "needs_validation", "reason": "at_least_two_states_required"}
    traces = [(state.get("id", str(index)), _normalise_trace(state)) for index, state in enumerate(states)]
    reference_frequency = traces[0][1]["frequency"]
    low, high = band if band is not None else (reference_frequency[0], reference_frequency[-1])
    samples: list[dict[str, Any]] = []
    for target_frequency in reference_frequency:
        if not low <= target_frequency <= high:
            continue
        phases: list[float] = []
        amplitudes: list[float] = []
        valid = True
        for _, trace in traces:
            if not trace["phase_valid"]:
                valid = False
                break
            index = _nearest_index(trace["frequency"], target_frequency)
            phases.append(_phase_deg([trace["real"][index]], [trace["imag"][index]])[0])
            amplitudes.append(_magnitude_db([trace["real"][index]], [trace["imag"][index]])[0])
        if not valid:
            continue
        nominal_step = 360.0 / len(phases)
        errors = [
            circular_error(phases[(index + 1) % len(phases)] - phases[index], nominal_step)
            for index in range(len(phases))
        ]
        samples.append(
            {
                "frequency": target_frequency,
                "phase_errors_deg": errors,
                "max_phase_error_deg": max(errors),
                "amplitude_spread_db": max(amplitudes) - min(amplitudes),
                "passes": max(errors) <= phase_tolerance_deg
                and max(amplitudes) - min(amplitudes) <= amplitude_spread_db,
            }
        )
    passing = [sample for sample in samples if sample["passes"]]
    ordered_samples = sorted(samples, key=lambda item: item["frequency"])
    sample_steps = [
        right["frequency"] - left["frequency"]
        for left, right in zip(ordered_samples, ordered_samples[1:])
        if right["frequency"] > left["frequency"]
    ]
    median_step = _median(sample_steps) or 1.0
    valid_intervals: list[dict[str, Any]] = []
    start: float | None = None
    previous: float | None = None
    count = 0

    def close_interval(stop: float | None) -> None:
        nonlocal start, count
        if start is not None and stop is not None:
            valid_intervals.append(
                {
                    "start": start,
                    "stop": stop,
                    "width": stop - start,
                    "samples": count,
                }
            )
        start = None
        count = 0

    for sample in ordered_samples:
        frequency = sample["frequency"]
        contiguous = previous is not None and frequency - previous <= 1.5 * median_step
        if sample["passes"] and (start is None or contiguous):
            if start is None:
                start = frequency
            count += 1
        else:
            close_interval(previous)
            if sample["passes"]:
                start = frequency
                count = 1
        previous = frequency
    close_interval(previous)
    best_interval = max(valid_intervals, key=lambda item: item["width"], default=None)
    valid_width = best_interval["width"] if best_interval else 0.0
    return {
        "state_count": len(states),
        "nominal_step_deg": 360.0 / len(states),
        "phase_tolerance_deg": phase_tolerance_deg,
        "amplitude_spread_limit_db": amplitude_spread_db,
        "samples": samples,
        "valid_sample_count": len(passing),
        "valid_bandwidth": valid_width,
        "meets_constraints": bool(valid_intervals),
        "valid_intervals": valid_intervals,
        "max_phase_error_deg": max((item["max_phase_error_deg"] for item in samples), default=None),
        "max_amplitude_spread_db": max((item["amplitude_spread_db"] for item in samples), default=None),
        "rms_phase_error_deg": math.sqrt(sum(error * error for sample in samples for error in sample["phase_errors_deg"]) / sum(len(sample["phase_errors_deg"]) for sample in samples)) if samples else None,
        "phase_error_p95_deg": _percentile([error for sample in samples for error in sample["phase_errors_deg"]], 0.95) if samples else None,
        "status": ("validated" if valid_intervals else "failed_constraints") if samples else "needs_validation",
    }


def _farfield_component_values(payload: dict[str, Any], name: str) -> list[Any] | None:
    candidates = [name, name.replace("_", ""), name.title().replace("_", "")]
    for candidate in candidates:
        if isinstance(payload.get(candidate), list):
            return payload[candidate]
    components = payload.get("components")
    if isinstance(components, dict):
        for candidate in candidates:
            if isinstance(components.get(candidate), list):
                return components[candidate]
    return None


def _ludwig3_values(
    payload: dict[str, Any],
    component: str,
    reference_axis: str | None,
) -> list[list[float]] | None:
    axis = str(reference_axis or "").strip().lower()
    if axis not in {"x", "y"}:
        return None
    theta_values = _farfield_component_values(payload, "e_theta")
    phi_values = _farfield_component_values(payload, "e_phi")
    if theta_values is None or phi_values is None:
        return None
    azimuth = _series(payload, ("phi", "phi_deg"))
    count = min(len(theta_values), len(phi_values))
    if azimuth is None:
        fixed_phi = payload.get("fixed_phi_deg")
        if fixed_phi is None:
            return None
        azimuth = [_as_float(fixed_phi)] * count
    count = min(count, len(azimuth))
    transformed: list[list[float]] = []
    for theta_value, phi_value, phi_angle in zip(
        theta_values[:count], phi_values[:count], azimuth[:count]
    ):
        theta_real, theta_imag = _complex_value(theta_value)
        phi_real, phi_imag = _complex_value(phi_value)
        e_theta = complex(theta_real, theta_imag)
        e_phi = complex(phi_real, phi_imag)
        cos_phi = math.cos(math.radians(phi_angle))
        sin_phi = math.sin(math.radians(phi_angle))
        if axis == "x":
            co = e_theta * cos_phi - e_phi * sin_phi
            cross = e_theta * sin_phi + e_phi * cos_phi
        else:
            co = e_theta * sin_phi + e_phi * cos_phi
            cross = -e_theta * cos_phi + e_phi * sin_phi
        selected = co if component == "ludwig3_co" else cross
        transformed.append([float(selected.real), float(selected.imag)])
    return transformed


def _select_farfield_component(
    payload: dict[str, Any],
    component: str | None,
    ludwig3_reference_axis: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = str(component or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "theta": "e_theta",
        "etheta": "e_theta",
        "e_theta": "e_theta",
        "phi": "e_phi",
        "ephi": "e_phi",
        "e_phi": "e_phi",
        "co": "ludwig3_co",
        "copol": "ludwig3_co",
        "co_polar": "ludwig3_co",
        "ludwig3_co": "ludwig3_co",
        "ludwig_3_co": "ludwig3_co",
        "cross": "ludwig3_cross",
        "crosspol": "ludwig3_cross",
        "cross_polar": "ludwig3_cross",
        "ludwig3_cross": "ludwig3_cross",
        "ludwig_3_cross": "ludwig3_cross",
    }
    requested = aliases.get(normalized)
    if requested is None:
        return payload, None
    if requested.startswith("ludwig3_"):
        values = _ludwig3_values(payload, requested, ludwig3_reference_axis)
        if values is None:
            return None, requested
        return {**payload, "complex": values, "polarization_basis": "ludwig_3"}, requested
    values = _farfield_component_values(payload, requested)
    if values is not None:
        return {**payload, "complex": values}, requested
    return None, requested


def evaluate_farfield_phase(
    payload: dict[str, Any],
    magnitude_floor_db: float = -30.0,
    component: str | None = None,
) -> dict[str, Any]:
    theta = _series(payload, ("theta", "theta_deg", "angle", "angles"))
    if theta is None:
        raise ValueError("far-field payload is missing theta")
    trace = _normalise_trace({**payload, "frequency": list(range(len(theta)))})
    count = min(len(theta), len(trace["real"]), len(trace["imag"]))
    order = sorted(range(count), key=lambda index: theta[index])
    theta = [theta[index] for index in order]
    trace["real"] = [trace["real"][index] for index in order]
    trace["imag"] = [trace["imag"][index] for index in order]
    magnitude_db = _magnitude_db(trace["real"], trace["imag"])
    peak = max(magnitude_db, default=-300.0)
    valid = [index for index, value in enumerate(magnitude_db) if value >= peak + magnitude_floor_db]
    phases = _phase_deg(trace["real"], trace["imag"])
    if not valid:
        return {
            "status": "needs_validation",
            "valid_points": 0,
            "component": component,
        }
    valid_phases = [phases[index] for index in valid]
    valid_unwrapped_phases = _unwrap_deg(valid_phases)
    reference = valid_phases[0]
    deviations = [circular_error(phase, reference) for phase in valid_phases]
    steps = [
        circular_error(phases[b], phases[a])
        for a, b in zip(valid, valid[1:])
        if b == a + 1
    ]
    return {
        "status": "validated",
        "component": component,
        "frequency_ghz": payload.get("frequency_ghz"),
        "fixed_phi_deg": payload.get("fixed_phi_deg"),
        "reference_plane": payload.get("reference_plane"),
        "polarization_basis": payload.get("polarization_basis"),
        "valid_points": len(valid),
        "theta_start": theta[valid[0]],
        "theta_stop": theta[valid[-1]],
        "magnitude_floor_relative_db": magnitude_floor_db,
        "phase_range_deg": max(valid_unwrapped_phases) - min(valid_unwrapped_phases),
        "max_deviation_from_reference_deg": max(deviations, default=0.0),
        "max_adjacent_phase_step_deg": max(steps, default=0.0),
    }


def _farfield_grid_cut(payload: dict[str, Any], phi_cut_deg: float = 0.0) -> dict[str, Any] | None:
    """Convert a CST farfield_grid export into a theta cut for scalar metrics."""
    if str(payload.get("format", "")).lower() != "farfield_grid":
        return None
    theta = payload.get("xpositions", payload.get("theta_values"))
    phi = payload.get("ypositions", payload.get("phi_values"))
    data = payload.get("data")
    if not isinstance(theta, list) or not isinstance(phi, list) or not isinstance(data, list):
        return None
    if not theta or not phi or not data:
        return None
    try:
        theta_values = [_as_float(value) for value in theta]
        phi_values = [_as_float(value) for value in phi]
    except (TypeError, ValueError):
        return None
    rows = [[_as_float(value) for value in row] for row in data if isinstance(row, list)]
    if len(rows) == len(phi_values) and rows and len(rows[0]) == len(theta_values):
        phi_index = min(range(len(phi_values)), key=lambda index: abs(((phi_values[index] - phi_cut_deg + 180.0) % 360.0) - 180.0))
        cut = rows[phi_index]
    elif len(rows) == len(theta_values) and rows and len(rows[0]) == len(phi_values):
        phi_index = min(range(len(phi_values)), key=lambda index: abs(((phi_values[index] - phi_cut_deg + 180.0) % 360.0) - 180.0))
        cut = [row[phi_index] for row in rows]
    else:
        return None
    quantity = str(payload.get("quantity", payload.get("zlabel", "gain"))).lower()
    if "realized gain" in quantity:
        metric_name = "realized_gain_dbi"
    elif "directivity" in quantity:
        metric_name = "directivity_dbi"
    else:
        metric_name = "gain_dbi"
    return {
        "theta": theta_values,
        metric_name: cut,
        "phi_cut_deg": phi_values[phi_index],
        "frequency_ghz": payload.get("frequency_ghz"),
        "source_format": "farfield_grid",
    }

def evaluate_farfield_gain(
    payload: dict[str, Any],
    absolute_target_dbi: float | None = None,
) -> dict[str, Any]:
    theta = _series(payload, ("theta", "theta_deg", "angle", "angles"))
    if theta is None:
        raise ValueError("far-field gain payload is missing theta")
    metric_name = next(
        (name for name in ("realized_gain_dbi", "gain_dbi", "directivity_dbi", "gain", "directivity") if isinstance(payload.get(name), list)),
        None,
    )
    if metric_name is None:
        raise ValueError("far-field gain payload is missing gain/directivity data")
    gain = _series(payload, (metric_name,))
    if gain is None or len(gain) != len(theta):
        raise ValueError("far-field gain and theta lengths do not match")
    order = sorted(range(len(theta)), key=theta.__getitem__)
    sorted_theta = [theta[index] for index in order]
    sorted_gain = [gain[index] for index in order]
    peak_index = max(range(len(sorted_gain)), key=sorted_gain.__getitem__)
    peak = sorted_gain[peak_index]
    half_power = peak - 3.0
    left = peak_index
    while left > 0 and sorted_gain[left - 1] >= half_power:
        left -= 1
    right = peak_index
    while right + 1 < len(sorted_gain) and sorted_gain[right + 1] >= half_power:
        right += 1
    outside = sorted_gain[:left] + sorted_gain[right + 1:]
    cross = _series(payload, ("cross_polar_dbi", "cross_polar_gain_dbi", "crosspol_dbi"))
    result = {
        "status": "validated",
        "metric": metric_name,
        "peak_gain_dbi": peak,
        "peak_theta_deg": sorted_theta[peak_index],
        "beamwidth_3db_deg": sorted_theta[right] - sorted_theta[left],
        "sidelobe_max_dbi": max(outside, default=None),
        "cross_polar_max_dbi": max(cross) if cross else None,
        "absolute_target_dbi": absolute_target_dbi,
        "meets_absolute_target": None if absolute_target_dbi is None else peak >= absolute_target_dbi,
    }
    return result


def evaluate_absorption(
    reflection: Any,
    transmission: Any,
) -> dict[str, Any]:
    reflected = _normalise_trace(reflection)
    transmitted = _normalise_trace(transmission)
    frequency = reflected["frequency"]
    absorption: list[float] = []
    for index, target in enumerate(frequency):
        r_index = _nearest_index(reflected["frequency"], target)
        t_index = _nearest_index(transmitted["frequency"], target)
        r = math.hypot(reflected["real"][r_index], reflected["imag"][r_index])
        t = math.hypot(transmitted["real"][t_index], transmitted["imag"][t_index])
        absorption.append(1.0 - r * r - t * t)
    peak_index = max(range(len(absorption)), key=absorption.__getitem__) if absorption else None
    return {
        "status": "validated" if absorption else "needs_validation",
        "frequency": frequency,
        "absorption": absorption,
        "peak_absorption": absorption[peak_index] if peak_index is not None else None,
        "peak_frequency": frequency[peak_index] if peak_index is not None else None,
    }


def _parameter_identity(expected: dict[str, Any], actual: Any) -> dict[str, Any]:
    if not expected:
        return {"status": "not_checked", "missing": [], "unexpected": [], "mismatched": []}
    if not isinstance(actual, dict):
        return {"status": "missing", "missing": list(expected), "unexpected": [], "mismatched": []}
    missing = [name for name in expected if name not in actual]
    unexpected = [name for name in actual if name not in expected]
    mismatched = []
    for name, expected_value in expected.items():
        if name in actual:
            try:
                if abs(float(expected_value) - float(actual[name])) > 1e-9 * max(1.0, abs(float(expected_value))):
                    mismatched.append({"name": name, "expected": expected_value, "actual": actual[name]})
            except (TypeError, ValueError):
                if expected_value != actual[name]:
                    mismatched.append({"name": name, "expected": expected_value, "actual": actual[name]})
    return {
        "status": "matched" if not missing and not unexpected and not mismatched else "mismatch",
        "missing": missing,
        "unexpected": unexpected,
        "mismatched": mismatched,
    }

def _is_input_match_name(name: Any) -> bool:
    normalized = str(name).lower().replace(" ", "")
    return any(token in normalized for token in ("s11", "s1,1", "zmax(1),zmax(1)"))


def _is_transmission_name(name: Any) -> bool:
    normalized = str(name).lower().replace(" ", "")
    return any(token in normalized for token in ("s21", "s2,1", "zmax(2),zmax(1)"))


def evaluate_network(network: dict[str, Any]) -> dict[str, Any]:
    """Evaluate feed or bias network evidence without inventing device limits."""
    if not isinstance(network, dict):
        raise ValueError("network must be an object")
    traces = network.get("sparameters", network.get("traces", {}))
    if isinstance(traces, list):
        trace_items = [(str(item.get("name", index)), item) for index, item in enumerate(traces) if isinstance(item, dict)]
    elif isinstance(traces, dict):
        trace_items = list(traces.items())
    else:
        trace_items = []
    evaluated: dict[str, dict[str, Any]] = {}
    for name, trace in trace_items:
        evaluated[str(name)] = evaluate_sparameter(trace)
    forward_names = [
        name for name in evaluated
        if re.search(r"s[2-9],?1$", name.lower().replace(" ", ""))
        or any(token in name.lower() for token in ("forward", "output", "through"))
    ]
    isolation_names = [
        name for name in evaluated
        if any(token in name.lower() for token in ("isolation", "s23", "s24", "s34"))
    ]
    result: dict[str, Any] = {
        "type": network.get("type", network.get("network_type")),
        "sparameters": evaluated,
        "forward_paths": forward_names,
        "isolation_paths": isolation_names,
        "dc_continuity": network.get("dc_continuity"),
        "dc_resistance_ohm": network.get("dc_resistance_ohm"),
        "choke_isolation_db": network.get("choke_isolation_db"),
    }
    if forward_names:
        magnitudes = [evaluated[name]["magnitude_db"] for name in forward_names]
        common = min(len(values) for values in magnitudes)
        balance = [max(values[index] for values in magnitudes) - min(values[index] for values in magnitudes) for index in range(common)]
        result["amplitude_balance_max_db"] = max(balance, default=None)
        phases = []
        for name in forward_names:
            raw = next((trace for candidate, trace in trace_items if str(candidate) == name), None)
            if raw is not None:
                normalised = _normalise_trace(raw)
                phases.append(_phase_deg(normalised["real"][:common], normalised["imag"][:common]))
        phase_balance = []
        if len(phases) >= 2:
            phase_balance = [
                max(circular_error(values[index], phases[0][index]) for values in phases[1:])
                for index in range(common)
            ]
        result["phase_balance_max_deg"] = max(phase_balance, default=None)
    if isolation_names:
        result["isolation_max_db"] = max(
            max(evaluated[name]["magnitude_db"], default=-300.0) for name in isolation_names
        )
    result["status"] = "validated" if evaluated or any(value is not None for value in (result["dc_continuity"], result["dc_resistance_ohm"], result["choke_isolation_db"])) else "needs_validation"
    return result

def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    evaluation = payload.get("evaluation", {})
    frequency_spec = payload.get("frequency_spec", {})
    profile = str(payload.get("profile", "")).lower()
    band = None
    if "start" in frequency_spec and "stop" in frequency_spec:
        band = (_as_float(frequency_spec["start"]), _as_float(frequency_spec["stop"]))
    threshold = _as_float(evaluation.get("s11_threshold_db", -10.0))
    if "xdata" in payload and "ydata" in payload:
        sparameters = {payload.get("treepath", "trace"): payload}
    elif isinstance(payload.get("data"), list):
        # CST read_1d_result exports rows under `data` and names the item
        # with `tree_path`; accept that native format directly.
        sparameters = {payload.get("tree_path", "trace"): payload}
    else:
        sparameters = payload.get("sparameters", {})
    if isinstance(sparameters, list):
        sparameter_items = [(item.get("name", str(index)), item) for index, item in enumerate(sparameters)]
    else:
        sparameter_items = list(sparameters.items())
    antenna_matching = profile == "radiation_antenna"
    result: dict[str, Any] = {
        "profile": payload.get("profile"),
        "acceptance_policy": (
            "antenna_input_s11_only"
            if antenna_matching
            else "profile_specific_complex_response"
        ),
        "sparameters": {
            name: evaluate_sparameter(
                trace,
                threshold if antenna_matching and _is_input_match_name(name) else None,
                band,
            )
            for name, trace in sparameter_items
        },
    }
    matching_issue = profile == "radiation_antenna" and not any(
        _is_input_match_name(name) for name, _ in sparameter_items
    )
    if matching_issue:
        result["matching_warning"] = "No recognizable input S11 trace was supplied; matching bandwidth was not evaluated."
    states = payload.get("state_responses")
    if states:
        result["codebook"] = evaluate_codebook(
            states,
            band,
            _as_float(evaluation.get("phase_tolerance_deg", 10.0)),
            _as_float(evaluation.get("amplitude_spread_db", 1.5)),
        )
    farfield = payload.get("farfield")
    if farfield:
        phase_keys = ("theta", "theta_deg", "angle", "angles")
        complex_keys = ("complex", "real", "imag", "e_theta", "e_phi")
        component_map = farfield.get("components") if isinstance(farfield, dict) else None
        has_phase_trace = isinstance(farfield, dict) and any(
            isinstance(farfield.get(name), list) for name in phase_keys
        ) and (
            any(isinstance(farfield.get(name), list) for name in complex_keys)
            or isinstance(component_map, dict)
        )
        if has_phase_trace:
            requested_component = evaluation.get("farfield_phase_component")
            selected_payload, selected_component = _select_farfield_component(
                farfield,
                str(requested_component) if requested_component is not None else None,
                str(evaluation.get("ludwig3_reference_axis", "")) or None,
            )
            if selected_payload is None:
                result["farfield_phase"] = {
                    "status": "needs_validation",
                    "component": selected_component,
                    "reason": "requested far-field polarization component was not exported",
                }
            else:
                result["farfield_phase"] = evaluate_farfield_phase(
                    selected_payload,
                    _as_float(evaluation.get("farfield_magnitude_floor_db", -30.0)),
                    component=selected_component,
                )
    farfield_gain = payload.get("farfield_gain")
    if farfield_gain is None and isinstance(farfield, dict):
        gain_names = ("realized_gain_dbi", "gain_dbi", "directivity_dbi", "gain", "directivity")
        if any(isinstance(farfield.get(name), list) for name in gain_names):
            farfield_gain = farfield
        else:
            farfield_gain = _farfield_grid_cut(
                farfield,
                _as_float(evaluation.get("farfield_phi_cut_deg", 0.0)),
            )
    if farfield_gain:
        result["farfield_gain"] = evaluate_farfield_gain(
            farfield_gain,
            _as_float(evaluation["gain_target_dbi"]) if evaluation.get("gain_target_dbi") is not None else None,
        )
    network = payload.get("network")
    if network:
        result["network"] = evaluate_network(network)
    if profile == "absorber":
        trace_map = {str(name): trace for name, trace in sparameter_items}
        reflection = next((value for name, value in trace_map.items() if _is_input_match_name(name)), None)
        transmission = next((value for name, value in trace_map.items() if _is_transmission_name(name)), None)
        if reflection is not None and transmission is not None:
            result["absorption"] = evaluate_absorption(reflection, transmission)
        else:
            result["absorption_status"] = "needs_reflection_and_transmission_traces"
    evidence_present = bool(sparameter_items or states or farfield or farfield_gain)
    sparameter_issue = any(
        isinstance(item, dict) and item.get("status") == "failed_constraints"
        for item in result["sparameters"].values()
    )
    smoothness_issue = any(
        isinstance(item.get("smoothness"), dict) and item["smoothness"].get("spike_count", 0) > 0
        for item in result["sparameters"].values()
    )
    codebook_issue = isinstance(result.get("codebook"), dict) and result["codebook"].get("status") != "validated"
    network_issue = isinstance(result.get("network"), dict) and result["network"].get("status") != "validated"
    result["status"] = (
        "needs_refinement"
        if smoothness_issue
        else "needs_validation"
        if matching_issue
        else "failed_constraints"
        if sparameter_issue or codebook_issue or network_issue
        else "validated"
    ) if evidence_present else "needs_validation"
    return result

def _raw_trace_from_export(payload: dict[str, Any], treepath: str | None = None) -> dict[str, Any] | None:
    if str(payload.get("format", "")).lower() == "farfield_grid":
        return None
    if isinstance(payload.get("data"), list):
        return {
            "tree_path": payload.get("tree_path", payload.get("treepath", "trace")),
            "data": payload["data"],
        }
    if isinstance(payload.get("xdata"), list) and isinstance(payload.get("ydata"), list):
        return payload
    traces = payload.get("sparameters")
    if isinstance(traces, dict) and traces:
        if treepath and treepath in traces:
            return traces[treepath]
        return next(iter(traces.values()))
    if isinstance(traces, list) and traces:
        return traces[0]
    return None


def evaluate_study_directory(study_dir: Path, output: Path) -> dict[str, Any]:
    plan_path = study_dir / "study_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    evaluation = dict(plan.get("evaluation", {}))
    profile = plan.get("profile")
    frequency_spec = plan.get("frequency_spec", {})
    state_root = study_dir / "states"
    raw_export_paths = sorted(state_root.glob("**/exports/*.json")) if state_root.exists() else []
    export_paths: list[Path] = []
    state_dirs = sorted(path for path in state_root.iterdir() if path.is_dir()) if state_root.exists() else []
    for state_dir in state_dirs:
        manifest_path = state_dir / "state.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    manifest = loaded
            except (OSError, json.JSONDecodeError):
                manifest = {}
        selected = manifest.get("selected_exports")
        if isinstance(selected, list) and selected:
            for value in selected:
                path = Path(str(value))
                if not path.is_absolute():
                    path = state_dir / path
                export_paths.append(path)
        else:
            export_paths.extend(sorted(state_dir.glob("**/exports/*.json")))
    export_paths = list(dict.fromkeys(export_paths))
    state_results: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    state_records: dict[str, dict[str, Any]] = {}
    status_rank = {
        "validated": 0,
        "needs_validation": 1,
        "failed_constraints": 2,
        "needs_refinement": 3,
        "failed_identity": 4,
        "failed_parse": 5,
    }
    for export_path in export_paths:
        state_id = export_path.relative_to(state_root).parts[0] if state_root.exists() else "unknown"
        record = state_records.setdefault(
            state_id,
            {
                "state_id": state_id,
                "exports": [],
                "status": "validated",
                "parameter_identities": [],
                "result": {"profile": profile, "frequency_spec": frequency_spec, "sparameters": {}},
            },
        )
        record["exports"].append(str(export_path))
        try:
            raw = json.loads(export_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("export is not a JSON object")
            manifest_path = state_root / state_id / "state.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            expected_parameters = (
                manifest.get("expected_parameter_combination", manifest.get("parameters", {}))
                if isinstance(manifest, dict) else {}
            )
            identity = _parameter_identity(expected_parameters, raw.get("parameter_combination"))
            record["parameter_identities"].append({"export": str(export_path), **identity})
            payload = {
                "profile": profile,
                "frequency_spec": frequency_spec,
                "evaluation": evaluation,
                **raw,
            }
            if str(raw.get("format", "")).lower() == "farfield_grid":
                payload = {
                    "profile": profile,
                    "frequency_spec": frequency_spec,
                    "evaluation": evaluation,
                    "farfield": raw,
                }
            result = evaluate_payload(payload)
            if identity["status"] not in {"matched", "not_checked"}:
                outer_status = "failed_identity"
            else:
                outer_status = result.get("status", "needs_validation")
            if status_rank.get(outer_status, 99) > status_rank.get(record["status"], -1):
                record["status"] = outer_status
            merged = record["result"]
            for key, value in result.items():
                if key == "sparameters" and isinstance(value, dict):
                    merged.setdefault("sparameters", {}).update(value)
                elif key == "status":
                    continue
                elif value not in (None, {}, []):
                    merged[key] = value
            trace = _raw_trace_from_export(raw, evaluation.get("codebook_treepath"))
            if trace is not None and outer_status != "failed_identity":
                grouped.setdefault(state_id, []).append((str(export_path), trace))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            record["status"] = "failed_parse"
            record["parameter_identities"].append({"export": str(export_path), "status": "parse_error"})
            record["error"] = str(exc)

    for state_id in sorted(state_records):
        record = state_records[state_id]
        identities = record.pop("parameter_identities")
        statuses = [str(item.get("status")) for item in identities]
        if statuses and all(status in {"matched", "not_checked"} for status in statuses):
            identity_status = "matched" if "matched" in statuses else "not_checked"
        else:
            identity_status = "mismatch"
        record["parameter_identity"] = {"status": identity_status, "exports": identities}
        record["result"]["status"] = record["status"]
        state_results.append(record)
    codebook_states: list[dict[str, Any]] = []
    for state_id in sorted(grouped):
        _, trace = grouped[state_id][0]
        codebook_states.append({"id": state_id, **trace})
    aggregate: dict[str, Any] = {}
    if len(codebook_states) >= 2:
        band = None
        if "start" in frequency_spec and "stop" in frequency_spec:
            band = (_as_float(frequency_spec["start"]), _as_float(frequency_spec["stop"]))
        aggregate["codebook"] = evaluate_codebook(
            codebook_states,
            band,
            _as_float(evaluation.get("phase_tolerance_deg", 10.0)),
            _as_float(evaluation.get("amplitude_spread_db", 1.5)),
        )
    refinement_requests: list[dict[str, Any]] = []
    for state in state_results:
        sparameters = state.get("result", {}).get("sparameters", {})
        if not isinstance(sparameters, dict):
            continue
        for treepath, metrics in sparameters.items():
            smoothness = metrics.get("smoothness", {}) if isinstance(metrics, dict) else {}
            request = smoothness.get("refinement_request") if isinstance(smoothness, dict) else None
            if isinstance(request, dict):
                refinement_requests.append(
                    {
                        "state_id": state.get("state_id"),
                        "treepath": treepath,
                        "source_exports": state.get("exports", []),
                        **request,
                    }
                )
    failed_count = sum(1 for item in state_results if item.get("status") not in {None, "validated", "success"})
    aggregate_issue = any(
        isinstance(value, dict) and value.get("status") != "validated"
        for value in aggregate.values()
    )
    result = {
        "status": "validated" if state_results and failed_count == 0 and not aggregate_issue else ("partial" if state_results else "needs_validation"),
        "study_dir": str(study_dir),
        "export_count": len(export_paths),
        "raw_export_count": len(raw_export_paths),
        "state_result_count": len(state_results),
        "failed_count": failed_count,
        "states": state_results,
        "refinement_requests": refinement_requests,
        **aggregate,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate-results")
    evaluate.add_argument("--input", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    study = subparsers.add_parser("evaluate-study")
    study.add_argument("--study-dir", required=True, type=Path)
    study.add_argument("--output", required=True, type=Path)
    compare = subparsers.add_parser("compare-backends")
    compare.add_argument("--reference", required=True, type=Path)
    compare.add_argument("--candidate", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument("--complex-abs-tolerance", type=float, default=1e-3)
    compare.add_argument("--magnitude-db-tolerance", type=float, default=0.02)
    compare.add_argument("--phase-deg-tolerance", type=float, default=0.1)
    compare.add_argument("--frequency-tolerance", type=float, default=1e-9)
    args = parser.parse_args()
    if args.command == "evaluate-results":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = evaluate_payload(payload)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "success", "output": str(args.output)}, ensure_ascii=False))
        return 0
    if args.command == "evaluate-study":
        result = evaluate_study_directory(args.study_dir, args.output)
        print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
        return 0 if result["status"] in {"validated", "needs_validation"} else 1
    if args.command == "compare-backends":
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        result = compare_backend_exports(
            reference,
            candidate,
            complex_abs_tolerance=args.complex_abs_tolerance,
            magnitude_db_tolerance=args.magnitude_db_tolerance,
            phase_deg_tolerance=args.phase_deg_tolerance,
            frequency_tolerance=args.frequency_tolerance,
        )
        result["reference"] = str(args.reference)
        result["candidate"] = str(args.candidate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
        return 0 if result["within_tolerance"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
