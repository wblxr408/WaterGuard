import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "contracts"))

from services.tool_gateway.server import (_preflight_action_plan, _catalog_for, wg_create_perturbation,
    wg_generate_candidates, wg_inspect_network, wg_propose_task, wg_submit_plan, wg_validate_action_plan)
from waterguard_contracts.core import FIXTURES, file_hash, load, plan_hash, task_hash, task_ref
from services.api.runtime import RunStore


class ToolGatewayTests(unittest.TestCase):
    def _epa_task(self):
        task = copy.deepcopy(load(FIXTURES / "task.json"))
        manifest, catalog = _catalog_for("epa-net1")
        task["network_ref"] = {"network_id": "epa-net1", "revision": manifest["revision"], "sha256": manifest["sha256"], "overlay_hash": manifest["overlays"][0]["sha256"]}
        task["action_catalog_ref"] = "data/catalogs/epa-net1.json"
        task["action_catalog_hash"] = file_hash(ROOT / "data" / "catalogs" / "epa-net1.json")
        task["fault"]["link_id"] = "10"
        task["action_limits"]["allowed_link_ids"] = ["10", "11"]
        task["constraint_hash"] = task_hash(task)
        return task, catalog

    def test_inspect_returns_only_approved_catalog(self):
        response = wg_inspect_network("epa-net1")

        self.assertEqual(response["code"], "NETWORK_INSPECTED")
        self.assertEqual(response["allowed_action_catalog"]["network_id"], "epa-net1")
        self.assertEqual(len(response["allowed_action_catalog"]["facilities"]), 12)

    def test_inspect_rejects_unapproved_network_id(self):
        with self.assertRaises(ValueError):
            wg_inspect_network("../epa-net1")

    def test_preflight_accepts_a_frozen_candidate(self):
        task = load(FIXTURES / "task.json")
        plan = load(FIXTURES / "candidate-b.json")
        catalog = load(FIXTURES / "action-catalog.json")

        result = _preflight_action_plan(task, plan, catalog)

        self.assertEqual(result["candidate_id"], "candidate-b")
        self.assertEqual(result["action_count"], 1)
        self.assertEqual(result["action_cost"], 1)

    def test_preflight_rejects_unauthorized_action(self):
        task = load(FIXTURES / "task.json")
        plan = copy.deepcopy(load(FIXTURES / "candidate-b.json"))
        catalog = load(FIXTURES / "action-catalog.json")
        plan["actions"][0]["link_id"] = "P_MAIN"
        plan["plan_hash"] = plan_hash(plan)

        with self.assertRaisesRegex(ValueError, "unauthorized facility"):
            _preflight_action_plan(task, plan, catalog)

    def test_tool_returns_structured_feedback_for_invalid_request(self):
        response = wg_validate_action_plan("{}", "{}")

        self.assertEqual(response["status"], "rejected")
        self.assertEqual(response["code"], "INVALID_ACTION")
        self.assertTrue(response["retryable"])

    def test_epa_catalog_remains_bound_to_its_network(self):
        manifest, catalog = _catalog_for("epa-net1")

        self.assertEqual(manifest["network_id"], catalog["network_id"])

    def test_generation_submission_and_perturbation_are_bounded(self):
        task, _ = self._epa_task()
        generated = wg_generate_candidates(json.dumps(task), 4)
        self.assertEqual(generated["code"], "CANDIDATES_GENERATED")
        plan = next(candidate for candidate in generated["candidates"] if candidate["actions"])
        submitted = wg_submit_plan(json.dumps(task), json.dumps(plan))
        self.assertEqual(submitted["code"], "CANDIDATE_ACCEPTED")
        perturbation = wg_create_perturbation(json.dumps(task), json.dumps(plan), json.dumps({"demand_multiplier": task["perturbation_domain"]["demand_multipliers"][0]}))
        self.assertEqual(perturbation["code"], "PERTURBATION_CREATED")

    def test_physical_v4_task_uses_the_compiler_catalogue(self):
        from services.hydraulics.scenarios import physical_case

        task, plan = physical_case("epa-net1", "closure")

        proposed = wg_propose_task(json.dumps(task))
        submitted = wg_submit_plan(json.dumps(task), json.dumps(plan))
        generated = wg_generate_candidates(json.dumps(task), 1)

        self.assertEqual(proposed["code"], "TASK_PROPOSAL_VALID")
        self.assertEqual(submitted["code"], "CANDIDATE_ACCEPTED")
        self.assertEqual(len(generated["candidates"]), 1)

    def test_strict_gateway_uses_frozen_run_not_model_task_json(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".test-artifacts") as directory:
            root = Path(directory)
            store = RunStore(root / "state.sqlite3", root / "artifacts")
            from services.hydraulics.scenarios import physical_case
            task, _plan = physical_case("epa-net1", "closure")
            task = store.save_task(task)
            run = store.create_run(task, "benchmark")
            store.bind_agh_session(run["run_id"], "session-test-1", "model-test-1")
            env = {
                "WG_GATEWAY_STRICT": "1", "WG_AGH_SESSION_REF": "session-test-1",
                "WG_AGH_ROLE": "planner", "WG_AGH_HOST_VERIFIED": "1",
                "WG_DATABASE": str(root / "state.sqlite3"), "WG_ARTIFACT_ROOT": str(root / "artifacts"),
            }
            with patch.dict(os.environ, env, clear=False):
                good = wg_generate_candidates(candidate_limit=1, run_id=run["run_id"])
                injected = wg_generate_candidates(candidate_limit=1, run_id=run["run_id"], task_json=json.dumps({}))

        self.assertEqual(good["code"], "CANDIDATES_GENERATED", good)
        self.assertEqual(len(good["candidates"]), 1)
        self.assertEqual(injected["code"], "INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
