"""Read-only and bounded tool implementations exposed through MCP."""
from __future__ import annotations

import os
from pathlib import Path

from ..core import RepoPilot


def _pilot() -> RepoPilot:
    return RepoPilot(Path(os.environ.get("REPOPILOT_REPO_PATH", ".")).resolve())


def search_code(query: str, top_k: int = 5) -> dict:
    return {"query": query, "results": _pilot().search_code(query, min(max(top_k, 1), 20))}


def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    result = _pilot().read_file(path, start_line, end_line)
    return {"ok": result.ok, "summary": result.summary, "content": result.content}


def run_tests(target: str = "tests") -> dict:
    result = _pilot().run_pytest(target)
    return {"ok": result.ok, "summary": result.summary, "content": result.content, "duration_ms": result.duration_ms}


TOOL_DEFINITIONS = [
    {"name": "search_code", "description": "Search AST-indexed Python code in the configured repository.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}},
    {"name": "read_file", "description": "Read a bounded range from a file inside the configured repository.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "run_tests", "description": "Run the repository's allowed pytest target. Paths outside the repository are rejected.",
     "inputSchema": {"type": "object", "properties": {"target": {"type": "string", "default": "tests"}}}},
]
