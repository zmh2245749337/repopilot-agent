"""Evaluate deterministic Code RAG retrieval, routing, and source citations."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repopilot.chat import CodeRagAssistant


def main() -> None:
    cases = json.loads((ROOT / "eval" / "rag_cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        answer = CodeRagAssistant(ROOT / "demo" / "cases" / case["case"]).ask(case["query"])
        paths = [citation.path for citation in answer.citations]
        symbols = [citation.symbol for citation in answer.citations]
        results.append({
            "case": case["case"],
            "source_hit": case["expected_path"] in paths,
            "symbol_hit": case["expected_symbol"] in symbols,
            "intent_hit": answer.intent == case["expected_intent"],
            "tool_hit": answer.tool == case["expected_tool"],
            "citations": paths,
        })
    output = ROOT / "reports" / "rag_eval.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    if not all(all(item[key] for key in ("source_hit", "symbol_hit", "intent_hit", "tool_hit")) for item in results):
        raise SystemExit("RAG evaluation failed; inspect reports/rag_eval.json")


if __name__ == "__main__":
    main()
