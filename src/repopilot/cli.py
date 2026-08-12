import argparse
from pathlib import Path

from .core import RepoPilot


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoPilot repository analysis MVP")
    parser.add_argument("repo", type=Path)
    parser.add_argument("issue")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    pilot = RepoPilot(args.repo)
    state = pilot.analyze(args.issue, args.top_k)
    print(f"status={state.status.value}")
    print(pilot.evidence.to_json())


if __name__ == "__main__":
    main()

