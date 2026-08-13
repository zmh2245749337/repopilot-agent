"""Planner interfaces with an optional OpenAI-compatible implementation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_STEPS = [
    "Retrieve relevant AST chunks",
    "Reproduce with an allowed test",
    "Propose the smallest patch",
    "Wait for human approval",
    "Verify and review evidence",
]


@dataclass(frozen=True)
class PlanResult:
    steps: list[str]
    provider: str
    fallback: bool = False


class Planner(Protocol):
    def plan(self, issue: str) -> PlanResult: ...


class DeterministicPlanner:
    """Offline planner used for all reproducible demos and safe fallback."""

    def plan(self, issue: str) -> PlanResult:
        return PlanResult(DEFAULT_STEPS, provider="deterministic")


class OpenAICompatiblePlanner:
    """Minimal JSON-only client for a compatible chat-completions endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key, self.model, self.timeout_s = api_key, model, timeout_s

    def plan(self, issue: str) -> PlanResult:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only: {\"steps\":[string,...]}. Plan a safe Python repository diagnosis. Never include a command that writes code."},
                {"role": "user", "content": issue},
            ],
        }
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            steps = json.loads(content).get("steps", [])
            if not isinstance(steps, list) or not all(isinstance(item, str) and item.strip() for item in steps):
                raise ValueError("planner response did not contain string steps")
            return PlanResult(steps[:8], provider=f"openai-compatible:{self.model}")
        except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return PlanResult(DEFAULT_STEPS, provider=f"openai-compatible:{self.model}", fallback=True)


def planner_from_env() -> Planner:
    base_url = os.getenv("REPOPILOT_MODEL_BASE_URL")
    api_key = os.getenv("REPOPILOT_API_KEY")
    model = os.getenv("REPOPILOT_MODEL_NAME")
    if base_url and api_key and model:
        return OpenAICompatiblePlanner(base_url, api_key, model)
    return DeterministicPlanner()
