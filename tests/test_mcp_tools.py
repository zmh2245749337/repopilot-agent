import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repopilot.mcp_server import mcp_tools


class McpCodeRagToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    return (page - 1) * page_size\n", encoding="utf-8"
        )
        self.environment = patch.dict(os.environ, {"REPOPILOT_REPO_PATH": str(self.root)}, clear=False)
        self.environment.start()

    def tearDown(self):
        assistant = mcp_tools._assistants.pop(str(self.root.resolve()), None)
        if assistant:
            assistant.conversations.close()
        self.environment.stop()
        self.tmp.cleanup()

    def test_mcp_exposes_code_rag_agent_tools(self):
        names = {definition["name"] for definition in mcp_tools.TOOL_DEFINITIONS}
        self.assertTrue({"ask_code", "summarize_code", "locate_code_dependencies",
                         "suggest_code_tests", "scan_code_safety"}.issubset(names))

    def test_ask_code_returns_grounded_trace_and_citations(self):
        result = mcp_tools.ask_code("Where is list_orders?")
        self.assertEqual(result["tool"], "search_code")
        self.assertEqual(result["citations"][0]["path"], "orders.py")
        self.assertIn("tool.completed", [step["event"] for step in result["trace"]])
