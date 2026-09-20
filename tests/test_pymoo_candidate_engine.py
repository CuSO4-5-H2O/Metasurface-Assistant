import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pymoo_candidate_engine", ROOT / "scripts" / "pymoo_candidate_engine.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PymooCandidateEngineTests(unittest.TestCase):
    def test_variable_and_constraint_schema_is_normalised(self):
        variables = MODULE._normalise_variables(
            {
                "parameters": {
                    "length": {"type": "float", "min": 1.0, "max": 2.0},
                    "state": {"type": "categorical", "values": ["00", "01", "10", "11"]},
                    "enabled": {"type": "binary"},
                }
            }
        )
        self.assertEqual([item["type"] for item in variables], ["float", "categorical", "binary"])
        self.assertEqual(variables[1]["max"], 3)
        constraints = MODULE._normalise_constraints(
            {"constraints": [{"parameter": "length", "min": 1.1, "max": 1.9}]}
        )
        self.assertEqual(constraints[0]["parameter"], "length")

    def test_array_state_matrix_expands_to_categorical_variables(self):
        variables, spec = MODULE._array_state_variables(
            {
                "array_state": {
                    "rows": 2,
                    "cols": 2,
                    "values": ["00", "01", "10", "11"],
                }
            }
        )
        self.assertEqual(len(variables), 4)
        self.assertEqual(variables[-1]["name"], "state[1,1]")
        self.assertEqual(spec["values"], ["00", "01", "10", "11"])

    def test_constraint_violation_handles_parameter_and_sum(self):
        candidate = {"a": 0.2, "b": 0.7}
        constraints = [
            {"parameter": "a", "min": 0.3},
            {"sum": [{"parameter": "a"}, {"parameter": "b"}], "max": 0.8},
        ]
        self.assertAlmostEqual(MODULE._constraint_violation(candidate, constraints), 0.2)

    def test_available_mixed_variables_are_legal(self):
        if not MODULE.pymoo_status()["available"]:
            self.skipTest("host runtime has no pymoo")
        result = MODULE.generate_candidates(
            {
                "parameters": {
                    "length": {"type": "float", "min": 1.0, "max": 2.0},
                    "state": {"type": "categorical", "values": ["00", "01", "10", "11"]},
                    "enabled": {"type": "binary"},
                },
                "candidate_count": 4,
                "seed": 11,
            }
        )
        self.assertEqual(result["status"], "candidates_ready")
        self.assertEqual(len(result["candidates"]), 4)
        for row in result["candidates"]:
            self.assertGreaterEqual(row["parameters"]["length"], 1.0)
            self.assertLessEqual(row["parameters"]["length"], 2.0)
            self.assertIn(row["parameters"]["state"], ["00", "01", "10", "11"])
            self.assertIn(row["parameters"]["enabled"], [0, 1])
            self.assertEqual(row["status"], "pending_cst_evaluation")

    def test_unavailable_dependency_is_explicit(self):
        status = MODULE.pymoo_status()
        if status["available"]:
            self.skipTest("host runtime already provides pymoo")
        with self.assertRaises(MODULE.PymooUnavailable):
            MODULE.generate_candidates(
                {
                    "parameters": {"x": {"type": "float", "min": 0.0, "max": 1.0}},
                    "candidate_count": 2,
                }
            )
