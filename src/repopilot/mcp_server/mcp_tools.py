"""Read-only and bounded tool implementations exposed through MCP."""
from __future__ import annotations

import os
from pathlib import Path

from ..chat import CodeRagAssistant, ConversationStore
from ..core import RepoPilot


_assistants: dict[str, CodeRagAssistant] = {}


def _pilot() -> RepoPilot:
    return RepoPilot(Path(os.environ.get("REPOPILOT_REPO_PATH", ".")).resolve())


def _assistant() -> CodeRagAssistant:
    root = str(Path(os.environ.get("REPOPILOT_REPO_PATH", ".")).resolve())
    if root not in _assistants:
        _assistants[root] = CodeRagAssistant(root, conversations=ConversationStore())
    return _assistants[root]


def search_code(query: str, top_k: int = 5) -> dict:
    return {"query": query, "results": _pilot().search_code(query, min(max(top_k, 1), 20))}


def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    result = _pilot().read_file(path, start_line, end_line)
    return {"ok": result.ok, "summary": result.summary, "content": result.content}


def run_tests(target: str = "tests") -> dict:
    result = _pilot().run_pytest(target)
    return {"ok": result.ok, "summary": result.summary, "content": result.content, "duration_ms": result.duration_ms}


def ask_code(question: str, conversation_id: str | None = None, top_k: int = 4) -> dict:
    """Ask the routed Code RAG agent and return answer, citations, and execution trace."""
    answer = _assistant().ask(question, conversation_id, min(max(top_k, 1), 8))
    return CodeRagAssistant.view(answer)


def summarize_code(symbol: str, top_k: int = 4) -> dict:
    return ask_code(f"Explain the function or class {symbol}", top_k=top_k)


def locate_code_dependencies(symbol: str, top_k: int = 4) -> dict:
    return ask_code(f"What imports and dependencies does {symbol} use?", top_k=top_k)


def suggest_code_tests(query: str, top_k: int = 4) -> dict:
    return ask_code(f"Suggest focused pytest tests for {query}", top_k=top_k)


def scan_code_safety(query: str = "repository security risks", top_k: int = 4) -> dict:
    return ask_code(f"Review security risks related to {query}", top_k=top_k)


TOOL_DEFINITIONS = [
    {"name": "search_code", "description": "Search AST-indexed Python code in the configured repository.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}, "required": ["query"]}},
    {"name": "read_file", "description": "Read a bounded range from a file inside the configured repository.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "run_tests", "description": "Run the repository's allowed pytest target. Paths outside the repository are rejected.",
     "inputSchema": {"type": "object", "properties": {"target": {"type": "string", "default": "tests"}}}},
    {"name": "ask_code", "description": "Route a repository question through Code RAG and return grounded citations plus an execution trace.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "conversation_id": {"type": "string"}, "top_k": {"type": "integer", "default": 4}}, "required": ["question"]}},
    {"name": "summarize_code", "description": "Explain a function or class using retrieved repository evidence.",
     "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "top_k": {"type": "integer", "default": 4}}, "required": ["symbol"]}},
    {"name": "locate_code_dependencies", "description": "Locate imports and dependencies for a code symbol using cited evidence.",
     "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "top_k": {"type": "integer", "default": 4}}, "required": ["symbol"]}},
    {"name": "suggest_code_tests", "description": "Recommend focused pytest targets for a code behavior or symbol.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 4}}, "required": ["query"]}},
    {"name": "scan_code_safety", "description": "Run the read-only Code RAG safety review and return cited findings.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": "repository security risks"}, "top_k": {"type": "integer", "default": 4}}}},
]
