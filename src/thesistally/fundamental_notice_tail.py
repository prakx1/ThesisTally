"""Assist an active forward archive queue from its opposite end using immutable URL receipts."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import httpx

from .fundamental_notice_proof import archive_notice
from .fundamental_nse import HEAD, ROOT, digest, write_json

MAX_IN_FLIGHT = 6
SEPARATION = 300
FORWARD_IN_FLIGHT = 14


class ArchiveThrottled(RuntimeError):
    pass


class Throttle:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.stopped = False

    def before(self, request):
        with self.lock:
            if self.stopped:
                raise ArchiveThrottled("Public archive requested backoff")
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = now + delay + 0.8
        time.sleep(delay)
        if self.stopped:
            raise ArchiveThrottled("Public archive requested backoff")

    def after(self, response):
        if response.status_code == 429:
            # Relinquish the entire helper queue. The scheduled forward collector can
            # pick up unattempted work later; do not turn a throttle into a missing filing.
            self.stopped = True
            raise ArchiveThrottled(f"HTTP 429; Retry-After={response.headers.get('Retry-After')}")


def terminal(root, url):
    key = digest(url.encode())
    return (root / "notice-text" / (key + ".json.gz")).exists() or (
        root / "notice-request-failures" / (key + ".json")
    ).exists()


def next_batch(root, plan, forward, reverse, *, wanted=None):
    room = MAX_IN_FLIGHT if wanted is None else wanted
    while forward <= reverse and terminal(root, plan[forward]):
        forward += 1
    # Earlier stragglers can delay the forward progress counter, while the
    # other 13 active workers have moved on. Its next queued index cannot have
    # more than 14 preceding unacknowledged active transfers.
    upper = forward
    missing = 0
    while upper < reverse and missing < FORWARD_IN_FLIGHT:
        if not terminal(root, plan[upper]):
            missing += 1
        upper += 1
    items = []
    while reverse > upper + SEPARATION and len(items) < room:
        url = plan[reverse]
        reverse -= 1
        if not terminal(root, url):
            items.append(url)
    return forward, reverse, items


def main():
    plan = json.loads((ROOT / "notice-request-plan.json").read_bytes())
    throttle = Throttle()
    workers = MAX_IN_FLIGHT
    if (ROOT / "transfer-retry-summary.json").is_file() and (
        ROOT / "structured-mirror-audit-summary.json"
    ).is_file():
        # The independent seven-worker healthy retry now finished. Reassign
        # its transfer slots without increasing the public request frequency.
        workers += 6
    forward, reverse, total = 0, len(plan) - 1, 0
    state = {
        "order": "reverse",
        "workers": workers,
        "rate_limit_per_second": 1.25,
        "forward_buffer": SEPARATION,
        "completed_this_run": 0,
    }
    with (
        httpx.Client(
            headers=HEAD,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            event_hooks={"request": [throttle.before], "response": [throttle.after]},
        ) as client,
        ThreadPoolExecutor(workers) as pool,
    ):
        pending = set()
        exhausted = False
        while pending or not (throttle.stopped or exhausted):
            if not throttle.stopped and not exhausted:
                forward, reverse, urls = next_batch(
                    ROOT, plan, forward, reverse, wanted=workers - len(pending)
                )
                pending.update(pool.submit(archive_notice, client, url) for url in urls)
                exhausted = not urls and len(pending) < workers
            if pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for job in finished:
                    try:
                        job.result()
                        total += 1
                    except ArchiveThrottled as exc:
                        state["stop_reason"] = str(exc)
            state.update(completed_this_run=total, forward_index=forward, reverse_index=reverse)
            write_json(ROOT / "notice-tail-assistance.json", state)
        state.setdefault("stop_reason", "approached_forward_queue_or_all_terminal")
    write_json(ROOT / "notice-tail-assistance.json", state)


if __name__ == "__main__":
    from .fundamental_sources import run

    run("notice-tail")
