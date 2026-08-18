import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from repopilot.chat import CodeRagAssistant, ConversationStore, OpenAICompatibleResponder, classify_intent


class FakeResponder:
    model = "test-model"
    base_url = "https://open.bigmodel.cn/api/paas/v4"

    def answer(self, message, context, history):
        return "The evidence says list_orders is in [orders.py:1-3]."


class SemanticFakeResponder(FakeResponder):
    def route(self, message, previous=None):
        return {"intent": "repository_summary", "retrieval_query": "repository architecture main modules"}


class CodeRagAssistantTests(unittest.TestCase):
    def setUp(self):
        self.model_env = patch.dict(os.environ, {
            "REPOPILOT_MODEL_BASE_URL": "", "REPOPILOT_MODEL_NAME": "", "REPOPILOT_API_KEY": "",
        })
        self.model_env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    offset = (page - 1) * page_size\n    return offset\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()
        self.model_env.stop()

    def test_offline_answer_returns_grounded_citations(self):
        assistant = CodeRagAssistant(self.root)
        answer = assistant.ask("Where is the list_orders pagination logic?")
        self.assertEqual(answer.provider, "offline-evidence")
        self.assertFalse(answer.fallback)
        self.assertEqual(answer.citations[0].path, "orders.py")
        self.assertIn("list_orders", answer.answer)
        self.assertEqual(answer.trace[0]["event"], "intent.classified")

    def test_greeting_uses_direct_answer_without_code_retrieval(self):
        assistant = CodeRagAssistant(self.root)
        answer = assistant.ask("你好")
        self.assertEqual(answer.intent, "direct_answer")
        self.assertEqual(answer.provider, "local-direct-answer")
        self.assertFalse(answer.fallback)
        self.assertEqual(answer.citations, [])
        self.assertIn("我是 RepoPilot", answer.answer)
        self.assertIn("retrieval.skipped", [event["event"] for event in answer.trace])

    def test_project_and_module_questions_route_to_useful_intents(self):
        self.assertEqual(classify_intent("这个项目主要做什么？"), "repository_summary")
        self.assertEqual(classify_intent("详细解释这个模块"), "function_summary")
        self.assertEqual(classify_intent("我怎么详细学习 Agent"), "direct_answer")

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
        self.assertEqual(assistant.model_status()["status"], "configured")
        self.assertEqual(assistant.model_status()["provider"], "zhipu")
        answer = assistant.ask("Where is list_orders?")
        self.assertEqual(answer.provider, "openai-compatible:test-model")
        self.assertFalse(answer.fallback)
        self.assertIn("orders.py", answer.answer)
        self.assertEqual(assistant.model_status()["status"], "online")

    def test_semantic_router_can_upgrade_an_ambiguous_question(self):
        assistant = CodeRagAssistant(self.root, responder=SemanticFakeResponder())
        answer = assistant.ask("这个代码主要是做啥的")
        self.assertEqual(answer.intent, "repository_summary")
        self.assertTrue(next(event for event in answer.trace if event["event"] == "semantic_router.completed")
                        ["used_model_query"])
        self.assertIn("orders.py", answer.answer)

    def test_offline_model_status_never_exposes_credentials(self):
        status = CodeRagAssistant(self.root, responder=None).model_status()
        self.assertEqual(status, {"configured": False, "status": "offline",
                                  "provider": "offline-evidence", "model": None})
        self.assertNotIn("api_key", status)

    def test_zhipu_payload_disables_thinking_and_bounds_output(self):
        responder = OpenAICompatibleResponder(
            "https://open.bigmodel.cn/api/paas/v4", "secret-not-serialized", "glm-4.7-flash", max_tokens=900
        )
        payload = responder._payload("ping", "synthetic evidence", [], stream=True)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 900)
        self.assertTrue(payload["stream"])
        self.assertNotIn("api_key", payload)

    def test_model_retries_a_transient_rate_limit_before_succeeding(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"REPOPILOT_GLM_OK"}}]}'

        responder = OpenAICompatibleResponder("https://open.bigmodel.cn/api/paas/v4", "test-key", "glm-test")
        limited = HTTPError("https://example.test", 429, "busy", None, None)
        with patch("repopilot.chat.urlopen", side_effect=[limited, Response()]) as open_call, \
                patch("repopilot.chat.time.sleep") as sleep:
            self.assertEqual(responder.answer("ping", "synthetic", []), "REPOPILOT_GLM_OK")
        self.assertEqual(open_call.call_count, 2)
        sleep.assert_called_once_with(1)

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
