import importlib.util
import tempfile
import unittest
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
