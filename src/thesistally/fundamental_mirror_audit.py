"""Recheck unresolved legacy requests which recorded only the last official mirror's error."""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from .fundamental_notice_proof import archive_notice
from .fundamental_notice_tail import ArchiveThrottled, Throttle
from .fundamental_nse import HEAD, ROOT, archive_structured, digest, write_json
from .fundamental_retry import MAX_ATTEMPTS, already_archived, history, probe, transient


def audit_history(root, kind, url):
    path = root / "mirror-audits" / kind / digest(url.encode())
    return [json.loads(p.read_bytes()) for p in sorted(path.glob("*.json"))]


def outcomes(root):
    for name in ["attachment-gaps.json", "dated-alias-attachment-gaps.json"]:
        path = root / name
        if path.exists():
            for url, error in json.loads(path.read_bytes()):
                yield "structured", url, error
    for path in (root / "notice-request-failures").glob("*.json"):
        url, error = json.loads(path.read_bytes())
        yield "notice", url, error


def needs_check(root, kind, url, original):
    if already_archived(root, kind, url):
        return False
    audits = audit_history(root, kind, url)
    if audits:
        return audits[-1]["status"] == "transient" and len(audits) < MAX_ATTEMPTS
    previous = history(root, kind, url)
    latest = (previous[-1].get("outcome_error") if previous else None) or original
    if latest.startswith("Official archive responses:") and not transient(latest):
        return False
    # A previous document parse or specifically unsupported format is already
    # accounted for by the original raw receipt and media-folio recovery.
    return bool(transient(latest) or "404" in latest or "403" in latest or "410" in latest)


def check(client, health, candidate):
    kind, url, original = candidate
    previous = audit_history(ROOT, kind, url)
    failed = (
        archive_notice(client, url, retry=True)
        if kind == "notice"
        else archive_structured(client, url)
    )
    error = failed[1] if failed else None
    status = "archived" if not failed else "transient" if transient(error) else "unavailable"
    new = {
        "url": url,
        "kind": kind,
        "initial_error": original,
        "status": status,
        "response": error,
        "health": health,
        "attempt": len(previous) + 1,
        "at": datetime.now(UTC).isoformat(),
    }
    path = ROOT / "mirror-audits" / kind / digest(url.encode()) / f"{new['attempt']:02}.json"
    if path.exists():
        raise ValueError("Duplicate active archive audit for one source")
    write_json(path, new)


def legacy_tables():
    return main(only_terminal_tables=True)


def main(only_terminal_tables=False):
    throttle = Throttle()
    counts = Counter()
    with (
        httpx.Client(
            headers=HEAD,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            event_hooks={"request": [throttle.before], "response": [throttle.after]},
        ) as client,
        ThreadPoolExecutor(1 if only_terminal_tables else 3) as pool,
    ):
        for _ in range(MAX_ATTEMPTS):
            candidates = []
            for c in sorted(set(outcomes(ROOT))):
                kind, url, first = c
                if only_terminal_tables:
                    if kind != "structured":
                        continue
                    prior = history(ROOT, kind, url)
                    last = (prior[-1].get("outcome_error") if prior else None) or first
                    if transient(last):
                        continue
                if needs_check(ROOT, *c):
                    candidates.append(c)
            if not candidates:
                break
            for start in range(0, len(candidates), 150):
                try:
                    health = probe(client)
                    list(
                        pool.map(
                            lambda c, h=health: check(client, h, c), candidates[start : start + 150]
                        )
                    )
                except ArchiveThrottled as error:
                    print(str(error), flush=True)
                    return
    for p in (ROOT / "mirror-audits").glob("*/*"):
        if p.is_dir():
            latest = max(p.glob("*.json"), default=None)
            if latest:
                result = json.loads(latest.read_bytes())
                counts[(result["kind"], result["status"])] += 1
    name = (
        "structured-mirror-audit-summary.json"
        if only_terminal_tables
        else "mirror-audit-summary.json"
    )
    write_json(
        ROOT / name,
        {
            "outcomes": [
                {"kind": k, "status": s, "count": n} for (k, s), n in sorted(counts.items())
            ]
        },
    )
