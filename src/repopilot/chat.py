"""Read-only conversational code RAG and bounded tool routing.

The chat layer may inspect a repository but deliberately has no patch-writing
tool.  Repository changes remain behind the separate approval workflow.
"""
from __future__ import annotations

import ast
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator
from urllib.error import URLError
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


class ConversationStore:
    """In-memory history for a local dashboard session."""

    def __init__(self):
        self._messages: dict[str, list[dict[str, str]]] = {}

    def append(self, conversation_id: str | None, role: str, content: str) -> str:
        identifier = conversation_id or uuid.uuid4().hex[:12]
        self._messages.setdefault(identifier, []).append({"role": role, "content": content})
        return identifier

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(conversation_id, []))

    def latest_user_message(self, conversation_id: str | None) -> str | None:
        for message in reversed(self._messages.get(conversation_id or "", [])):
            if message["role"] == "user":
                return message["content"]
        return None


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
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 30):
        self.base_url, self.api_key, self.model, self.timeout_s = base_url.rstrip("/"), api_key, model, timeout_s

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
        payload = {"model": self.model, "temperature": 0.2, "messages": self._messages(message, context, history)}
        with urlopen(self._request(payload), timeout=self.timeout_s) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = result["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("empty model answer")
        return answer

    def stream_answer(self, message: str, context: str, history: list[dict[str, str]]) -> Iterator[str]:
        payload = {"model": self.model, "temperature": 0.2, "stream": True, "messages": self._messages(message, context, history)}
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
    return OpenAICompatibleResponder(base_url, api_key, model) if base_url and api_key and model else None


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
        index = CodeIndex(self.root)
        index.build()
        self.retriever = HybridRetriever(index, embedding_provider_from_env())
        self.conversations = conversations or ConversationStore()
        self.responder = responder if responder is not None else responder_from_env()

    def _prepare(self, message: str, conversation_id: str | None, top_k: int) -> PreparedTurn:
        previous = self.conversations.latest_user_message(conversation_id)
        rewritten_query, rewritten = rewrite_follow_up(message, previous)
        intent = classify_intent(message)
        tool = self.TOOL_BY_INTENT[intent]
        conversation_id = self.conversations.append(conversation_id, "user", message)
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
        provider, fallback = "offline-evidence", False
        if self.responder:
            try:
                answer = self.responder.answer(prepared.rewritten_query, prepared.context, prepared.history)
                provider = f"openai-compatible:{self.responder.model}"
            except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                answer, fallback = self._tool_answer(prepared), True
                provider = f"openai-compatible:{self.responder.model}"
        else:
            answer = self._tool_answer(prepared)
        self.conversations.append(prepared.conversation_id, "assistant", answer)
        trace = [*prepared.trace, {"event": "answer.generated", "provider": provider, "fallback": fallback}]
        return ChatAnswer(prepared.conversation_id, answer, prepared.citations, provider, fallback, trace,
                          prepared.intent, prepared.tool, prepared.rewritten_query)

    def stream(self, message: str, conversation_id: str | None = None, top_k: int = 4) -> Iterator[tuple[str, dict]]:
        """Yield SSE-friendly metadata and answer deltas; model responses stream token-by-token."""
        prepared = self._prepare(message, conversation_id, top_k)
        provider, fallback, parts = "offline-evidence", False, []
        yield "meta", {"conversation_id": prepared.conversation_id, "intent": prepared.intent, "tool": prepared.tool,
                       "rewritten_query": prepared.rewritten_query, "citations": [asdict(c) for c in prepared.citations],
                       "trace": prepared.trace}
        try:
            chunks: Iterator[str]
            if self.responder:
                provider = f"openai-compatible:{self.responder.model}"
                chunks = self.responder.stream_answer(prepared.rewritten_query, prepared.context, prepared.history)
            else:
                text = self._tool_answer(prepared)
                chunks = (text[index:index + 28] for index in range(0, len(text), 28))
            for chunk in chunks:
                parts.append(chunk)
                yield "answer.delta", {"delta": chunk}
        except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            fallback = True
            parts = []
            yield "answer.reset", {"reason": "model_stream_failed"}
            text = self._tool_answer(prepared)
            for index in range(0, len(text), 28):
                chunk = text[index:index + 28]
                parts.append(chunk)
                yield "answer.delta", {"delta": chunk}
        answer = "".join(parts) or self._tool_answer(prepared)
        self.conversations.append(prepared.conversation_id, "assistant", answer)
        trace = [*prepared.trace, {"event": "answer.generated", "provider": provider, "fallback": fallback}]
        yield "complete", {"conversation_id": prepared.conversation_id, "provider": provider, "fallback": fallback,
                           "citations": [asdict(c) for c in prepared.citations], "trace": trace}

    def _tool_answer(self, prepared: PreparedTurn) -> str:
        references = ", ".join(f"[{c.path}:{c.start_line}-{c.end_line}]" for c in prepared.citations[:3])
        if not prepared.citations:
            return "没有找到可引用的代码证据。请提供函数名、文件名、报错信息或测试名，以便重新检索。"
        if prepared.tool == "summarize_function":
            lead = prepared.citations[0]
            return f"函数/符号 `{lead.symbol}` 位于 `{lead.path}` 第 {lead.start_line}-{lead.end_line} 行。它的实现已作为证据返回；建议结合其调用方和测试一起阅读。来源：{references}"
        if prepared.tool == "locate_dependencies":
            imports = self._imports_for(prepared.citations[0].path)
            return f"与问题最相关的实现是 `{prepared.citations[0].symbol}`。文件 `{prepared.citations[0].path}` 导入了：{', '.join(imports) or '未发现显式导入'}。来源：{references}"
        if prepared.tool == "suggest_tests":
            tests = sorted({citation.path for citation in prepared.citations if '/test' in citation.path or citation.path.startswith('tests/')})
            tests = tests or sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('test_*.py'))[:5]
            return f"建议优先运行与检索结果相同范围的 pytest 目标：{', '.join(tests) or 'tests'}。若要进入修复流程，该目标必须先在原仓库失败。来源：{references}"
        if prepared.tool == "scan_safety":
            findings = self._safety_findings()
            rendered = "; ".join(findings[:4]) or "未命中内置高风险模式"
            return f"静态安全扫描结果：{rendered}。这只是只读初筛，仍应人工审查数据流。相关代码来源：{references}"
        if prepared.tool == "summarize_repository":
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
