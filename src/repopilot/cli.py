from __future__ import annotations

import argparse
from pathlib import Path

from .core import RepoPilot


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoPilot evidence-grounded repository agent")
    parser.add_argument("repo", type=Path, help="Python repository to inspect")
    parser.add_argument("issue", help="Issue description or error report")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--propose", action="store_true", help="Create a safe patch proposal when a deterministic rule matches")
    parser.add_argument("--approve", action="store_true", help="Apply the proposal and run tests (explicit human approval)")
    parser.add_argument("--test-target", default="tests")
    args = parser.parse_args()

    pilot = RepoPilot(args.repo)
    state = pilot.analyze(args.issue, args.top_k)
    if args.propose or args.approve:
        pilot.propose_patch(state)
    if args.approve:
        print(pilot.apply_proposal(state).summary)
        test_result = pilot.run_pytest(args.test_target, root=Path(state.workspace) if state.workspace else None)
        pilot.review_task(state, test_result)
    print(pilot.report(state))


if __name__ == "__main__":
    main()
