"""Read-only conversational code RAG and bounded tool routing.

The chat layer may inspect a repository but deliberately has no patch-writing
tool.  Repository changes remain behind the separate approval workflow.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .core import CodeIndex
from .retrieval import HybridRetriever, embedding_provider_from_env


@dataclass(frozen=True)
class Citation:
    path: str
    symbol: str
    start_line: int
    end_line: int
    channels: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class ChatAnswer:
    conversation_id: str
    answer: str
    citations: list[Citation]
    provider: str
    fallback: bool
    trace: list[dict]
    intent: str
    tool: str
    rewritten_query: str


@dataclass(frozen=True)
class PreparedTurn:
    conversation_id: str
    intent: str
    tool: str
    rewritten_query: str
    citations: list[Citation]
    context: str
    trace: list[dict]
    history: list[dict[str, str]]


@dataclass(frozen=True)
class CodeTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[PreparedTurn], str] = field(repr=False, compare=False)


@dataclass(frozen=True)
class CodeToolResult:
    name: str
    output: str
    duration_ms: int
    metadata: dict


class CodeToolRegistry:
    """Typed registry for read-only Code RAG tools."""

    def __init__(self):
        self._tools: dict[str, CodeTool] = {}

    def register(self, tool: CodeTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate code tool: {tool.name}")
        self._tools[tool.name] = tool

    def execute(self, name: str, prepared: PreparedTurn) -> CodeToolResult:
        if name not in self._tools:
            raise KeyError(f"unknown code tool: {name}")
        started = time.monotonic()
        output = self._tools[name].handler(prepared)
        return CodeToolResult(name, output, int((time.monotonic() - started) * 1000),
                              {"risk": "read-only", "citations": len(prepared.citations)})

    def definitions(self) -> list[dict]:
        return [{"name": tool.name, "description": tool.description, "risk": "read-only",
                 "inputSchema": tool.input_schema} for tool in self._tools.values()]


class ConversationStore:
    """SQLite-backed conversation history, isolated by repository root."""

    def __init__(self, database: str | Path | None = None):
        self.database = str(database) if database else ":memory:"
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_repository_updated
                ON conversations(repository, updated_at DESC);
            """)

    def create(self, repository: str = "default", title: str = "新对话") -> str:
        identifier = uuid.uuid4().hex[:12]
        now = int(time.time() * 1000)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO conversations(id, repository, title, created_at, updated_at) VALUES(?,?,?,?,?)",
                (identifier, repository, title, now, now),
            )
        return identifier

    def append(self, conversation_id: str | None, role: str, content: str, repository: str = "default",
               citations: list[dict] | None = None) -> str:
        identifier = conversation_id or self.create(repository)
        now = int(time.time() * 1000)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT repository, title FROM conversations WHERE id = ?", (identifier,)
            ).fetchone()
            if not existing:
                self._connection.execute(
                    "INSERT INTO conversations(id, repository, title, created_at, updated_at) VALUES(?,?,?,?,?)",
                    (identifier, repository, "新对话", now, now),
                )
                existing = {"repository": repository, "title": "新对话"}
            if existing["repository"] != repository:
                raise ValueError("conversation belongs to a different repository")
            self._connection.execute(
                "INSERT INTO conversation_messages(conversation_id, role, content, citations_json, created_at) VALUES(?,?,?,?,?)",
                (identifier, role, content, json.dumps(citations or [], ensure_ascii=False), now),
            )
            title = existing["title"]
            if role == "user" and title == "新对话":
                title = content.strip().replace("\n", " ")[:60] or "新对话"
            self._connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now, identifier)
            )
        return identifier

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, content FROM conversation_messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def messages(self, conversation_id: str, repository: str) -> list[dict]:
        with self._lock:
            owner = self._connection.execute(
                "SELECT repository FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not owner or owner["repository"] != repository:
                raise KeyError("conversation not found")
            rows = self._connection.execute(
                "SELECT role, content, citations_json, created_at FROM conversation_messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"],
                 "citations": json.loads(row["citations_json"]), "created_at": row["created_at"]} for row in rows]

    def list(self, repository: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute("""
                SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(m.id) AS message_count
                FROM conversations c LEFT JOIN conversation_messages m ON m.conversation_id = c.id
                WHERE c.repository = ? GROUP BY c.id ORDER BY c.updated_at DESC
            """, (repository,)).fetchall()
        return [dict(row) for row in rows]

    def owns(self, conversation_id: str, repository: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND repository = ?", (conversation_id, repository)
            ).fetchone()
        return bool(row)

    def delete(self, conversation_id: str, repository: str) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT id FROM conversations WHERE id = ? AND repository = ?", (conversation_id, repository)
            ).fetchone()
            if not row:
                return False
            self._connection.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conversation_id,))
            self._connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def latest_user_message(self, conversation_id: str | None, repository: str = "default") -> str | None:
        if not conversation_id:
            return None
        with self._lock:
            row = self._connection.execute("""
                SELECT m.content FROM conversation_messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE m.conversation_id = ? AND c.repository = ? AND m.role = 'user' ORDER BY m.id DESC LIMIT 1
            """, (conversation_id, repository)).fetchone()
        return row["content"] if row else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def classify_intent(message: str) -> str:
    lowered = message.lower()
    if any(word in lowered for word in ("总结", "概览", "summary", "summarize", "overview")):
        return "repository_summary"
    if any(word in lowered for word in ("依赖", "调用", "导入", "import", "depends", "dependency", "call graph")):
        return "dependency_lookup"
    if any(word in lowered for word in ("测试", "test", "pytest", "回归")):
        return "test_guidance"
    if any(word in lowered for word in ("安全", "风险", "漏洞", "security", "risk", "injection", "traversal")):
        return "safety_review"
    if any(word in lowered for word in ("函数", "function", "解释", "怎么实现", "what does", "explain")):
        return "function_summary"
    if any(word in lowered for word in ("在哪", "位置", "where", "locate", "哪个文件")):
        return "code_location"
    return "code_question"


def rewrite_follow_up(message: str, previous: str | None) -> tuple[str, bool]:
    """Resolve simple follow-ups without inventing facts or using a model."""
    if not previous:
        return message, False
    lowered = message.lower()
    has_reference = bool(re.search(r"\b(it|this|that|they|them)\b", lowered)) or any(
        token in message for token in ("它", "这个", "该函数", "上面", "刚才", "前者", "后者")
    )
    if not has_reference:
        return message, False
    return f"Previous question: {previous}\nFollow-up: {message}", True


def expand_retrieval_query(query: str, intent: str) -> str:
    """Add small, auditable bilingual retrieval hints for common code concepts."""
    lowered = query.lower()
    expansions: list[str] = []
    if intent == "safety_review":
        expansions.extend(["security validation sanitize unsafe"])
        if any(word in lowered for word in ("路径", "穿越", "path", "traversal")):
            expansions.extend(["path traversal requested_path resolve_download_path parent absolute"])
    if intent == "test_guidance":
        expansions.append("tests pytest test_")
    if intent == "dependency_lookup":
        expansions.append("import from dependency call")
    if intent == "function_summary":
        expansions.append("def function class")
    return f"{query}\n{' '.join(expansions)}".strip()


class OpenAICompatibleResponder:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 30, max_tokens: int = 1_200):
        self.base_url, self.api_key, self.model, self.timeout_s = base_url.rstrip("/"), api_key, model, timeout_s
        self.max_tokens = max_tokens
        self.is_zhipu = (urlparse(self.base_url).hostname or "").endswith("bigmodel.cn")

    def _payload(self, message: str, context: str, history: list[dict[str, str]], stream: bool = False) -> dict:
        payload = {"model": self.model, "temperature": 0.2, "max_tokens": self.max_tokens,
                   "stream": stream, "messages": self._messages(message, context, history)}
        if self.is_zhipu:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _messages(self, message: str, context: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": (
                "You are RepoPilot's read-only code assistant. Answer only from supplied repository evidence. "
                "Do not claim to edit files, run commands, approve patches, or know facts outside the evidence. "
                "Cite relevant files as [path:start-end]. If evidence is insufficient, say so."
            )},
            *history[-6:],
            {"role": "user", "content": f"Question:\n{message}\n\nRepository evidence:\n{context}"},
        ]

    def _request(self, payload: dict):
        return Request(
            f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST",
        )

    def answer(self, message: str, context: str, history: list[dict[str, str]]) -> str:
        payload = self._payload(message, context, history)
        with urlopen(self._request(payload), timeout=self.timeout_s) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = result["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("empty model answer")
        return answer

    def stream_answer(self, message: str, context: str, history: list[dict[str, str]]) -> Iterator[str]:
        payload = self._payload(message, context, history, stream=True)
        with urlopen(self._request(payload), timeout=self.timeout_s) as response:
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content", "")
                if delta:
                    yield delta


def responder_from_env() -> OpenAICompatibleResponder | None:
    base_url = os.getenv("REPOPILOT_MODEL_BASE_URL")
    api_key = os.getenv("REPOPILOT_API_KEY")
    model = os.getenv("REPOPILOT_MODEL_NAME")
    try:
        max_tokens = min(max(int(os.getenv("REPOPILOT_MODEL_MAX_TOKENS", "1200")), 64), 4_096)
    except ValueError:
        max_tokens = 1_200
    return OpenAICompatibleResponder(base_url, api_key, model, max_tokens=max_tokens) if base_url and api_key and model else None


class CodeRagAssistant:
    """Hybrid retrieval, bounded read-only tools, and optional grounded LLM."""

    TOOL_BY_INTENT = {
        "repository_summary": "summarize_repository",
        "dependency_lookup": "locate_dependencies",
        "test_guidance": "suggest_tests",
        "safety_review": "scan_safety",
        "function_summary": "summarize_function",
        "code_location": "search_code",
        "code_question": "search_code",
    }

    def __init__(self, root: str | Path, conversations: ConversationStore | None = None,
                 responder: OpenAICompatibleResponder | None = None):
        self.root = Path(root).resolve()
        self.repository_key = str(self.root)
        index = CodeIndex(self.root)
        index.build()
        self.retriever = HybridRetriever(index, embedding_provider_from_env())
        self.conversations = conversations or ConversationStore()
        self.responder = responder if responder is not None else responder_from_env()
        self._model_state = "configured" if self.responder else "offline"
        self.tool_registry = CodeToolRegistry()
        descriptions = {
            "search_code": "Locate relevant code symbols and return grounded file/line citations.",
            "summarize_repository": "Summarize the retrieved repository units without claiming full coverage.",
            "summarize_function": "Explain one retrieved function or class from repository evidence.",
            "locate_dependencies": "Inspect imports for the strongest matching code unit.",
            "suggest_tests": "Recommend focused pytest targets from retrieved implementation and tests.",
            "scan_safety": "Run bounded read-only static pattern checks and return cited findings.",
        }
        for name, description in descriptions.items():
            self.tool_registry.register(CodeTool(
                name, description,
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                lambda prepared, tool_name=name: self._run_registered_tool(tool_name, prepared),
            ))

    def model_status(self) -> dict:
        """Return safe runtime metadata without ever exposing credentials."""
        if not self.responder:
            return {"configured": False, "status": "offline", "provider": "offline-evidence", "model": None}
        host = urlparse(getattr(self.responder, "base_url", "")).hostname or ""
        provider = "zhipu" if host.endswith("bigmodel.cn") else "openai-compatible"
        return {"configured": True, "status": self._model_state, "provider": provider,
                "model": self.responder.model}

    def _prepare(self, message: str, conversation_id: str | None, top_k: int) -> PreparedTurn:
        previous = self.conversations.latest_user_message(conversation_id, self.repository_key)
        rewritten_query, rewritten = rewrite_follow_up(message, previous)
        intent = classify_intent(message)
        tool = self.TOOL_BY_INTENT[intent]
        conversation_id = self.conversations.append(conversation_id, "user", message, self.repository_key)
        retrieval_query = expand_retrieval_query(rewritten_query, intent)
        retrieved = self.retriever.search(retrieval_query, top_k=top_k)
        citations = [Citation(item.chunk.path, item.chunk.symbol, item.chunk.start_line, item.chunk.end_line,
                              item.channels, item.chunk.content[:1_200]) for item in retrieved]
        trace = [
            {"event": "intent.classified", "intent": intent},
            {"event": "query.rewritten", "applied": rewritten, "query": rewritten_query if rewritten else message},
            {"event": "tool.selected", "tool": tool, "risk": "read-only"},
            {"event": "retrieval.completed", "sources": len(citations), "query": retrieval_query,
             "channels": sorted({channel for citation in citations for channel in citation.channels})},
        ]
        context = "\n\n".join(f"[{c.path}:{c.start_line}-{c.end_line}] symbol={c.symbol}\n{c.excerpt}" for c in citations)
        return PreparedTurn(conversation_id, intent, tool, rewritten_query, citations,
                            context or "No matching code evidence was found.", trace,
                            self.conversations.history(conversation_id)[:-1])

    def ask(self, message: str, conversation_id: str | None = None, top_k: int = 4) -> ChatAnswer:
        prepared = self._prepare(message, conversation_id, top_k)
        tool_result = self.tool_registry.execute(prepared.tool, prepared)
        trace = [*prepared.trace, {"event": "tool.completed", "tool": tool_result.name,
                                   "duration_ms": tool_result.duration_ms, **tool_result.metadata}]
        grounded_context = f"{prepared.context}\n\nRead-only tool analysis:\n{tool_result.output}"
        provider, fallback = "offline-evidence", False
        if self.responder:
            try:
                answer = self.responder.answer(prepared.rewritten_query, grounded_context, prepared.history)
                provider = f"openai-compatible:{self.responder.model}"
                self._model_state = "online"
            except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                answer, fallback = tool_result.output, True
                provider = f"openai-compatible:{self.responder.model}"
                self._model_state = "fallback"
        else:
            answer = tool_result.output
        self.conversations.append(prepared.conversation_id, "assistant", answer, self.repository_key,
                                  [asdict(citation) for citation in prepared.citations])
        trace.append({"event": "answer.generated", "provider": provider, "fallback": fallback})
        return ChatAnswer(prepared.conversation_id, answer, prepared.citations, provider, fallback, trace,
                          prepared.intent, prepared.tool, prepared.rewritten_query)

    def stream(self, message: str, conversation_id: str | None = None, top_k: int = 4) -> Iterator[tuple[str, dict]]:
        """Yield SSE-friendly metadata and answer deltas; model responses stream token-by-token."""
        prepared = self._prepare(message, conversation_id, top_k)
        tool_result = self.tool_registry.execute(prepared.tool, prepared)
        trace = [*prepared.trace, {"event": "tool.completed", "tool": tool_result.name,
                                   "duration_ms": tool_result.duration_ms, **tool_result.metadata}]
        grounded_context = f"{prepared.context}\n\nRead-only tool analysis:\n{tool_result.output}"
        provider, fallback, parts = "offline-evidence", False, []
        yield "meta", {"conversation_id": prepared.conversation_id, "intent": prepared.intent, "tool": prepared.tool,
                       "rewritten_query": prepared.rewritten_query, "citations": [asdict(c) for c in prepared.citations],
                       "trace": trace, "tool_result": asdict(tool_result)}
        try:
            chunks: Iterator[str]
            if self.responder:
                provider = f"openai-compatible:{self.responder.model}"
                chunks = self.responder.stream_answer(prepared.rewritten_query, grounded_context, prepared.history)
            else:
                text = tool_result.output
                chunks = (text[index:index + 28] for index in range(0, len(text), 28))
            for chunk in chunks:
                parts.append(chunk)
                yield "answer.delta", {"delta": chunk}
        except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            fallback = True
            parts = []
            yield "answer.reset", {"reason": "model_stream_failed"}
            text = tool_result.output
            for index in range(0, len(text), 28):
                chunk = text[index:index + 28]
                parts.append(chunk)
                yield "answer.delta", {"delta": chunk}
        answer = "".join(parts) or tool_result.output
        if self.responder:
            if parts and not fallback:
                self._model_state = "online"
            else:
                fallback = True
                self._model_state = "fallback"
        self.conversations.append(prepared.conversation_id, "assistant", answer, self.repository_key,
                                  [asdict(citation) for citation in prepared.citations])
        trace.append({"event": "answer.generated", "provider": provider, "fallback": fallback})
        yield "complete", {"conversation_id": prepared.conversation_id, "provider": provider, "fallback": fallback,
                           "citations": [asdict(c) for c in prepared.citations], "trace": trace,
                           "tool_result": asdict(tool_result)}

    def _run_registered_tool(self, tool_name: str, prepared: PreparedTurn) -> str:
        references = ", ".join(f"[{c.path}:{c.start_line}-{c.end_line}]" for c in prepared.citations[:3])
        if not prepared.citations:
            return "没有找到可引用的代码证据。请提供函数名、文件名、报错信息或测试名，以便重新检索。"
        if tool_name == "summarize_function":
            lead = prepared.citations[0]
            return f"函数/符号 `{lead.symbol}` 位于 `{lead.path}` 第 {lead.start_line}-{lead.end_line} 行。它的实现已作为证据返回；建议结合其调用方和测试一起阅读。来源：{references}"
        if tool_name == "locate_dependencies":
            imports = self._imports_for(prepared.citations[0].path)
            return f"与问题最相关的实现是 `{prepared.citations[0].symbol}`。文件 `{prepared.citations[0].path}` 导入了：{', '.join(imports) or '未发现显式导入'}。来源：{references}"
        if tool_name == "suggest_tests":
            tests = sorted({citation.path for citation in prepared.citations if '/test' in citation.path or citation.path.startswith('tests/')})
            tests = tests or sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('test_*.py'))[:5]
            return f"建议优先运行与检索结果相同范围的 pytest 目标：{', '.join(tests) or 'tests'}。若要进入修复流程，该目标必须先在原仓库失败。来源：{references}"
        if tool_name == "scan_safety":
            findings = self._safety_findings()
            rendered = "; ".join(findings[:4]) or "未命中内置高风险模式"
            return f"静态安全扫描结果：{rendered}。这只是只读初筛，仍应人工审查数据流。相关代码来源：{references}"
        if tool_name == "summarize_repository":
            symbols = ", ".join(f"`{citation.symbol}`" for citation in prepared.citations[:4])
            return f"与该概览最相关的仓库单元是 {symbols}。这些是基于当前检索得到的局部概览，而不是未经证据支持的全仓库结论。来源：{references}"
        lead = prepared.citations[0]
        return f"最强匹配是 `{lead.symbol}`，位于 `{lead.path}` 第 {lead.start_line}-{lead.end_line} 行。回答基于这些检索到的代码来源：{references}"

    def _imports_for(self, relative_path: str) -> list[str]:
        path = self.root / relative_path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return []
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or ".")
        return sorted(set(imports))[:12]

    def _safety_findings(self) -> list[str]:
        rules = (("eval(", "dynamic eval"), ("exec(", "dynamic exec"), ("shell=True", "shell execution"),
                 ("pickle.loads", "unsafe deserialization"), ("yaml.load(", "unsafe YAML loading"))
        findings: list[str] = []
        for chunk in self.retriever.index.chunks:
            for needle, label in rules:
                if needle in chunk.content:
                    findings.append(f"{label} in {chunk.path}:{chunk.start_line}-{chunk.end_line}")
        return findings

    @staticmethod
    def view(answer: ChatAnswer) -> dict:
        return asdict(answer)
