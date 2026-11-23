"""Run the offline results-study calculations with writes confined to the fundamentals tree."""

import argparse
import os
import sys

from .discovery_guard import REPO, confined, output_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["coverage", "signals", "replay"])
    args = parser.parse_args()
    if not (REPO / ".git").is_file():
        raise ValueError("Run the results study from its linked research worktree")
    root = output_root(workstream="fundamentals")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(root),
    }
    module = {
        "coverage": "fundamental_coverage",
        "signals": "results_signals",
        "replay": "results_replay",
    }[args.operation]
    command = [sys.executable, "-B", "-m", "thesistally." + module]
    result = confined(command, root, env=env, check=False)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    print(result.stdout, end="")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
