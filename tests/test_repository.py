import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from repopilot.repository import RepositoryCatalog, RepositoryImportError, validate_github_url


def zip_payload(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


class RepositoryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "initial"
        self.root.mkdir()
        (self.root / "main.py").write_text("def initial():\n    return True\n", encoding="utf-8")
        self.catalog = RepositoryCatalog(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_github_url_is_strictly_normalized(self):
        self.assertEqual(validate_github_url("https://github.com/openai/example"), "https://github.com/openai/example.git")
        with self.assertRaises(RepositoryImportError):
            validate_github_url("https://evil.example/openai/example")

    def test_zip_import_unwraps_root_and_supports_safe_preview(self):
        info = self.catalog.import_zip(zip_payload({"sample/service.py": "def answer():\n    return 42\n"}), "sample.zip")
        self.assertEqual(info.source, "zip")
        self.assertEqual(self.catalog.list_files(info.root), ["service.py"])
        self.assertIn("answer", self.catalog.read_file(info.root, "service.py")["content"])
        with self.assertRaises(RepositoryImportError):
            self.catalog.read_file(info.root, "../initial/main.py")

    def test_zip_import_rejects_path_traversal(self):
        with self.assertRaises(RepositoryImportError):
            self.catalog.import_zip(zip_payload({"../outside.py": "danger"}))
