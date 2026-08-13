"""Evidence-grounded, human-approved repository maintenance workflow.

RepoPilot deliberately keeps the model-facing decision layer replaceable.  The
default planner is deterministic so all included demos work without an API key.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from difflib import unified_diff
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")
MAX_OUTPUT = 12_000


def tokens(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text)}


@dataclass(frozen=True)
class CodeChunk:
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    content: str


class CodeIndex:
    """Small dependency-free, AST-aware lexical index for Python repositories."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.chunks: list[CodeChunk] = []

    def build(self) -> int:
        self.chunks.clear()
        for path in sorted(self.root.rglob("*.py")):
            if any(part.startswith(".") or part in {"__pycache__", ".venv"} for part in path.parts):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines, rel, found = source.splitlines(), path.relative_to(self.root).as_posix(), False
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    end = getattr(node, "end_lineno", node.lineno)
                    self.chunks.append(CodeChunk(rel, node.name, type(node).__name__, node.lineno, end,
                                                 "\n".join(lines[node.lineno - 1:end])))
                    found = True
            if not found:
                self.chunks.append(CodeChunk(rel, rel, "Module", 1, len(lines), source))
        return len(self.chunks)

    def search(self, query: str, top_k: int = 5) -> list[tuple[float, CodeChunk]]:
        query_tokens = tokens(query)
        ranked: list[tuple[float, CodeChunk]] = []
        for chunk in self.chunks:
            body, symbol = tokens(chunk.content), tokens(chunk.symbol + " " + chunk.path)
            lexical = len(query_tokens & body) / max(1, len(query_tokens))
            symbol_bonus = len(query_tokens & symbol) / max(1, len(query_tokens))
            score = lexical + 1.5 * symbol_bonus
            if score:
                ranked.append((score, chunk))
        return sorted(ranked, key=lambda item: (-item[0], item[1].path, item[1].start_line))[:top_k]


@dataclass(frozen=True)
class Evidence:
    kind: str
    source: str
    content: str
    metadata: dict = field(default_factory=dict)


class EvidenceStore:
    def __init__(self):
        self.items: list[Evidence] = []

    def add(self, evidence: Evidence) -> int:
        self.items.append(evidence)
        return len(self.items) - 1

    def to_json(self) -> str:
        return json.dumps([asdict(item) for item in self.items], ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    summary: str
    content: str
    duration_ms: int = 0


@dataclass
class Tool:
    name: str
    risk: str
    handler: Callable[..., ToolResult]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def execute(self, name: str, approved: bool = False, **kwargs) -> ToolResult:
        tool = self._tools[name]
        if tool.risk == "high" and not approved:
            return ToolResult(False, "approval_required", "High-risk tool requires human approval")
        started = time.monotonic()
        result = tool.handler(**kwargs)
        return ToolResult(result.ok, result.summary, result.content,
                          result.duration_ms or int((time.monotonic() - started) * 1000))


class TaskStatus(str, Enum):
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    APPLYING = "applying"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PatchOperation:
    path: str
    search: str
    replace: str
    reason: str


@dataclass
class Review:
    decision: str
    summary: str
    checks: list[str]


@dataclass
class TaskState:
    issue: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.PLANNING
    plan: list[str] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    proposal: list[PatchOperation] = field(default_factory=list)
    review: Review | None = None
    workspace: str | None = None

    def event(self, name: str, **data: object) -> None:
        self.trace.append({"event": name, "at": int(time.time() * 1000), **data})


class TaskStore:
    """SQLite checkpoint store. JSON makes a saved task portable and inspectable."""

    def __init__(self, root: Path):
        self.path = root / ".repopilot" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def save(self, state: TaskState, evidence: EvidenceStore) -> None:
        payload = json.dumps({"state": asdict(state), "evidence": [asdict(item) for item in evidence.items]},
                             ensure_ascii=False, default=lambda value: value.value if isinstance(value, Enum) else str(value))
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT OR REPLACE INTO tasks(task_id, payload) VALUES (?, ?)", (state.task_id, payload))

    def get(self, task_id: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def load(self, task_id: str) -> tuple[TaskState, EvidenceStore] | None:
        """Restore a durable task into executable domain objects."""
        payload = self.get(task_id)
        if not payload:
            return None
        raw = payload["state"]
        state = TaskState(
            issue=raw["issue"], task_id=raw["task_id"], status=TaskStatus(raw["status"]),
            plan=raw.get("plan", []), evidence_ids=raw.get("evidence_ids", []), trace=raw.get("trace", []),
            proposal=[PatchOperation(**item) for item in raw.get("proposal", [])],
            review=Review(**raw["review"]) if raw.get("review") else None,
            workspace=raw.get("workspace"),
        )
        evidence = EvidenceStore()
        evidence.items = [Evidence(**item) for item in payload.get("evidence", [])]
        return state, evidence


class RepoPilot:
    """Safe, deterministic Issue → evidence → approval → patch → review workflow."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.index = CodeIndex(self.repo)
        self.evidence = EvidenceStore()
        self.tasks = TaskStore(self.repo)

    def _record(self, state: TaskState, evidence: Evidence) -> None:
        state.evidence_ids.append(self.evidence.add(evidence))

    def resume(self, task_id: str) -> TaskState | None:
        restored = self.tasks.load(task_id)
        if not restored:
            return None
        state, self.evidence = restored
        state.event("task.resumed")
        self.tasks.save(state, self.evidence)
        return state

    def search_code(self, query: str, top_k: int = 5) -> list[dict]:
        self.index.build()
        return [{"score": round(score, 4), "path": chunk.path, "symbol": chunk.symbol,
                 "kind": chunk.kind, "start_line": chunk.start_line, "end_line": chunk.end_line,
                 "content": chunk.content}
                for score, chunk in self.index.search(query, top_k)]

    def read_file(self, relative_path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        path = (self.repo / relative_path).resolve()
        if self.repo not in path.parents or not path.is_file():
            return ToolResult(False, "unsafe_path", "Path must resolve inside the repository")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = max(1, start_line or 1), min(len(lines), end_line or len(lines))
        if end < start:
            return ToolResult(False, "invalid_range", "end_line must not precede start_line")
        return ToolResult(True, "file_read", "\n".join(lines[start - 1:end]))

    def analyze(self, issue: str, top_k: int = 5) -> TaskState:
        state = TaskState(issue=issue)
        state.plan = ["Retrieve relevant AST chunks", "Reproduce with an allowed test", "Propose the smallest patch",
                      "Wait for human approval", "Verify and review evidence"]
        state.event("plan.created", steps=state.plan)
        state.status = TaskStatus.RETRIEVING
        self.index.build()
        for score, chunk in self.index.search(issue, top_k):
            self._record(state, Evidence("code_reference", f"{chunk.path}:{chunk.start_line}", chunk.content,
                                         {"symbol": chunk.symbol, "score": round(score, 4)}))
        state.event("retrieval.completed", count=len(state.evidence_ids))
        state.status = TaskStatus.EXECUTING if state.evidence_ids else TaskStatus.FAILED
        self.tasks.save(state, self.evidence)
        return state

    def run_pytest(self, target: str = "tests", root: Path | None = None) -> ToolResult:
        if Path(target).is_absolute() or ".." in Path(target).parts:
            return ToolResult(False, "invalid_test_target", "Test target must stay inside the repository")
        cwd = (root or self.repo).resolve()
        result = subprocess.run(["python", "-m", "pytest", target, "-q"], cwd=cwd,
                                capture_output=True, text=True, timeout=60)
        output = (result.stdout + result.stderr)[-MAX_OUTPUT:]
        self.evidence.add(Evidence("test_output", target, output, {"returncode": result.returncode}))
        return ToolResult(result.returncode == 0, "tests_passed" if result.returncode == 0 else "tests_failed", output)

    def propose_patch(self, state: TaskState) -> list[PatchOperation]:
        """Propose only a narrow, explainable pagination repair in the bundled demo."""
        candidates: Iterable[Evidence] = (self.evidence.items[item] for item in state.evidence_ids)
        for item in candidates:
            path = item.source.split(":")[0]
            if path.startswith("tests/") or "/tests/" in path:
                continue
            if "offset = page * page_size" in item.content:
                state.proposal = [PatchOperation(path, "offset = page * page_size", "offset = (page - 1) * page_size",
                                                 "Page numbering is one-based; first page must begin at offset zero.")]
                state.status = TaskStatus.WAITING_APPROVAL
                state.event("approval.required", proposed_files=[operation.path for operation in state.proposal])
                self.tasks.save(state, self.evidence)
                return state.proposal
        state.event("proposal.unavailable", reason="No safe deterministic patch rule matched")
        self.tasks.save(state, self.evidence)
        return []

    def _create_isolated_workspace(self, task_id: str) -> Path:
        """Create a Git worktree where possible, with a copy fallback for demo folders."""
        base = self.repo.parent / ".repopilot-worktrees"
        base.mkdir(exist_ok=True)
        workspace = base / task_id
        if workspace.exists():
            raise RuntimeError(f"workspace already exists: {workspace}")
        git_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.repo,
                                   capture_output=True, text=True)
        if git_check.returncode == 0:
            created = subprocess.run(["git", "worktree", "add", "--detach", str(workspace), "HEAD"], cwd=self.repo,
                                     capture_output=True, text=True)
            if created.returncode == 0:
                return workspace
        shutil.copytree(self.repo, workspace, ignore=shutil.ignore_patterns(".repopilot", "__pycache__", ".pytest_cache", ".venv"))
        return workspace

    def apply_proposal(self, state: TaskState) -> ToolResult:
        if state.status != TaskStatus.WAITING_APPROVAL or not state.proposal:
            return ToolResult(False, "no_approved_proposal", "Task has no approved patch proposal")
        state.status = TaskStatus.APPLYING
        workspace = self._create_isolated_workspace(state.task_id)
        state.workspace = str(workspace)
        changed: list[str] = []
        for operation in state.proposal:
            path = (workspace / operation.path).resolve()
            if workspace not in path.parents or not path.is_file():
                return ToolResult(False, "unsafe_path", operation.path)
            original = path.read_text(encoding="utf-8")
            if operation.search not in original:
                return ToolResult(False, "patch_context_missing", operation.path)
            path.write_text(original.replace(operation.search, operation.replace, 1), encoding="utf-8")
            changed.append(operation.path)
        state.event("patch.applied", files=changed)
        self._record(state, Evidence("diff_reference", ",".join(changed), "Approved patch applied", {"files": changed}))
        state.status = TaskStatus.VERIFYING
        self.tasks.save(state, self.evidence)
        return ToolResult(True, "patch_applied", f"{', '.join(changed)} in {workspace}")

    def reject_proposal(self, state: TaskState, reason: str = "Rejected by human reviewer") -> ToolResult:
        if state.status != TaskStatus.WAITING_APPROVAL:
            return ToolResult(False, "nothing_to_reject", "Task is not waiting for approval")
        state.status = TaskStatus.CANCELLED
        state.event("approval.rejected", reason=reason)
        self.tasks.save(state, self.evidence)
        return ToolResult(True, "proposal_rejected", reason)

    def diff(self, state: TaskState) -> ToolResult:
        if not state.workspace:
            return ToolResult(False, "no_workspace", "Approve a proposal before requesting a diff")
        workspace = Path(state.workspace).resolve()
        pieces: list[str] = []
        for operation in state.proposal:
            original = (self.repo / operation.path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            changed = (workspace / operation.path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            pieces.extend(unified_diff(original, changed, fromfile=f"a/{operation.path}", tofile=f"b/{operation.path}"))
        return ToolResult(True, "diff_ready", "".join(pieces) or "No changes")

    def review_task(self, state: TaskState, test_result: ToolResult) -> Review:
        state.status = TaskStatus.REVIEWING
        allowed_paths = all(not Path(p.path).is_absolute() and ".." not in Path(p.path).parts for p in state.proposal)
        decision = "approve" if test_result.ok and allowed_paths else "request_changes"
        checks = [f"tests: {'passed' if test_result.ok else 'failed'}", f"patch scope: {'valid' if allowed_paths else 'invalid'}",
                  f"evidence items: {len(state.evidence_ids)}"]
        review = Review(decision, "Evidence, test output and patch scope were checked.", checks)
        state.review = review
        state.status = TaskStatus.COMPLETED if decision == "approve" else TaskStatus.FAILED
        state.event("review.completed", decision=decision, checks=checks)
        self.tasks.save(state, self.evidence)
        return review

    def report(self, state: TaskState) -> str:
        lines = [f"# RepoPilot task {state.task_id}", "", f"**Status:** {state.status.value}", "", "## Issue", state.issue,
                 "", "## Evidence", *[f"- {self.evidence.items[i].kind}: {self.evidence.items[i].source}" for i in state.evidence_ids],
                 "", "## Proposed patch", *[f"- `{p.path}`: {p.reason}" for p in state.proposal]]
        if state.workspace:
            lines.extend(["", "## Isolated workspace", f"`{state.workspace}`"])
        if state.review:
            lines.extend(["", "## Review", f"**{state.review.decision}** — {state.review.summary}", *[f"- {check}" for check in state.review.checks]])
        return "\n".join(lines) + "\n"
