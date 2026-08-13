import shutil
import tempfile
import unittest
from pathlib import Path

from repopilot.core import CodeIndex, RepoPilot, TaskStatus
from repopilot.planner import PlanResult
from repopilot.retrieval import HybridRetriever


class FixedPlanner:
    def plan(self, issue: str) -> PlanResult:
        return PlanResult(["Understand the issue", "Collect evidence", "Wait for approval"], provider="test-planner")


class DemoEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [[1.0, 0.0]]
        vectors.extend([([1.0, 0.0] if "semantic_target" in text else [0.0, 1.0]) for text in texts[1:]])
        return vectors


class AdvancedWorkflowTests(unittest.TestCase):
    def test_custom_planner_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def semantic_target():\n    return True\n", encoding="utf-8")
            pilot = RepoPilot(root, planner=FixedPlanner())
            state = pilot.analyze("find semantic target")
            self.assertEqual(state.plan[0], "Understand the issue")
            self.assertEqual(state.trace[0]["provider"], "test-planner")

    def test_semantic_channel_can_retrieve_when_lexical_channel_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("def semantic_target():\n    return True\n", encoding="utf-8")
            index = CodeIndex(root)
            index.build()
            result = HybridRetriever(index, DemoEmbeddingProvider()).search("unrelated words", 1)[0]
            self.assertEqual(result.chunk.symbol, "semantic_target")
            self.assertIn("semantic", result.channels)

    def test_all_controlled_cases_complete_in_isolated_workspace(self):
        cases_root = Path(__file__).parents[1] / "demo" / "cases"
        scenarios = [
            ("pagination_off_by_one", "list_orders first page offset bug"),
            ("optional_field_500", "create_order optional note None causes 500"),
            ("path_traversal", "resolve_download_path requested_path traversal"),
        ]
        for case_name, issue in scenarios:
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / case_name
                shutil.copytree(cases_root / case_name, root)
                pilot = RepoPilot(root)
                state = pilot.analyze(issue)
                self.assertFalse(pilot.reproduce(state, "tests").ok)
                self.assertTrue(pilot.propose_patch(state), case_name)
                self.assertTrue(pilot.apply_proposal(state).ok)
                result = pilot.run_pytest("tests", root=Path(state.workspace), state=state)
                pilot.review_task(state, result)
                self.assertTrue(result.ok, result.content)
                self.assertEqual(state.status, TaskStatus.COMPLETED)
