import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "run_metasurface_study", ROOT / "scripts" / "run_metasurface_study.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


SOURCE = Path(
    r"E:\Codex\toolkits\CST-Optimization-Workspace\tasks"
    r"\task_018_metasurface_identity_verified\runs\run_001\projects\working.cst"
)


def base_study():
    return {
        "profile": "reflective_programmable",
        "source_project": str(SOURCE),
        "output_dir": r"E:\gpt_articles\.runner_test_output",
        "frequency_spec": {"start": 4.0, "stop": 5.0, "unit": "GHz", "target": 4.5},
        "state_codebook": [
            {"id": "00", "bits": "00", "parameters": {"PathPara": 1.0}},
        ],
        "evaluation": {
            "sparameter_treepaths": [
                r"1D Results\S-Parameters\SZmax(1),Zmax(1)"
            ]
        },
    }


class MetasurfaceStudyRunnerTests(unittest.TestCase):
    def test_enabled_spike_refinement_requires_explicit_mesh_operation(self):
        study = base_study()
        study["spike_refinement"] = {"enabled": True, "mesh_operations": []}
        with self.assertRaisesRegex(ValueError, "requires an explicit mesh operation"):
            MODULE.validate_study(study)

    def test_spike_refinement_rejects_invalid_pass_budget(self):
        study = base_study()
        study["spike_refinement"] = {"enabled": False, "max_passes": 0}
        with self.assertRaisesRegex(ValueError, "must be a positive integer"):
            MODULE.validate_study(study)

    def test_auto_routes_spice_model_to_isolated(self):
        study = base_study()
        study["device_model"] = {
            "kind": "spice",
            "inline_netlist": ".model Dsw D(Is=1e-14)",
        }
        states, backend = MODULE.validate_study(study)
        self.assertEqual(len(states), 1)
        self.assertEqual(backend, "isolated")

    def test_incomplete_rlc_emits_blocking_checklist_instead_of_raising(self):
        study = base_study()
        study["device_model"] = {"kind": "rlc_series", "R": 0.3, "L": 0.4}
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertFalse(plan["execution_allowed"])
        self.assertFalse(plan["optimization_allowed"])
        gate = next(item for item in plan["preflight_gates"]["gates"] if item["name"] == "device_model_completeness")
        self.assertEqual(gate["status"], "blocked")
        self.assertTrue(any("missing: C" in issue for issue in gate["issues"]))
        self.assertTrue(any("device_evidence" in issue for issue in gate["issues"]))

    def test_incomplete_device_model_writes_pending_completion_artifact(self):
        study = base_study()
        study["device_model"] = {"kind": "rlc_series", "R": 0.3}
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            MODULE._write_plan_artifacts(plan)
            completion = json.loads((output / "device-model-completion.json").read_text(encoding="utf-8"))
        self.assertEqual(completion["status"], "pending_device_model")
        self.assertIsNone(completion["rlc_template"]["L"])
        self.assertEqual(completion["state_mapping_template"][0]["id"], "00")

    def test_invalid_rlc_value_remains_a_hard_validation_error(self):
        study = base_study()
        study["device_model"] = {"kind": "rlc_series", "R": -0.3, "L": 0.4, "C": 1.0}
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            with self.assertRaisesRegex(ValueError, "non-negative"):
                MODULE.build_plan(study, Path(directory))

    def test_incomplete_device_model_never_invokes_cst(self):
        study = base_study()
        study["state_backend"] = "isolated"
        study["device_model"] = {"kind": "spice"}
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            with patch.object(MODULE, "_invoke", side_effect=AssertionError("CST must not be called")):
                result = MODULE.run_isolated(study, plan, output)
        self.assertEqual(result["status"], "blocked_preflight")
        self.assertEqual(result["states"], [])

    def test_optimization_plan_contains_probe_and_runtime_copy(self):
        study = base_study()
        study["state_backend"] = "native_sweep"
        study["optimization"] = {
            "parameters": {
                "a": {"min": 0.1, "max": 1.0},
                "b": {"min": 1.0, "max": 2.0},
                "c": {"min": 2.0, "max": 3.0},
                "d": {"min": 3.0, "max": 4.0},
            },
            "max_trials": 3,
            "objective": {"type": "bandwidth", "below_db": -10},
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        request = plan["optimization_request"]
        self.assertTrue(request["probe_required"])
        self.assertIn("working.cst", request["campaign"]["project_path"])
        self.assertEqual(request["campaign"]["direction"], "maximize")
        self.assertEqual(request["study_parameters"]["a"]["type"], "float")



    def test_pymoo_array_state_derives_values_from_codebook(self):
        study = base_study()
        study["optimization"] = {
            "engine": "pymoo",
            "parameters": {"length": {"type": "float", "min": 1.0, "max": 2.0}},
            "array_state": {"rows": 2, "cols": 3},
            "candidate_count": 2,
        }
        study["state_codebook"] = [
            {"id": "00", "parameters": {"PathPara": 0.0}},
            {"id": "01", "parameters": {"PathPara": 1.0}},
        ]
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        request = plan["optimization_request"]["pymoo"]
        self.assertEqual(request["array_state"]["values"], ["00", "01"])
        self.assertEqual(request["array_state"]["rows"], 2)

    def test_mesh_convergence_request_is_validated_and_emitted(self):
        study = base_study()
        study["mesh_convergence"] = {
            "task_path": r"E:\Codex\toolkits\CST-Optimization-Workspace\tasks\task_018_metasurface_identity_verified",
            "mesh_levels": [
                {
                    "label": "coarse",
                    "steps_per_wave_near": 8,
                    "steps_per_wave_far": 8,
                    "steps_per_box_near": 8,
                    "steps_per_box_far": 2,
                },
                {
                    "label": "fine",
                    "steps_per_wave_near": 12,
                    "steps_per_wave_far": 12,
                    "steps_per_box_near": 12,
                    "steps_per_box_far": 4,
                },
            ],
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertEqual(len(plan["mesh_convergence_request"]["mesh_levels"]), 2)
        self.assertEqual(plan["mesh_convergence_request"]["min_levels"], 2)


    def test_invoke_turns_runtime_timeout_into_a_result(self):
        with tempfile.TemporaryDirectory(dir=r"E:\\gpt_articles") as directory:
            args_path = Path(directory) / "run.json"
            args_path.write_text('{"timeout_seconds": 1}', encoding="utf-8")
            expired = subprocess.TimeoutExpired(["cst_runtime"], 1, output="partial", stderr="late")
            with patch.object(MODULE.subprocess, "run", side_effect=expired):
                result = MODULE._invoke(Path(sys.executable), Path(directory), "run-experiment", args_path)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["timeout_seconds"], 31.0)

    def test_ingest_native_manifest_archives_matching_state_exports(self):
        with tempfile.TemporaryDirectory(dir=r"E:\\gpt_articles") as directory:
            root = Path(directory)
            source = root / "working.cst"
            source.write_text("project", encoding="utf-8")
            solved_project = root / "native" / "case_001" / "working.cst"
            solved_project.parent.mkdir(parents=True)
            solved_project.write_text("solved project", encoding="utf-8")
            native_export = root / "native" / "trace.json"
            native_export.write_text(json.dumps({
                "tree_path": "S11",
                "data": [[5.0, "(0.1+0j)", "(50+0j)"]],
                "case_project": str(solved_project),
            }), encoding="utf-8")
            manifest = root / "sweep_manifest.json"
            manifest.write_text(json.dumps({
                "source_project": str(source),
                "completed_cases": 1,
                "failed_cases": 0,
                "cases": [{
                    "case_id": "case_001",
                    "status": "success",
                    "parameters": {"PathPara": 1.0},
                    "result_exports": [{"file": str(native_export)}],
                }],
            }), encoding="utf-8")
            plan = {"source_project": str(source), "cases": [{"state_id": "00", "parameters": {"PathPara": 1.0}}]}
            result = MODULE.ingest_native_manifest({}, plan, root / "study", manifest)

            self.assertEqual(result["status"], "validated")
            archived = root / "study" / "states" / "00" / "exports" / "trace.json"
            self.assertTrue(archived.is_file())
            self.assertEqual(json.loads(archived.read_text(encoding="utf-8"))["parameter_combination"], {"PathPara": 1.0})
            self.assertEqual(result["states"][0]["project_path"], str(solved_project))

    def test_plan_preserves_device_and_network_evidence(self):
        study = base_study()
        study["device_evidence"] = [{"source": "E:\\evidence.json", "claim": "RLC source"}]
        study["feed_network"] = {"metrics_file": "E:\\feed_metrics.json"}
        study["bias_network"] = {"dc_continuity": True}
        with tempfile.TemporaryDirectory(dir=r"E:\\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertEqual(plan["device_evidence"][0]["claim"], "RLC source")
        self.assertEqual(plan["feed_network"]["metrics_file"], "E:\\feed_metrics.json")
        self.assertTrue(plan["bias_network"]["dc_continuity"])

    def test_plan_records_threshold_values_and_sources(self):
        study = base_study()
        study["profile"] = "radiation_antenna"
        study["evaluation"]["sparameter_treepaths"] = [r"1D Results\S-Parameters\S1,1"]
        study["evaluation"]["phase_tolerance_deg"] = 8.0
        study["evaluation"]["threshold_sources"] = {"phase_tolerance_deg": "task requirement"}
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))

        self.assertEqual(plan["acceptance_thresholds"]["s11_threshold_db"]["value"], -10.0)
        self.assertEqual(
            plan["acceptance_thresholds"]["s11_threshold_db"]["source"],
            "built_in_conservative_default",
        )
        self.assertEqual(plan["acceptance_thresholds"]["phase_tolerance_deg"]["source"], "task requirement")
    def test_native_request_is_mcp_executable(self):
        plan = MODULE.build_plan(base_study(), Path(r"E:\gpt_articles\.native_request_test"))
        request = plan["native_sweep_request"]
        self.assertTrue(request["project_path"].endswith("working.cst"))
        self.assertEqual(request["result_tree_paths"], base_study()["evaluation"]["sparameter_treepaths"])
        self.assertTrue(request["output_dir"].endswith("native_sweep"))

    def test_current_export_selection_ignores_stale_run_ids(self):
        with tempfile.TemporaryDirectory(dir=r"E:\\gpt_articles") as directory:
            state_dir = Path(directory)
            exports = state_dir / "exports"
            exports.mkdir()
            treepath = r"1D Results\S-Parameters\S1,1"
            for run_id, value, parameters in (
                (1, 0.8, {"C": 1.0, "phaseY": 0.0}),
                (2, 0.1, {"C": 2.0, "phaseY": 3.6}),
            ):
                (exports / f"s11_run{run_id}.json").write_text(
                    json.dumps({
                        "treepath": treepath,
                        "run_id": run_id,
                        "parameter_combination": parameters,
                        "xdata": [5.0],
                        "ydata": [[value, 0.0]],
                    }),
                    encoding="utf-8",
                )
            case = {"state_dir": str(state_dir), "run_args": {"sparameter_treepaths": [treepath]}}
            selected = MODULE._record_selected_exports(
                case,
                {"status": "success", "run_id": 2, "sparameter_exported": [str(path) for path in exports.glob("*.json")]},
                {"C": 1.0, "phaseY": 0.0},
            )
            self.assertEqual(selected, ["exports/s11_run1.json"])
            manifest = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["raw_export_count"], 2)
            self.assertEqual(manifest["expected_parameter_combination"], {"C": 1.0, "phaseY": 0.0})
    def test_mesh_quality_flag_blocks_validated_status(self):
        study = base_study()
        plan = {
            "mesh_convergence_request": {
                "task_path": str(SOURCE.parent.parent.parent.parent),
                "mesh_levels": [{"label": "coarse"}, {"label": "fine"}],
            }
        }
        with tempfile.TemporaryDirectory(dir=r"E:\\gpt_articles") as directory:
            with patch.object(MODULE, "_invoke", return_value={"status": "success", "validated": False}):
                result = MODULE._run_mesh_convergence(study, plan, Path(directory))
        self.assertEqual(result["status"], "partial")

    def test_spike_refinement_plan_requires_explicit_mesh_policy(self):
        study = base_study()
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            solved_project = Path(plan["cases"][0]["project_path"])
            solved_project.parent.mkdir(parents=True)
            solved_project.write_text("solved project", encoding="utf-8")
            evaluation = {
                "refinement_requests": [{
                    "state_id": "00",
                    "treepath": r"1D Results\S-Parameters\SZmax(1),Zmax(1)",
                    "frequency_start": 4.2,
                    "frequency_stop": 4.4,
                    "suggested_step": 0.01,
                }]
            }
            result = MODULE._spike_refinement_plan(study, plan, evaluation, output)

        self.assertEqual(result["status"], "blocked_configuration")
        self.assertTrue(any("mesh_operations" in item for item in result["blocking_reasons"]))

    def test_spike_refinement_plan_builds_dry_run_and_mesh_recipe(self):
        study = base_study()
        study["spike_refinement"] = {
            "enabled": True,
            "max_passes": 1,
            "mesh_operations": [{
                "tool": "define-mesh",
                "args": {"steps_per_wave_near": 14, "steps_per_wave_far": 14},
            }],
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            solved_project = Path(plan["cases"][0]["project_path"])
            solved_project.parent.mkdir(parents=True)
            solved_project.write_text("solved project", encoding="utf-8")
            evaluation = {
                "refinement_requests": [{
                    "state_id": "00",
                    "treepath": r"1D Results\S-Parameters\SZmax(1),Zmax(1)",
                    "frequency_start": 4.2,
                    "frequency_stop": 4.4,
                }]
            }
            result = MODULE._spike_refinement_plan(study, plan, evaluation, output)

        self.assertEqual(result["status"], "ready")
        action = result["actions"][0]
        self.assertTrue(action["dry_run_args"]["dry_run"])
        self.assertFalse(action["apply_args"]["dry_run"])
        self.assertEqual(action["apply_args"]["operations"][0]["tool"], "define-frequency-range")
        self.assertEqual(action["apply_args"]["operations"][1]["tool"], "define-mesh")

    def test_blocked_spike_refinement_does_not_invoke_runtime(self):
        study = base_study()
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            evaluation = {
                "refinement_requests": [{
                    "state_id": "00",
                    "treepath": "S11",
                    "frequency_start": 4.2,
                    "frequency_stop": 4.4,
                }]
            }
            with patch.object(MODULE, "_invoke", side_effect=AssertionError("runtime must not run")):
                result = MODULE._run_spike_refinement(study, plan, evaluation, output)

        self.assertEqual(result["status"], "blocked_configuration")

    def test_failed_spike_refinement_action_cannot_validate(self):
        study = base_study()
        study["spike_refinement"] = {
            "enabled": True,
            "mesh_operations": [{"tool": "define-mesh", "args": {"steps_per_wave_near": 14}}],
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            source = Path(plan["cases"][0]["project_path"])
            source.parent.mkdir(parents=True)
            source.write_text("project", encoding="utf-8")
            evaluation = {
                "refinement_requests": [{
                    "state_id": "00",
                    "treepath": "S11",
                    "frequency_start": 4.2,
                    "frequency_stop": 4.4,
                }]
            }
            with patch.object(MODULE, "_invoke", return_value={"status": "error"}):
                result = MODULE._run_spike_refinement(study, plan, evaluation, output)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["passes"][0]["actions"][0]["status"], "failed_dry_run")

    def test_spike_refinement_validates_only_after_clean_parameter_matched_export(self):
        study = base_study()
        study["spike_refinement"] = {
            "enabled": True,
            "max_passes": 1,
            "mesh_operations": [{"tool": "define-mesh", "args": {"steps_per_wave_near": 14}}],
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            output = Path(directory)
            plan = MODULE.build_plan(study, output)
            source = Path(plan["cases"][0]["project_path"])
            source.parent.mkdir(parents=True)
            source.write_text("solved project", encoding="utf-8")
            export = output / "runtime-export.json"
            treepath = study["evaluation"]["sparameter_treepaths"][0]
            export.write_text(json.dumps({
                "treepath": treepath,
                "xdata": [4.2, 4.25, 4.3, 4.35, 4.4],
                "ydata": [[0.2, 0.0], [0.21, 0.0], [0.22, 0.0], [0.21, 0.0], [0.2, 0.0]],
                "parameter_combination": {"PathPara": 1.0},
                "run_id": 7,
            }), encoding="utf-8")
            evaluation = {
                "refinement_requests": [{
                    "state_id": "00",
                    "treepath": treepath,
                    "frequency_start": 4.2,
                    "frequency_stop": 4.4,
                }]
            }
            invoke_results = [
                {"status": "success"},
                {"status": "success"},
                {"status": "success", "run_id": 7, "sparameter_exported": [str(export)]},
            ]
            with patch.object(MODULE, "_invoke", side_effect=invoke_results) as invoke:
                result = MODULE._run_spike_refinement(study, plan, evaluation, output)

        self.assertEqual(invoke.call_count, 3)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["remaining_requests"], [])
        self.assertEqual(result["passes"][0]["actions"][0]["status"], "validated")
    def test_plan_preserves_classification_and_antenna_gate(self):
        study = base_study()
        study["classification"] = "finite_radiating_antenna"
        study["radiation_antenna_gate"] = {"status": "pending"}
        study["remaining_validation"] = ["run finite antenna"]
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertEqual(plan["classification"], "finite_radiating_antenna")
        self.assertEqual(plan["radiation_antenna_gate"]["status"], "pending")
        self.assertEqual(plan["remaining_validation"], ["run finite antenna"])

    def test_radiation_antenna_without_input_s11_blocks_optimization(self):
        study = base_study()
        study["profile"] = "radiation_antenna"
        study["evaluation"]["sparameter_treepaths"] = [
            r"1D Results\S-Parameters\SZmax(1),Zmax(1)"
        ]
        study["optimization"] = {
            "parameters": {"patch_length": {"min": 1.0, "max": 2.0}},
            "max_trials": 2,
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertEqual(plan["preflight_gates"]["status"], "blocked")
        self.assertFalse(plan["optimization_allowed"])
        self.assertEqual(plan["optimization_request"]["status"], "blocked_preflight")
        self.assertTrue(plan["optimization_request"]["blocked_by"])

    def test_radiation_antenna_with_input_s11_can_plan_optimization(self):
        study = base_study()
        study["profile"] = "radiation_antenna"
        study["evaluation"]["sparameter_treepaths"] = [
            r"1D Results\S-Parameters\S1,1"
        ]
        study["optimization"] = {
            "parameters": {"patch_length": {"min": 1.0, "max": 2.0}},
            "max_trials": 2,
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        self.assertEqual(plan["preflight_gates"]["status"], "ready")
        self.assertTrue(plan["optimization_allowed"])
        self.assertNotIn("status", plan["optimization_request"])

    def test_blocked_preflight_does_not_call_optimization_runtime(self):
        study = base_study()
        plan = {
            "source_project": str(SOURCE),
            "cases": [],
            "optimization_request": {"status": "blocked_preflight"},
            "optimization_allowed": False,
            "preflight_gates": {"blocking_reasons": ["missing S11"]},
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            with patch.object(MODULE, "_run_optimization", side_effect=AssertionError("must not run")):
                result = MODULE.run_isolated(study, plan, Path(directory))
        self.assertEqual(result["optimization"]["status"], "blocked_preflight")
        self.assertEqual(result["status"], "partial")

    def test_smoke_gate_requires_nonempty_input_s11_export(self):
        study = base_study()
        study["profile"] = "radiation_antenna"
        study["evaluation"]["sparameter_treepaths"] = [r"1D Results\S-Parameters\S1,1"]
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            root = Path(directory)
            state_dir = root / "state"
            export = state_dir / "exports" / "s11.json"
            export.parent.mkdir(parents=True)
            export.write_text(json.dumps({
                "treepath": r"1D Results\S-Parameters\S1,1",
                "data": [],
            }), encoding="utf-8")
            plan = {"cases": [{"state_id": "00", "state_dir": str(state_dir)}]}
            result = MODULE._post_smoke_gates(
                study,
                plan,
                [{"state_id": "00", "status": "validated", "run": {"selected_exports": ["exports/s11.json"]}}],
            )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("no non-empty driven-feed input S11", result["blocking_reasons"][0])

    def test_pymoo_accepts_list_form_with_binary_and_categorical_variables(self):
        study = base_study()
        study["optimization"] = {
            "engine": "pymoo",
            "parameters": [
                {"name": "length", "type": "float", "lower": 1.0, "upper": 2.0},
                {"name": "state", "type": "categorical", "values": ["00", "01"]},
                {"name": "enabled", "type": "binary"},
            ],
            "array_state": {"rows": 1, "cols": 2},
            "candidate_count": 2,
        }
        with tempfile.TemporaryDirectory(dir=r"E:\gpt_articles") as directory:
            plan = MODULE.build_plan(study, Path(directory))
        request = plan["optimization_request"]
        self.assertEqual(request["study_parameters"]["length"]["min"], 1.0)
        self.assertEqual(request["study_parameters"]["length"]["max"], 2.0)
        self.assertEqual(request["study_parameters"]["enabled"]["min"], 0)
        self.assertEqual(request["study_parameters"]["enabled"]["max"], 1)
        self.assertEqual(request["pymoo"]["array_state"]["values"], ["00"])


if __name__ == "__main__":
    unittest.main()
