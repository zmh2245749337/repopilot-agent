"""Verify the configured model without sending repository content."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repopilot.chat import responder_from_env


def main() -> None:
    responder = responder_from_env()
    if not responder:
        raise SystemExit("Model is not configured; set REPOPILOT_MODEL_BASE_URL, REPOPILOT_MODEL_NAME and REPOPILOT_API_KEY")
    try:
        answer = responder.answer(
            "This is a connectivity smoke test. Reply with REPOPILOT_GLM_OK.",
            "Synthetic connectivity evidence only. No repository content is included.",
            [],
        )
    except HTTPError as error:
        reason = "rate_limited" if error.code == 429 else "provider_http_error"
        print(json.dumps({"connected": False, "model": responder.model,
                          "http_status": error.code, "reason": reason}, indent=2))
        raise SystemExit(2) from None
    except (URLError, TimeoutError):
        print(json.dumps({"connected": False, "model": responder.model,
                          "reason": "network_or_timeout"}, indent=2))
        raise SystemExit(2) from None
    result = {"connected": "REPOPILOT_GLM_OK" in answer, "model": responder.model,
              "answer_preview": answer[:200]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["connected"]:
        raise SystemExit("Configured model did not pass the connectivity smoke test")


if __name__ == "__main__":
    main()
