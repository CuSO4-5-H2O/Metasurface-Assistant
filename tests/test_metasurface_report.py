import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("metasurface_report", ROOT / "scripts" / "metasurface_report.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class MetasurfaceReportTests(unittest.TestCase):
    def test_level_deltas_compare_adjacent_layers(self):
        result = MODULE.level_deltas(
            [
                {"name": "device", "metrics": {"s21_db": -1.0, "gain_dbi": 2.0}},
                {"name": "bias", "metrics": {"s21_db": -2.0, "gain_dbi": 1.5}},
            ]
        )
        self.assertEqual(result[1]["relative_to"], "device")
        self.assertEqual(result[1]["delta"]["s21_db"], -1.0)

    def test_candidate_comparison_selects_maximizing_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text('{"objectives": {"bandwidth": 1.0}}', encoding="utf-8")
            candidate.write_text('{"objectives": {"bandwidth": 2.0}}', encoding="utf-8")
            result = MODULE.compare_candidates(baseline, [candidate], {"bandwidth": "maximize"})
            self.assertEqual(result["best_candidate_id"], "candidate")



    def test_build_report_includes_failure_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "states" / "00"
            state_dir.mkdir(parents=True)
            (root / "study_plan.json").write_text(
                '{"profile": "reflective_programmable", "backend": "isolated"}',
                encoding="utf-8",
            )
            (state_dir / "failure.log").write_text(
                '{"status": "aborted_timeout", "stage": "run-experiment"}',
                encoding="utf-8",
            )
            (root / "study_summary.json").write_text(
                '{"states": [{"state_id": "00", "status": "aborted_timeout", "failure_log": "states/00/failure.log"}]}',
                encoding="utf-8",
            )
            report = MODULE.build_report(root, root / "study_report.json")
            self.assertEqual(len(report["failed_cases"]), 1)
            self.assertEqual(report["failed_cases"][0]["status"], "aborted_timeout")

    def test_build_report_preserves_classification_and_remaining_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "study_plan.json").write_text(
                '{"profile": "reflective_programmable", '
                '"classification": "periodic_floquet_unit", '
                '"radiation_antenna_gate": {"status": "not_satisfied"}, '
                '"preflight_gates": {"status": "blocked"}, '
                '"optimization_allowed": false, '
                '"remaining_validation": ["run finite antenna"]}',
                encoding="utf-8",
            )
            (root / "study_summary.json").write_text(
                '{"states": [], "smoke_gates": {"status": "blocked"}}',
                encoding="utf-8",
            )
            (root / "study_evaluation.json").write_text(
                '{"status": "partial", "states": []}',
                encoding="utf-8",
            )
            report = MODULE.build_report(root, root / "study_report.json")
            self.assertEqual(report["classification"], "periodic_floquet_unit")
            self.assertEqual(report["radiation_antenna_gate"]["status"], "not_satisfied")
            self.assertEqual(report["preflight_gates"]["status"], "blocked")
            self.assertFalse(report["optimization_allowed"])
            self.assertEqual(report["smoke_gates"]["status"], "blocked")
            self.assertIn("run finite antenna", report["remaining_validation"])

    def test_report_includes_codebook_sources_thresholds_and_networks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "states" / "00" / "projects" / "working.cst"
            project.parent.mkdir(parents=True)
            project.write_text("project", encoding="utf-8")
            (root / "study_plan.json").write_text(
                json.dumps({
                    "profile": "reflective_programmable",
                    "backend": "isolated",
                    "cases": [{
                        "state_id": "00",
                        "bits": "00",
                        "parameters": {"C": 0.9},
                        "device_model": {"kind": "rlc_series", "R": 0.3, "L": 0.4, "C": 0.9},
                        "project_path": str(project),
                    }],
                    "acceptance_thresholds": {
                        "phase_tolerance_deg": {"value": 10.0, "source": "paper DOI"}
                    },
                    "device_evidence": [{"source": "paper DOI", "claim": "device model"}],
                    "feed_network": {"status": "validated"},
                    "bias_network": {"status": "validated"},
                }),
                encoding="utf-8",
            )
            report = MODULE.build_report(root, root / "study_report.json")

        self.assertEqual(report["state_codebook"][0]["parameters"], {"C": 0.9})
        self.assertEqual(report["parameter_sources"][0]["source"], "paper DOI")
        self.assertEqual(report["acceptance_thresholds"]["phase_tolerance_deg"]["source"], "paper DOI")
        self.assertEqual(report["feed_network"]["status"], "validated")
        self.assertTrue(report["paths"]["projects"][0]["exists"])
        self.assertEqual(report["remaining_risks"], report["remaining_validation"])

    def test_best_structure_uses_runtime_best_when_candidate_files_are_absent(self):
        summary = {"campaign": {"best_result": {"value": 0.42, "params": {"w": 3.2}}}}
        result = MODULE.best_structure_summary({"best_candidate_id": None}, summary)

        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["parameters"], {"w": 3.2})
        self.assertEqual(result["objectives"], {"value": 0.42})

if __name__ == "__main__":
    unittest.main()
