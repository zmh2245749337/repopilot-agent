import shutil
import tempfile
import unittest
from pathlib import Path

from repopilot.core import RepoPilot


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_nested_target_uses_a_copy_of_the_selected_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "nested" / "target"
            target.mkdir(parents=True)
            (target / "orders.py").write_text(
                "def list_orders(page, page_size):\n    offset = page * page_size\n    return offset\n", encoding="utf-8")
            tests = target / "tests"
            tests.mkdir()
            (tests / "test_orders.py").write_text(
                "from orders import list_orders\n\ndef test_first_page():\n    assert list_orders(1, 10) == 0\n", encoding="utf-8")
            pilot = RepoPilot(target)
            state = pilot.analyze("list_orders first page offset bug")
            pilot.reproduce(state)
            pilot.propose_patch(state)
            self.assertTrue(pilot.apply_proposal(state).ok)
            self.assertTrue((Path(state.workspace) / "orders.py").is_file())

    def test_git_probe_handles_unicode_repository_path(self):
        with tempfile.TemporaryDirectory(prefix="\u4ed3\u5e93_") as directory:
            root = Path(directory)
            (root / "orders.py").write_text("def list_orders(page, page_size):\n    offset = page * page_size\n", encoding="utf-8")
            pilot = RepoPilot(root)
            workspace = pilot._create_isolated_workspace(f"unicode-{root.name}")
            try:
                self.assertTrue((workspace / "orders.py").is_file())
            finally:
                shutil.rmtree(workspace, ignore_errors=True)
