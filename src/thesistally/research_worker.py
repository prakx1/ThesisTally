"""Drain committed observation jobs in a background process shared by all launchers."""

import fcntl
import logging
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from .journal import journal
from .research_journal import configured_provider


def start_pending(root, *, after_scan=False):
    root = Path(root).resolve()
    if configured_provider(root) == "off" or not (root / "journal.sqlite").is_file():
        return
    with closing(journal(root / "journal.sqlite")) as db:
        if not db.execute(
            "SELECT 1 FROM ai_work WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone():
            return
    if not after_scan:
        with (root / ".research-worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
    # Each new scan wakes a worker even if the previous worker is just exiting.
    # The OS lock below serializes them and is released automatically on a crash.
    try:
        with (root / "research-worker.log").open("a") as log:
            return subprocess.Popen(
                [sys.executable, "-m", "thesistally.research_worker", str(root)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Research jobs remain queued. Worker launch failed: %s", exc
        )


def drain_pending(root):
    from .research import process_queue

    with (root / ".research-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        while process_queue(root):
            pass


if __name__ == "__main__":
    drain_pending(Path(sys.argv[1]))
