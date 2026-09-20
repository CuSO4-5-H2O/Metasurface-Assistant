import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "metasurface_metrics.py"
SPEC = importlib.util.spec_from_file_location("metasurface_metrics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class MetasurfaceMetricTests(unittest.TestCase):
    def test_disconnected_bands_are_not_merged(self):
        result = MODULE.evaluate_sparameter(
            {
                "frequency": [1, 2, 3, 4, 5, 6],
                "db": [-12, -11, -8, -11, -12, -12],
            },
            threshold_db=-10,
        )
        self.assertEqual(len(result["passing_intervals"]), 2)
        self.assertEqual(result["widest_interval"]["start"], 4)

    def test_circular_phase_error_wraps_at_180(self):
        self.assertAlmostEqual(MODULE.circular_error(-179, 180), 1)

    def test_two_bit_codebook(self):
        states = []
        for name, phase in [("00", 0), ("01", 90), ("10", 180), ("11", -90)]:
            states.append(
                {
                    "id": name,
                    "frequency": [10, 11],
                    "complex": [
                        [math.cos(math.radians(phase)), math.sin(math.radians(phase))],
                        [math.cos(math.radians(phase)), math.sin(math.radians(phase))],
                    ],
                }
            )
        result = MODULE.evaluate_codebook(states)
        self.assertEqual(result["status"], "validated")
        self.assertLessEqual(result["max_phase_error_deg"], 1e-9)

    def test_codebook_constraint_failure_is_not_validated(self):
        states = []
        for name in ("00", "01"):
            states.append(
                {
                    "id": name,
                    "frequency": [10, 11],
                    "complex": [[1.0, 0.0], [1.0, 0.0]],
                }
            )
        result = MODULE.evaluate_codebook(states)
        self.assertEqual(result["status"], "failed_constraints")
        self.assertFalse(result["meets_constraints"])
    def test_codebook_bandwidth_does_not_bridge_failed_sample(self):
        states = []
        for name, phase in [("00", 0), ("01", 180)]:
            phases = [phase, phase, phase + (35 if name == "01" else 0), phase]
            states.append(
                {
                    "id": name,
                    "frequency": [10, 11, 12, 13],
                    "complex": [
                        [math.cos(math.radians(value)), math.sin(math.radians(value))]
                        for value in phases
                    ],
                }
            )
        result = MODULE.evaluate_codebook(states, phase_tolerance_deg=10)
        self.assertEqual(result["valid_bandwidth"], 1)
        self.assertEqual(len(result["valid_intervals"]), 2)

    def test_farfield_phase_unwraps_and_masks_null(self):
        phases = [170, 179, -179, -170]
        magnitudes = [1.0, 0.001, 1.0, 1.0]
        payload = {
            "theta": [-1, 0, 1, 2],
            "complex": [
                [magnitude * math.cos(math.radians(phase)), magnitude * math.sin(math.radians(phase))]
                for magnitude, phase in zip(magnitudes, phases)
            ],
        }
        result = MODULE.evaluate_farfield_phase(payload, magnitude_floor_db=-20)
        self.assertEqual(result["valid_points"], 3)
        self.assertLess(result["phase_range_deg"], 30)

    def test_farfield_e_theta_component_is_selected_and_sorted(self):
        payload = {
            "profile": "radiation_antenna",
            "evaluation": {
                "farfield_phase_component": "E_theta",
                "farfield_magnitude_floor_db": -20,
            },
            "farfield": {
                "theta": [20, 0, 10],
                "e_theta": [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]],
                "e_phi": [[1.0, 0.0], [0.0, 1.0], [0.5, -0.5]],
            },
        }
        result = MODULE.evaluate_payload(payload)
        self.assertEqual(result["farfield_phase"]["status"], "validated")
        self.assertEqual(result["farfield_phase"]["component"], "e_theta")
        self.assertEqual(result["farfield_phase"]["theta_start"], 0)
        self.assertEqual(result["farfield_phase"]["theta_stop"], 20)

    def test_runtime_complex_cut_payload_is_evaluated_directly(self):
        payload = {
            "profile": "reflective_programmable",
            "evaluation": {
                "farfield_phase_component": "E_phi",
                "farfield_magnitude_floor_db": -30,
            },
            "farfield": {
                "format": "farfield_complex_cut",
                "frequency_ghz": 5.0,
                "fixed_phi_deg": 0.0,
                "reference_plane": "project_farfield_monitor",
                "polarization_basis": "spherical_linear",
                "theta_deg": [0.0, 10.0, 20.0],
                "e_theta": [
                    {"real": 1.0, "imag": 0.0},
                    {"real": 1.0, "imag": 0.0},
                    {"real": 1.0, "imag": 0.0},
                ],
                "e_phi": [
                    {"real": 0.0, "imag": 1.0},
                    {"real": -0.1, "imag": 0.99},
                    {"real": -0.2, "imag": 0.98},
                ],
            },
        }
        result = MODULE.evaluate_payload(payload)
        phase = result["farfield_phase"]
        self.assertEqual(phase["status"], "validated")
        self.assertEqual(phase["component"], "e_phi")
        self.assertEqual(phase["frequency_ghz"], 5.0)
        self.assertEqual(phase["reference_plane"], "project_farfield_monitor")

    def test_ludwig3_co_requires_axis_and_uses_both_spherical_components(self):
        farfield = {
            "theta_deg": [0.0, 10.0],
            "phi_deg": [0.0, 90.0],
            "reference_plane": "project_farfield_monitor",
            "e_theta": [[1.0, 0.0], [0.0, 0.0]],
            "e_phi": [[0.0, 0.0], [-1.0, 0.0]],
        }
        with_axis = MODULE.evaluate_payload(
            {
                "profile": "reflective_programmable",
                "evaluation": {
                    "farfield_phase_component": "Ludwig-3 co",
                    "ludwig3_reference_axis": "x",
                },
                "farfield": farfield,
            }
        )
        self.assertEqual(with_axis["farfield_phase"]["status"], "validated")
        self.assertEqual(with_axis["farfield_phase"]["component"], "ludwig3_co")
        self.assertAlmostEqual(with_axis["farfield_phase"]["phase_range_deg"], 0.0)

        without_axis = MODULE.evaluate_payload(
            {
                "profile": "reflective_programmable",
                "evaluation": {"farfield_phase_component": "Ludwig-3 co"},
                "farfield": farfield,
            }
        )
        self.assertEqual(
            without_axis["farfield_phase"]["status"], "needs_validation"
        )

    def test_missing_requested_farfield_component_is_not_validated(self):
        payload = {
            "evaluation": {"farfield_phase_component": "E_phi"},
            "farfield": {
                "theta": [0, 10],
                "e_theta": [[1.0, 0.0], [1.0, 0.0]],
            },
        }
        result = MODULE.evaluate_payload(payload)
        self.assertEqual(result["farfield_phase"]["status"], "needs_validation")

    def test_farfield_gain_and_absorption(self):
        gain = MODULE.evaluate_farfield_gain(
            {"theta": [-30, 0, 30], "gain_dbi": [5, 10, 4], "cross_polar_dbi": [-25, -30, -24]},
            absolute_target_dbi=9,
        )
        self.assertEqual(gain["peak_gain_dbi"], 10)
        self.assertTrue(gain["meets_absolute_target"])
        absorption = MODULE.evaluate_absorption(
            {"frequency": [1], "complex": [[0.1, 0.0]]},
            {"frequency": [1], "complex": [[0.1, 0.0]]},
        )
        self.assertAlmostEqual(absorption["peak_absorption"], 0.98)

    def test_parameter_identity_rejects_mismatch(self):
        result = MODULE._parameter_identity({"C": 1.0}, {"C": 1.2})
        self.assertEqual(result["status"], "mismatch")

    def test_parameter_identity_requires_exact_snapshot(self):
        result = MODULE._parameter_identity({"C": 1.0}, {"C": 1.0, "phaseY": 3.6})
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["unexpected"], ["phaseY"])

    def test_backend_comparison_uses_circular_phase_and_exact_identity(self):
        reference = {
            "xdata": [5.0, 5.1],
            "ydata": [complex(-1.0, 0.001).__str__(), complex(0.5, 0.5).__str__()],
            "parameter_combination": {"C": 1.0, "state": 0.0},
        }
        candidate = {
            "data": [[5.0, "(-1-0.001j)"], [5.1, "(0.5001+0.4999j)"]],
            "parameter_combination": {"C": 1.0, "state": 0.0},
        }
        result = MODULE.compare_backend_exports(
            reference,
            candidate,
            complex_abs_tolerance=0.003,
            magnitude_db_tolerance=0.01,
            phase_deg_tolerance=0.2,
        )
        self.assertEqual(result["status"], "validated")
        self.assertTrue(result["frequency_axis_matches"])
        self.assertEqual(result["parameter_identity"]["status"], "matched")
        self.assertLess(result["max_phase_difference_deg"], 0.2)

    def test_backend_comparison_rejects_parameter_mismatch(self):
        reference = {
            "xdata": [5.0],
            "ydata": ["(1+0j)"],
            "parameter_combination": {"C": 1.0},
        }
        candidate = {
            "xdata": [5.0],
            "ydata": ["(1+0j)"],
            "parameter_combination": {"C": 2.0},
        }
        result = MODULE.compare_backend_exports(reference, candidate)
        self.assertEqual(result["status"], "failed_consistency")
        self.assertEqual(result["parameter_identity"]["status"], "mismatch")

    def test_spike_is_flagged(self):
        result = MODULE.smoothness_metrics([1, 2, 3, 4, 5], [-10, -11, -2, -11, -10])
        self.assertGreaterEqual(result["spike_count"], 1)



    def test_payload_spike_requires_refinement(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "sparameters": {
                    "S11": {
                        "frequency": [1, 2, 3, 4, 5],
                        "complex": [[0.1, 0.0], [0.1, 0.0], [0.9, 0.0], [0.1, 0.0], [0.1, 0.0]],
                    }
                },
            }
        )
        self.assertEqual(result["status"], "needs_refinement")

    def test_study_evaluation_aggregates_spike_refinement_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "states" / "00"
            exports = state / "exports"
            exports.mkdir(parents=True)
            (root / "study_plan.json").write_text(json.dumps({
                "profile": "radiation_antenna",
                "frequency_spec": {"start": 1.0, "stop": 5.0},
                "evaluation": {"sparameter_treepaths": ["S1,1"]},
            }), encoding="utf-8")
            (state / "state.json").write_text(json.dumps({
                "state_id": "00",
                "parameters": {"x": 1.0},
                "selected_exports": ["exports/s11.json"],
            }), encoding="utf-8")
            (exports / "s11.json").write_text(json.dumps({
                "treepath": "S1,1",
                "xdata": [1, 2, 3, 4, 5],
                "ydata": [[0.3, 0], [0.28, 0], [0.9, 0], [0.28, 0], [0.3, 0]],
                "parameter_combination": {"x": 1.0},
            }), encoding="utf-8")
            result = MODULE.evaluate_study_directory(root, root / "study_evaluation.json")

        self.assertEqual(len(result["refinement_requests"]), 1)
        self.assertEqual(result["refinement_requests"][0]["state_id"], "00")
        self.assertEqual(result["refinement_requests"][0]["treepath"], "S1,1")
    def test_non_antenna_profile_does_not_use_s11_threshold(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "transmissive_programmable",
                "evaluation": {"s11_threshold_db": -10},
                "sparameters": {
                    "S21": {
                        "frequency": [1, 2],
                        "complex": [[0.1, 0.0], [0.2, 0.0]],
                    }
                },
            }
        )
        trace = result["sparameters"]["S21"]
        self.assertIsNone(trace["threshold_db"])
        self.assertIsNone(trace["meets_threshold"])

    def test_antenna_threshold_is_limited_to_input_match(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "evaluation": {"s11_threshold_db": -10},
                "sparameters": {
                    "S11": {
                        "frequency": [1, 2],
                        "complex": [[0.1, 0.0], [0.1, 0.0]],
                    },
                    "S21": {
                        "frequency": [1, 2],
                        "complex": [[0.7, 0.0], [0.7, 0.0]],
                    },
                },
            }
        )
        self.assertEqual(result["sparameters"]["S11"]["threshold_db"], -10.0)
        self.assertIsNone(result["sparameters"]["S21"]["threshold_db"])


    def test_cst_native_data_export_is_evaluated_as_a_trace(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "tree_path": "1D Results\\S-Parameters\\SZmax(1),Zmax(1)",
                "data": [[
                    5.0, "(0.1+0.0j)", "(50+0j)"
                ], [
                    5.1, "(0.2+0.0j)", "(50+0j)"
                ]],
            }
        )

        self.assertIn("1D Results\\S-Parameters\\SZmax(1),Zmax(1)", result["sparameters"])
        self.assertEqual(result["sparameters"]["1D Results\\S-Parameters\\SZmax(1),Zmax(1)"]["threshold_db"], -10.0)

    def test_missing_result_evidence_is_not_validated(self):
        result = MODULE.evaluate_payload({"profile": "radiation_antenna"})
        self.assertEqual(result["status"], "needs_validation")

    def test_cst_farfield_grid_is_cut_into_gain_metrics(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "evaluation": {"farfield_phi_cut_deg": 0.0},
                "farfield": {
                    "format": "farfield_grid",
                    "quantity": "Realized Gain",
                    "frequency_ghz": 5.5,
                    "xpositions": [0, 10, 20, 30, 40],
                    "ypositions": [0, 90, 180],
                    "data": [[10, 8, 3, -5, -10], [1, 1, 1, 1, 1], [2, 2, 2, 2, 2]],
                },
            }
        )
        self.assertEqual(result["status"], "needs_validation")
        self.assertEqual(result["farfield_gain"]["peak_gain_dbi"], 10)
        self.assertEqual(result["farfield_gain"]["beamwidth_3db_deg"], 10)
        self.assertEqual(result["farfield_gain"]["sidelobe_max_dbi"], 3)
    def test_feed_network_reports_balance_and_isolation(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "reflective_programmable",
                "network": {
                    "type": "feed",
                    "sparameters": {
                        "S21": {"frequency": [5, 6], "complex": [[0.5, 0.0], [0.5, 0.0]]},
                        "S31": {"frequency": [5, 6], "complex": [[0.45, 0.0], [0.45, 0.0]]},
                        "S23": {"frequency": [5, 6], "complex": [[0.01, 0.0], [0.01, 0.0]]},
                    },
                    "dc_continuity": True,
                },
            }
        )
        self.assertEqual(result["network"]["status"], "validated")
        self.assertEqual(result["network"]["forward_paths"], ["S21", "S31"])
        self.assertGreater(result["network"]["amplitude_balance_max_db"], 0)
        self.assertTrue(result["network"]["dc_continuity"])
    def test_antenna_s11_failure_propagates_to_status(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "frequency_spec": {"start": 5, "stop": 6},
                "evaluation": {"s11_threshold_db": -10},
                "sparameters": {
                    "S11": {
                        "frequency": [5, 5.5, 6],
                        "complex": [[0.9, 0.0], [0.8, 0.0], [0.9, 0.0]],
                    }
                },
            }
        )
        self.assertFalse(result["sparameters"]["S11"]["meets_threshold"])
        self.assertEqual(result["sparameters"]["S11"]["status"], "failed_constraints")
        self.assertEqual(result["status"], "failed_constraints")

    def test_antenna_without_input_match_is_needs_validation(self):
        result = MODULE.evaluate_payload(
            {
                "profile": "radiation_antenna",
                "farfield": {
                    "theta": [-10, 0, 10],
                    "gain_dbi": [0, 3, 0],
                },
            }
        )
        self.assertEqual(result["status"], "needs_validation")
        self.assertIn("matching_warning", result)

if __name__ == "__main__":
    unittest.main()
