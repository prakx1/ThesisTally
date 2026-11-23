"""Run A126 with its tagged engine when the research checkout has moved on."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from . import frozen_forward, market

# The experiment that froze this commit recorded it before Strategy C existed.
FROZEN_COMMIT = "dc8e45c791c4f8485eaa869a6985f492805039b3"
RUNNER = """
import json, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from thesistally import frozen_forward
root = Path(sys.argv[2])
if sys.argv[3] == 'report':
    result = frozen_forward.report(root)
else:
    through = date.fromisoformat(sys.argv[4]) if sys.argv[4] else None
    result = frozen_forward.catch_up(root, through, collect_missing=True)
print(json.dumps(result))
"""


def invoke(root, *, update=False, through=None):
    cfg = json.loads(frozen_forward.REGISTRATION.read_bytes())
    if frozen_forward.engine_sha256() == cfg["engine_sha256"]:
        if update:
            return frozen_forward.catch_up(root, through, collect_missing=True)
        return frozen_forward.report(root)

    repo = frozen_forward.PACKAGE.parents[1]
    # Use a temporary local checkout of the published code. Its original registration,
    # source fingerprint and tag checks still run inside the isolated interpreter.
    ref = "refs/tags/" + cfg["tag"]
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", ref + "^{commit}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if (
        result.returncode
        or result.stdout.strip() != FROZEN_COMMIT
        or not frozen_forward.tagged(cfg)
    ):
        raise market.MarketError("The locally available A126 tag differs from the frozen release.")
    with tempfile.TemporaryDirectory(prefix="thesistally-a126-") as tmp:
        subprocess.run(
            ["git", "clone", "-q", "--local", "--shared", "--no-checkout", str(repo), tmp],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                tmp,
                "restore",
                "--source",
                FROZEN_COMMIT,
                "--worktree",
                "--",
                "src/thesistally",
            ],
            check=True,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                RUNNER,
                str(Path(tmp) / "src"),
                str(root.resolve()),
                "update" if update else "report",
                str(through) if through else "",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            reason = result.stderr.strip().splitlines()
            raise market.MarketError(reason[-1] if reason else "Frozen paper process failed")
        return json.loads(result.stdout)
