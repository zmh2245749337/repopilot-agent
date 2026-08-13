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

    def test_follow_up_is_rewritten_and_routed_to_dependencies(self):
        assistant = CodeRagAssistant(self.root)
        first = assistant.ask("Where is list_orders?")
        second = assistant.ask("What imports does it depend on?", first.conversation_id)
        self.assertEqual(second.intent, "dependency_lookup")
        self.assertEqual(second.tool, "locate_dependencies")
        self.assertIn("Previous question", second.rewritten_query)
        self.assertTrue(next(event for event in second.trace if event["event"] == "query.rewritten")["applied"])

    def test_configured_responder_is_used_for_grounded_answer(self):
        assistant = CodeRagAssistant(self.root, responder=FakeResponder())
        answer = assistant.ask("Where is list_orders?")
        self.assertEqual(answer.provider, "openai-compatible:test-model")
        self.assertFalse(answer.fallback)
        self.assertIn("orders.py", answer.answer)

    def test_offline_stream_emits_metadata_deltas_and_completion(self):
        assistant = CodeRagAssistant(self.root)
        events = list(assistant.stream("Where is list_orders?"))
        self.assertEqual(events[0][0], "meta")
        self.assertEqual(events[0][1]["tool_result"]["name"], "search_code")
        self.assertIn("tool.completed", [step["event"] for step in events[0][1]["trace"]])
        self.assertTrue(any(event == "answer.delta" for event, _ in events))
        self.assertEqual(events[-1][0], "complete")
        self.assertEqual(events[-1][1]["tool_result"]["name"], "search_code")

    def test_tool_registry_exposes_six_typed_read_only_tools(self):
        assistant = CodeRagAssistant(self.root)
        definitions = assistant.tool_registry.definitions()
        self.assertEqual(len(definitions), 6)
        self.assertTrue(all(tool["risk"] == "read-only" for tool in definitions))
        self.assertIn("scan_safety", {tool["name"] for tool in definitions})

    def test_sqlite_conversations_survive_store_restart(self):
        database = self.root / ".repopilot" / "chat.sqlite3"
        first_store = ConversationStore(database)
        answer = CodeRagAssistant(self.root, conversations=first_store).ask("Where is list_orders?")
        first_store.close()
        second_store = ConversationStore(database)
        self.assertEqual(second_store.list(str(self.root))[0]["message_count"], 2)
        self.assertEqual(second_store.messages(answer.conversation_id, str(self.root))[0]["role"], "user")
        second_store.close()
