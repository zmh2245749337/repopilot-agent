"""Read-only conversational code RAG for the RepoPilot dashboard.

This module deliberately cannot call patching tools.  It makes the retrieval
and model layer visible to users while the write-capable repair workflow stays
behind baseline reproduction and human approval.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
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


class ConversationStore:
    """Small in-memory history store for a local dashboard session."""

    def __init__(self):
        self._messages: dict[str, list[dict[str, str]]] = {}

    def append(self, conversation_id: str | None, role: str, content: str) -> str:
        identifier = conversation_id or uuid.uuid4().hex[:12]
        self._messages.setdefault(identifier, []).append({"role": role, "content": content})
        return identifier

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(conversation_id, []))


def classify_intent(message: str) -> str:
    lowered = message.lower()
    if any(word in lowered for word in ("总结", "概览", "summary", "summarize", "overview")):
        return "repository_summary"
    if any(word in lowered for word in ("测试", "test", "pytest")):
        return "test_guidance"
    if any(word in lowered for word in ("安全", "风险", "security", "risk")):
        return "safety_review"
    if any(word in lowered for word in ("在哪", "位置", "where", "locate")):
        return "code_location"
    return "code_question"


class OpenAICompatibleResponder:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 30):
        self.base_url, self.api_key, self.model, self.timeout_s = base_url.rstrip("/"), api_key, model, timeout_s

    def answer(self, message: str, context: str, history: list[dict[str, str]]) -> str:
        messages = [
            {"role": "system", "content": (
                "You are RepoPilot's read-only code assistant. Answer only from the supplied repository evidence. "
                "Do not claim to edit files, run commands, or approve patches. Cite relevant files as [path:start-end]. "
                "If the evidence is insufficient, say what is missing."
            )},
            *history[-6:],
            {"role": "user", "content": f"Question:\n{message}\n\nRepository evidence:\n{context}"},
        ]
        payload = {"model": self.model, "temperature": 0.2, "messages": messages}
        request = Request(
            f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = result["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("empty model answer")
        return answer


def responder_from_env() -> OpenAICompatibleResponder | None:
    base_url = os.getenv("REPOPILOT_MODEL_BASE_URL")
    api_key = os.getenv("REPOPILOT_API_KEY")
    model = os.getenv("REPOPILOT_MODEL_NAME")
    if base_url and api_key and model:
        return OpenAICompatibleResponder(base_url, api_key, model)
    return None


class CodeRagAssistant:
    """Hybrid retrieval plus optional grounded model answer generation."""

    def __init__(self, root: str | Path, conversations: ConversationStore | None = None,
                 responder: OpenAICompatibleResponder | None = None):
        index = CodeIndex(Path(root))
        index.build()
        self.retriever = HybridRetriever(index, embedding_provider_from_env())
        self.conversations = conversations or ConversationStore()
        self.responder = responder if responder is not None else responder_from_env()

    def ask(self, message: str, conversation_id: str | None = None, top_k: int = 4) -> ChatAnswer:
        intent = classify_intent(message)
        conversation_id = self.conversations.append(conversation_id, "user", message)
        retrieved = self.retriever.search(message, top_k=top_k)
        citations = [Citation(
            item.chunk.path, item.chunk.symbol, item.chunk.start_line, item.chunk.end_line,
            item.channels, item.chunk.content[:1_200],
        ) for item in retrieved]
        trace = [
            {"event": "intent.classified", "intent": intent},
            {"event": "retrieval.completed", "sources": len(citations), "channels": sorted({channel for c in citations for channel in c.channels})},
        ]
        context = "\n\n".join(
            f"[{c.path}:{c.start_line}-{c.end_line}] symbol={c.symbol}\n{c.excerpt}" for c in citations
        ) or "No matching code evidence was found."
        provider, fallback = "offline-evidence", False
        if self.responder:
            try:
                answer = self.responder.answer(message, context, self.conversations.history(conversation_id)[:-1])
                provider = f"openai-compatible:{self.responder.model}"
            except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                answer, fallback = self._offline_answer(intent, citations), True
                provider = f"openai-compatible:{self.responder.model}"
        else:
            answer = self._offline_answer(intent, citations)
        self.conversations.append(conversation_id, "assistant", answer)
        trace.append({"event": "answer.generated", "provider": provider, "fallback": fallback})
        return ChatAnswer(conversation_id, answer, citations, provider, fallback, trace)

    @staticmethod
    def _offline_answer(intent: str, citations: list[Citation]) -> str:
        if not citations:
            return "I could not find grounded code evidence for that question. Try a symbol, filename, error message, or test name."
        references = ", ".join(f"[{c.path}:{c.start_line}-{c.end_line}]" for c in citations[:3])
        if intent == "repository_summary":
            symbols = ", ".join(f"`{c.symbol}`" for c in citations[:4])
            return f"The most relevant repository units are {symbols}. Inspect the cited code for implementation details: {references}"
        if intent == "test_guidance":
            return f"Relevant test or implementation evidence was retrieved. Use the cited symbols to choose a focused pytest target; RepoPilot will still require a failing baseline before it can propose a patch: {references}"
        if intent == "safety_review":
            return f"The retrieved code is the evidence to review for the reported risk. RepoPilot's chat layer is read-only; any repair remains gated by baseline reproduction and approval: {references}"
        lead = citations[0]
        return f"The strongest match is `{lead.symbol}` in `{lead.path}` (lines {lead.start_line}-{lead.end_line}). The answer is grounded in these retrieved sources: {references}"

    @staticmethod
    def view(answer: ChatAnswer) -> dict:
        return {**asdict(answer), "citations": [asdict(citation) for citation in answer.citations]}
