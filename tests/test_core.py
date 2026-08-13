import tempfile
import unittest
from pathlib import Path

from repopilot.core import CodeIndex, RepoPilot, TaskStatus, Tool, ToolRegistry, ToolResult


class RepoPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    offset = page * page_size\n    return offset\n", encoding="utf-8")
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_orders.py").write_text(
            "from orders import list_orders\n\ndef test_first_page():\n    assert list_orders(1, 10) == 0\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ast_index_and_symbol_retrieval(self):
        index = CodeIndex(self.root)
        self.assertEqual(index.build(), 2)
        self.assertEqual(index.search("list_orders 分页")[0][1].symbol, "list_orders")

    def test_exact_symbol_name_outranks_call_sites(self):
        (self.root / "service.py").write_text(
            "class OrderService:\n    def list_orders(self):\n        return []\n", encoding="utf-8"
        )
        (self.root / "test_service.py").write_text(
            "def test_orders():\n    assert OrderService().list_orders() == []\n", encoding="utf-8"
        )
        index = CodeIndex(self.root)
        index.build()
        self.assertEqual(index.search("Where is OrderService implemented?")[0][1].symbol, "OrderService")

    def test_workflow_records_evidence_and_checkpoint(self):
        pilot = RepoPilot(self.root)
        state = pilot.analyze("list_orders offset 分页错误")
        self.assertTrue(state.evidence_ids)
        self.assertEqual(state.trace[-1]["event"], "retrieval.completed")
        self.assertIsNotNone(pilot.tasks.get(state.task_id))

    def test_approved_patch_completes_reviewed_workflow(self):
        pilot = RepoPilot(self.root)
        state = pilot.analyze("first page list_orders offset")
        self.assertFalse(pilot.reproduce(state, "tests").ok)
        self.assertEqual(len(pilot.propose_patch(state)), 1)
        self.assertEqual(state.status, TaskStatus.WAITING_APPROVAL)
        self.assertTrue(pilot.apply_proposal(state).ok)
        result = pilot.run_pytest("tests", root=Path(state.workspace), state=state)
        review = pilot.review_task(state, result)
        self.assertTrue(result.ok, result.content)
        self.assertEqual(review.decision, "approve")

    def test_invalid_baseline_target_cannot_unlock_a_patch(self):
        pilot = RepoPilot(self.root)
        state = pilot.analyze("first page list_orders offset")
        baseline = pilot.reproduce(state, "../outside")
        self.assertEqual(baseline.summary, "invalid_test_target")
        self.assertEqual(pilot.propose_patch(state), [])
        self.assertEqual(state.status, TaskStatus.FAILED)
        self.assertIsNone(state.workspace)
        self.assertIn("page * page_size", (self.root / "orders.py").read_text(encoding="utf-8"))

    def test_high_risk_tool_requires_approval(self):
        registry = ToolRegistry()
        registry.register(Tool("apply_patch", "high", lambda: ToolResult(True, "ok", "done")))
        self.assertEqual(registry.execute("apply_patch").summary, "approval_required")
        self.assertTrue(registry.execute("apply_patch", approved=True).ok)

    def test_test_target_cannot_escape_repository(self):
        pilot = RepoPilot(self.root)
        self.assertFalse(pilot.run_pytest("../outside").ok)


if __name__ == "__main__":
    unittest.main()
