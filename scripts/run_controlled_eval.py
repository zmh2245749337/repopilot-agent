"""Run every bundled controlled Issue-to-Patch case and write a JSON summary."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from repopilot.core import RepoPilot


ROOT = Path(__file__).parents[1]
CASES = json.loads((ROOT / "eval" / "controlled_cases.json").read_text(encoding="utf-8"))


def main() -> None:
    results = []
    for case in CASES:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / case["case"]
            shutil.copytree(ROOT / "demo" / "cases" / case["case"], root)
            pilot = RepoPilot(root)
            state = pilot.analyze(case["issue"])
            proposal = pilot.propose_patch(state)
            applied = pilot.apply_proposal(state) if proposal else None
            test = pilot.run_pytest(case["test_target"], root=Path(state.workspace)) if applied and applied.ok else None
            review = pilot.review_task(state, test) if test else None
            results.append({"case": case["case"], "status": state.status.value, "proposal": bool(proposal),
                            "test_passed": test.ok if test else False, "review": review.decision if review else None})
    output = ROOT / "reports" / "controlled_eval.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
