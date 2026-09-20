import importlib.util
import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("metasurface_array", ROOT / "scripts" / "metasurface_array.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class MetasurfaceArrayTests(unittest.TestCase):
    def test_quantises_n_state_phase_grid(self):
        study = {
            "state_codebook": [
                {"id": "00", "phase_deg": 0},
                {"id": "01", "phase_deg": 90},
                {"id": "10", "phase_deg": 180},
                {"id": "11", "phase_deg": 270},
            ],
            "array": {"phase_deg": [[0, 44, 136, 225]], "rows": 1, "cols": 4},
        }
        result = MODULE.quantize_phase_grid(study)
        self.assertEqual(result["state_matrix"], [["00", "00", "10", "10"]])
        self.assertLessEqual(result["max_quantization_error_deg"], 45)

    def test_generates_direction_phase_gradient(self):
        study = {
            "frequency_spec": {"target": 10, "unit": "GHz"},
            "state_codebook": [
                {"id": "0", "phase_deg": 0},
                {"id": "1", "phase_deg": 180},
            ],
            "array": {
                "rows": 1,
                "cols": 3,
                "dx_mm": 10,
                "dy_mm": 10,
                "target_theta_deg": 30,
                "target_phi_deg": 0,
            },
        }
        phases = MODULE.ideal_phase_grid(study)[0]
        self.assertEqual(len(phases), 3)
        self.assertNotAlmostEqual(phases[0], phases[-1])


if __name__ == "__main__":
    unittest.main()
