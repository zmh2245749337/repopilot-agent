import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI optional dependency is not installed")
class ApiWorkflowTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from repopilot.api import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orders.py").write_text(
            "def list_orders(page, page_size):\n    offset = page * page_size\n    return offset\n", encoding="utf-8")
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_orders.py").write_text(
            "from orders import list_orders\n\ndef test_first_page():\n    assert list_orders(1, 10) == 0\n", encoding="utf-8")
        self.client = TestClient(create_app(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dashboard_and_approved_workflow(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        task = self.client.post("/api/tasks", json={"issue": "list_orders first page offset bug"}).json()
        self.assertEqual(task["status"], "executing")
        reproduced = self.client.post(f"/api/tasks/{task['task_id']}/reproduce", json={"test_target": "tests"})
        self.assertEqual(reproduced.status_code, 200)
        task = reproduced.json()["task"]
        self.assertEqual(task["status"], "waiting_approval")
        approved = self.client.post(f"/api/tasks/{task['task_id']}/approve")
        self.assertEqual(approved.status_code, 200)
        self.assertIn("-    offset = page * page_size", approved.json()["diff"])
        verified = self.client.post(f"/api/tasks/{task['task_id']}/verify", json={"test_target": "tests"})
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["task"]["status"], "completed")

    def test_reject_leaves_repository_unchanged(self):
        task = self.client.post("/api/tasks", json={"issue": "list_orders first page offset bug"}).json()
        task = self.client.post(f"/api/tasks/{task['task_id']}/reproduce", json={"test_target": "tests"}).json()["task"]
        rejected = self.client.post(f"/api/tasks/{task['task_id']}/reject", json={"reason": "not now"})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["task"]["status"], "cancelled")
        self.assertIn("page * page_size", (self.root / "orders.py").read_text(encoding="utf-8"))

    def test_read_only_code_rag_chat_returns_citations(self):
        response = self.client.post("/api/chat", json={"message": "Where is list_orders implemented?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "offline-evidence")
        self.assertEqual(payload["citations"][0]["path"], "orders.py")
        self.assertIn("answer.generated", [event["event"] for event in payload["trace"]])

    def test_code_rag_stream_exposes_agent_events_and_answer_deltas(self):
        response = self.client.post("/api/chat/stream", json={"message": "Where is list_orders?"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", response.text)
        self.assertIn("event: answer.delta", response.text)
        self.assertIn("event: complete", response.text)

    def test_zip_import_switches_the_active_read_only_repository(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("uploaded/catalog.py", "def product_title():\n    return 'RepoPilot'\n")
        response = self.client.post("/api/repositories/import/zip", files={"file": ("catalog.zip", archive.getvalue(), "application/zip")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["repository"]["source"], "zip")
        self.assertIn("catalog.py", response.json()["files"])
        preview = self.client.get("/api/repository/file", params={"path": "catalog.py"})
        self.assertEqual(preview.status_code, 200)
        self.assertIn("product_title", preview.json()["content"])
