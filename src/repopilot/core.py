from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")


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
    """AST-aware code index with lexical and symbol-aware retrieval."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.chunks: list[CodeChunk] = []

    def build(self) -> int:
        self.chunks.clear()
        for path in sorted(self.root.rglob("*.py")):
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            rel = path.relative_to(self.root).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    end = getattr(node, "end_lineno", node.lineno)
                    self.chunks.append(CodeChunk(rel, node.name, type(node).__name__, node.lineno, end,
                                                 "\n".join(lines[node.lineno - 1:end])))
            if not any(chunk.path == rel for chunk in self.chunks):
                self.chunks.append(CodeChunk(rel, rel, "Module", 1, len(lines), source))
        return len(self.chunks)

    def search(self, query: str, top_k: int = 5) -> list[tuple[float, CodeChunk]]:
        query_tokens = tokens(query)
        ranked = []
        for chunk in self.chunks:
            body = tokens(chunk.content)
            symbol = tokens(chunk.symbol + " " + chunk.path)
            lexical = len(query_tokens & body) / max(1, len(query_tokens))
            symbol_bonus = len(query_tokens & symbol) / max(1, len(query_tokens))
            score = lexical + 1.5 * symbol_bonus
            if score:
                ranked.append((score, chunk))
        return sorted(ranked, key=lambda item: (-item[0], item[1].path))[:top_k]


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
        return tool.handler(**kwargs)


class TaskStatus(str, Enum):
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskState:
    issue: str
    status: TaskStatus = TaskStatus.PLANNING
    plan: list[str] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


class RepoPilot:
    """Deterministic MVP shell for the future LLM/LangGraph orchestrator."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.index = CodeIndex(self.repo)
        self.evidence = EvidenceStore()

    def analyze(self, issue: str, top_k: int = 5) -> TaskState:
        state = TaskState(issue=issue)
        state.plan = ["检索相关代码", "运行或读取失败测试", "形成最小修改建议", "审批后验证"]
        state.trace.append({"event": "plan.created", "steps": state.plan})
        state.status = TaskStatus.RETRIEVING
        self.index.build()
        for score, chunk in self.index.search(issue, top_k):
            evidence_id = self.evidence.add(Evidence(
                "code_reference", f"{chunk.path}:{chunk.start_line}", chunk.content,
                {"symbol": chunk.symbol, "score": round(score, 4)}))
            state.evidence_ids.append(evidence_id)
        state.trace.append({"event": "retrieval.completed", "count": len(state.evidence_ids)})
        state.status = TaskStatus.EXECUTING if state.evidence_ids else TaskStatus.FAILED
        return state

    def run_pytest(self, target: str = "tests") -> ToolResult:
        result = subprocess.run(["python", "-m", "pytest", target, "-q"], cwd=self.repo,
                                capture_output=True, text=True, timeout=60)
        output = (result.stdout + result.stderr)[-12000:]
        self.evidence.add(Evidence("test_output", target, output, {"returncode": result.returncode}))
        return ToolResult(result.returncode == 0, "tests_passed" if result.returncode == 0 else "tests_failed", output)

