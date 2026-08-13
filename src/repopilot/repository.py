"""Safe repository import and read-only explorer helpers."""
from __future__ import annotations

import io
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_FILES = 2_000
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
SKIP_PARTS = {".git", ".repopilot", ".venv", "__pycache__", "node_modules"}


class RepositoryImportError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryInfo:
    root: str
    label: str
    source: str


def validate_github_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise RepositoryImportError("Only public https://github.com/owner/repository URLs are allowed")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise RepositoryImportError("GitHub URL must have exactly an owner and repository name")
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        raise RepositoryImportError("GitHub URL must include a repository name")
    return f"https://github.com/{owner}/{repository}.git"


class RepositoryCatalog:
    """Tracks selected repositories and confines imports to an isolated directory."""

    def __init__(self, initial_root: str | Path):
        self.initial_root = Path(initial_root).resolve()
        self.import_root = self.initial_root.parent / ".repopilot-imports"
        self.allowed_roots: set[Path] = {self.initial_root}

    def register(self, root: str | Path, label: str, source: str) -> RepositoryInfo:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise RepositoryImportError("Imported repository directory does not exist")
        self.allowed_roots.add(resolved)
        return RepositoryInfo(str(resolved), label, source)

    def import_github(self, url: str) -> RepositoryInfo:
        clone_url = validate_github_url(url)
        self.import_root.mkdir(parents=True, exist_ok=True)
        target = self.import_root / f"github-{uuid.uuid4().hex[:10]}"
        completed = subprocess.run(["git", "clone", "--depth", "1", clone_url, str(target)], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=90)
        if completed.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise RepositoryImportError(f"GitHub import failed: {(completed.stderr or completed.stdout).strip()[-500:]}")
        return self.register(target, label=Path(clone_url).stem, source="github")

    def import_zip(self, payload: bytes, filename: str = "repository.zip") -> RepositoryInfo:
        if not payload:
            raise RepositoryImportError("ZIP upload is empty")
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise RepositoryImportError("ZIP upload exceeds the 25 MB limit")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as error:
            raise RepositoryImportError("Uploaded file is not a valid ZIP archive") from error
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > MAX_ARCHIVE_FILES or sum(entry.file_size for entry in entries) > MAX_EXTRACTED_BYTES:
            raise RepositoryImportError("ZIP archive exceeds file-count or extracted-size limits")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RepositoryImportError("ZIP archive contains an unsafe path")
        self.import_root.mkdir(parents=True, exist_ok=True)
        target = self.import_root / f"zip-{uuid.uuid4().hex[:10]}"
        target.mkdir()
        try:
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                if any(part in SKIP_PARTS for part in relative.parts):
                    continue
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        root = self._unwrap_single_root(target)
        return self.register(root, label=Path(filename).stem or "uploaded-repository", source="zip")

    @staticmethod
    def _unwrap_single_root(root: Path) -> Path:
        children = [item for item in root.iterdir() if item.name not in {"__MACOSX"}]
        return children[0] if len(children) == 1 and children[0].is_dir() else root

    def list_files(self, root: str | Path, limit: int = 300) -> list[str]:
        selected = self._allowed(root)
        files = []
        for path in sorted(selected.rglob("*")):
            if any(part in SKIP_PARTS or part.startswith(".") for part in path.relative_to(selected).parts):
                continue
            if path.is_file():
                files.append(path.relative_to(selected).as_posix())
                if len(files) >= limit:
                    break
        return files

    def read_file(self, root: str | Path, relative_path: str, max_bytes: int = 80_000) -> dict:
        selected = self._allowed(root)
        candidate = (selected / relative_path).resolve()
        if candidate != selected and selected not in candidate.parents:
            raise RepositoryImportError("File path must stay inside the selected repository")
        if not candidate.is_file():
            raise RepositoryImportError("Requested repository file does not exist")
        if candidate.stat().st_size > max_bytes:
            raise RepositoryImportError("Requested repository file exceeds the preview limit")
        return {"path": candidate.relative_to(selected).as_posix(),
                "content": candidate.read_text(encoding="utf-8", errors="replace")}

    def _allowed(self, root: str | Path) -> Path:
        resolved = Path(root).resolve()
        if resolved not in self.allowed_roots:
            raise RepositoryImportError("Repository is not registered in this local session")
        return resolved
