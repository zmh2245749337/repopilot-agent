import tempfile
import unittest
from pathlib import Path

from repopilot.core import CodeIndex, RepoPilot, Tool, ToolRegistry, ToolResult


class RepoPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    offset = page * page_size\n    return offset\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ast_index_and_symbol_retrieval(self):
        index = CodeIndex(self.root)
        self.assertEqual(index.build(), 1)
        self.assertEqual(index.search("list_orders 分页")[0][1].symbol, "list_orders")

    def test_workflow_records_evidence(self):
        pilot = RepoPilot(self.root)
        state = pilot.analyze("list_orders offset 分页错误")
        self.assertTrue(state.evidence_ids)
        self.assertEqual(state.trace[-1]["event"], "retrieval.completed")

    def test_high_risk_tool_requires_approval(self):
        registry = ToolRegistry()
        registry.register(Tool("apply_patch", "high", lambda: ToolResult(True, "ok", "done")))
        self.assertEqual(registry.execute("apply_patch").summary, "approval_required")
        self.assertTrue(registry.execute("apply_patch", approved=True).ok)


if __name__ == "__main__":
    unittest.main()

