"""Retry transient public archive failures with independent health evidence and retained receipts."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from .fundamental_notice_proof import archive_notice
from .fundamental_notice_tail import ArchiveThrottled, Throttle
from .fundamental_nse import HEAD, ROOT, archive_structured, digest, public_copy, write_json

TRANSIENT = re.compile(
    r"err(?:no|or)\s*[0-9]+|timed?\s*out|timeout|temporar|nodename|name resolution|dns|tls|ssl|eof|disconnect|incomplete|connect|readerror|writeerror|reset|unreachable|no route|network|short|closed|cannot read issuer statement pdf|not a zip file|bad crc|no official mirror served a valid attachment|\b(?:408|429|500|502|503|504)\b",
    re.IGNORECASE,
)
MAX_ATTEMPTS = 3


def transient(message):
    # A symbol such as NETWORK18 inside an attachment URL cannot turn its
    # HTTP 404 into a network interruption.
    diagnostic = re.sub(r"https?://\S+", "", str(message))
    return bool(TRANSIENT.search(diagnostic))


def history(root, kind, url):
    folder = root / "transfer-retries" / kind / digest(url.encode())
    return [json.loads(p.read_bytes()) for p in sorted(folder.glob("*.json"))]


def targets(root):
    for p in (root / "notice-request-failures").glob("*.json"):
        url, reason = json.loads(p.read_bytes())
        if transient(reason):
            yield "notice", url, reason
    for name in ["attachment-gaps.json", "dated-alias-attachment-gaps.json"]:
        p = root / name
        if p.is_file():
            for url, reason in json.loads(p.read_bytes()):
                if transient(reason):
                    yield "structured", url, reason


def already_archived(root, kind, url):
    key = digest(url.encode())
    if kind == "notice":
        return any(
            (root / f / (key + ".json.gz")).exists() for f in ["notice-media", "notice-text"]
        )
    return (root / "receipts" / (key + ".json")).exists()


def probe(client):
    # Independently compare public bytes with the pinned bytes of a small, published
    # original. A DNS/captive-portal failure cannot turn a whole group of IDs into 404s.
    attempts = 0
    for p in sorted((ROOT / "receipts").glob("*.json"))[:60]:
        row = json.loads(p.read_bytes())
        if row.get("bytes", 999999) > 55000:
            continue
        attempts += 1
        if attempts > 3:
            break
        try:
            raw, actual = public_copy(
                client, row["url"], accept=lambda b, sha=row["sha256"]: digest(b) == sha
            )
            return {
                "url": row["url"],
                "retrieved_url": actual,
                "sha256": digest(raw),
                "at": datetime.now(UTC).isoformat(),
            }
        except (ValueError, httpx.HTTPError):
            continue
    raise ValueError("No independently pinned official source responded with matching content")


def eligible_attempts(previous):
    if len(previous) >= MAX_ATTEMPTS:
        return False
    if not previous:
        return True
    return previous[-1]["status"] == "transient" or transient(
        previous[-1].get("outcome_error") or ""
    )


def retry_one(client, health, target):
    kind, url, first = target
    if already_archived(ROOT, kind, url):
        return
    previous = history(ROOT, kind, url)
    if not eligible_attempts(previous):
        return
    error = (
        archive_notice(client, url, retry=True)
        if kind == "notice"
        else archive_structured(client, url)
    )
    fraction = "recovered" if not error else "transient" if transient(error[1]) else "unavailable"
    rec = {
        "kind": kind,
        "url": url,
        "original_error": first,
        "status": fraction,
        "outcome_error": error[1] if error else None,
        "health": health,
        "at": datetime.now(UTC).isoformat(),
        "attempt": len(previous) + 1,
    }
    path = (
        ROOT / "transfer-retries" / kind / digest(url.encode()) / (f"{len(previous) + 1:02}.json")
    )
    if path.exists():
        raise ValueError("Concurrent retry worker for the same official URL")
    write_json(path, rec)


def main():
    throttle = Throttle()
    workers = (
        14
        if (ROOT / "notice-document-gaps.json").exists()
        else (7 if (ROOT / "notice-alias-document-gaps.json").exists() else 4)
    )
    with (
        httpx.Client(
            headers=HEAD,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            event_hooks={"request": [throttle.before], "response": [throttle.after]},
        ) as client,
        ThreadPoolExecutor(workers) as pool,
    ):
        for wave in range(1, MAX_ATTEMPTS + 1):
            candidates = []
            for t in sorted(set(targets(ROOT)), key=lambda t: (t[0] != "structured", t[1])):
                kind, url, _ = t
                if already_archived(ROOT, kind, url):
                    continue
                attempts = history(ROOT, kind, url)
                if eligible_attempts(attempts):
                    candidates.append(t)
            if not candidates:
                break
            try:
                health = probe(client)
                for offset in range(0, len(candidates), 150):
                    if offset:
                        health = probe(client)
                    list(
                        pool.map(
                            lambda t, h=health: retry_one(client, h, t),
                            candidates[offset : offset + 150],
                        )
                    )
            except ValueError as exc:
                if (
                    str(exc)
                    != "No independently pinned official source responded with matching content"
                ):
                    raise
                moment = datetime.now(UTC).isoformat()
                write_json(
                    ROOT / "transfer-health" / (digest(moment.encode()) + ".json"),
                    {
                        "at": moment,
                        "status": "no_matching_official_probe",
                        "pending": len(candidates),
                    },
                )
                break
            except ArchiveThrottled:
                print(
                    "Exchange throttled recovery. Remaining URLs retain pending status.", flush=True
                )
                break
            print("Healthy recovery wave", wave, len(candidates), flush=True)
            if wave < MAX_ATTEMPTS:
                time.sleep(30)
    outcomes = Counter()
    for kind, url, _ in set(targets(ROOT)):
        h = history(ROOT, kind, url)
        state = (
            "archived" if already_archived(ROOT, kind, url) else h[-1]["status"] if h else "pending"
        )
        outcomes[(kind, state)] += 1
    write_json(
        ROOT / "transfer-retry-summary.json",
        {
            "outcomes": [
                {"kind": k, "status": s, "count": v} for (k, s), v in sorted(outcomes.items())
            ],
            "max_attempts": MAX_ATTEMPTS,
        },
    )


if __name__ == "__main__":
    from .fundamental_sources import run

    run("recover-transfers")
