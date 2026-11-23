import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "scripts/run_verified_a126.sh"


@pytest.mark.parametrize("changed", ["uv.lock", "compulsory-actions.json", "strategy.py"])
def test_release_guard_checks_tracked_inputs_before_starting_paper_runner(tmp_path, changed):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    for p in ("uv.lock", "compulsory-actions.json", "strategy.py"):
        (tmp_path / p).write_text("original\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=test@example.invalid", "commit", "-qm", "seed")
    tag = "paper-test"
    git("tag", tag)
    commit = git("rev-parse", "HEAD")
    args = ["/bin/sh", str(GUARD), str(tmp_path), tag, commit]
    radar = tmp_path / ".venv/bin/radar"
    radar.parent.mkdir(parents=True)
    radar.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    radar.chmod(0o700)
    assert subprocess.check_output(args, text=True).strip() == "forward-update"
    (tmp_path / changed).write_text("changed\n")
    for staged in [False, True]:
        if staged:
            git("add", changed)
        run = subprocess.run(args, check=False, text=True, capture_output=True)
        assert run.returncode != 0
        assert not run.stdout
        assert "tracked release file differs" in run.stderr
