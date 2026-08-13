import tempfile
import unittest
from pathlib import Path

from repopilot.chat import CodeRagAssistant, ConversationStore


class FakeResponder:
    model = "test-model"

    def answer(self, message, context, history):
        return "The evidence says list_orders is in [orders.py:1-3]."


class CodeRagAssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    offset = (page - 1) * page_size\n    return offset\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_answer_returns_grounded_citations(self):
        assistant = CodeRagAssistant(self.root)
        answer = assistant.ask("Where is the list_orders pagination logic?")
        self.assertEqual(answer.provider, "offline-evidence")
        self.assertFalse(answer.fallback)
        self.assertEqual(answer.citations[0].path, "orders.py")
        self.assertIn("list_orders", answer.answer)
        self.assertEqual(answer.trace[0]["event"], "intent.classified")

    def test_conversation_store_keeps_multi_turn_history(self):
        store = ConversationStore()
        assistant = CodeRagAssistant(self.root, conversations=store)
        first = assistant.ask("Where is list_orders?")
        second = assistant.ask("Summarize it", first.conversation_id)
        self.assertEqual(second.conversation_id, first.conversation_id)
        self.assertEqual([message["role"] for message in store.history(first.conversation_id)],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual(second.trace[0]["intent"], "repository_summary")

    def test_configured_responder_is_used_for_grounded_answer(self):
        assistant = CodeRagAssistant(self.root, responder=FakeResponder())
        answer = assistant.ask("Where is list_orders?")
        self.assertEqual(answer.provider, "openai-compatible:test-model")
        self.assertFalse(answer.fallback)
        self.assertIn("orders.py", answer.answer)
