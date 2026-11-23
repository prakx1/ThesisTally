"""Recover broken NSE catalogue links with the period's documented ticker and the same accession."""

from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict
from datetime import date
from functools import lru_cache
from urllib.parse import quote, unquote, urlsplit

import httpx

from .discovery_worker import archive_root
from .fundamental_facts import Cells
from .fundamental_identity import exchange_equity
from .fundamental_mirror_audit import audit_history
from .fundamental_notice_tail import Throttle
from .fundamental_nse import HEAD, ROOT, digest, load_plans, members, pack, public_copy, write_json
from .fundamental_retry import history


def header_matches(raw, row, old):
    parser = Cells()
    parser.feed(raw.decode("latin-1"))
    fields = [" ".join(s.lower().split()) for s in parser.values if s]

    def meta(key):
        return (
            fields[fields.index(key) + 1]
            if key in fields and fields.index(key) + 1 < len(fields)
            else None
        )

    scope = {
        "standalone": "standalone",
        "non-consolidated": "standalone",
        "consolidated": "consolidated",
    }
    return (
        meta("symbol") == old.lower()
        and meta("period ended") == row["metadata"]["toDate"].lower()
        and scope.get(meta("consolidated / non-consolidated")) == row["basis"]
        and meta("period") == row["metadata"]["period"].lower()
        and meta("cumulative / non-cumulative") == row["metadata"]["cumulative"].lower()
    )


def candidate_urls(row, symbols):
    url = row["source_url"] or ""
    seq = str(row["metadata"].get("seqNumber") or "")
    suffix = r"/financial_res_([^/]+)_" + re.escape(seq) + r"\.html$"
    matched = re.search(suffix, unquote(url))
    if not matched or not seq or matched[1] != row["symbol"]:
        return []
    prefix = url.rsplit("/", 1)[0]
    return [
        (s, prefix + "/financial_res_" + quote(s, safe="&-_.") + "_" + seq + ".html")
        for s in sorted(symbols)
        if s != row["symbol"]
    ]


def main():
    plans = load_plans(ROOT)
    by_url = {
        r["source_url"]: r
        for r in plans
        if r.get("source_url") and r["metadata"].get("isin") == r["isin"]
    }
    aliases = defaultdict(set)
    for m in members():
        aliases[m["isin"]].add(m["symbol"])
    base = archive_root() / "baseline/market/sources/nse-daily"
    days = sorted(p.name for p in base.iterdir() if p.is_dir() and len(p.name) == 10)

    @lru_cache(maxsize=75)
    def trading(day):
        folder = base / day
        src = json.loads((folder / "sources.json").read_bytes())["bhavcopy.zip"]
        raw = (folder / "bhavcopy.zip").read_bytes()
        if digest(raw) != src["sha256"] or urlsplit(src["url"]).hostname not in {
            "archives.nseindia.com",
            "nsearchives.nseindia.com",
            "nsearchives.nseindia.in",
        }:
            raise ValueError("Changed historical security identity evidence")
        return exchange_equity(raw, date.fromisoformat(day)), src

    jobs = {}
    for fn in ["attachment-gaps.json", "dated-alias-attachment-gaps.json"]:
        p = ROOT / fn
        if not p.exists():
            continue
        for url, error in json.loads(p.read_bytes()):
            prior = history(ROOT, "structured", url)
            mirrors = audit_history(ROOT, "structured", url)
            if url in by_url and (
                "404" in error
                or any("404" in str(q["outcome_error"]) for q in prior)
                or any("HTTP 404" in str(q.get("response")) for q in mirrors)
            ):
                jobs[url] = by_url[url]
    throttle = Throttle()
    with httpx.Client(
        headers=HEAD,
        timeout=httpx.Timeout(30, connect=12),
        follow_redirects=True,
        event_hooks={"request": [throttle.before], "response": [throttle.after]},
    ) as client:
        for url, row in sorted(jobs.items()):
            key = digest(url.encode())
            receipt = ROOT / "receipts" / (key + ".json")
            if receipt.exists():
                continue
            record = ROOT / "historical-path-requests" / (key + ".json")
            if record.exists() and json.loads(record.read_bytes()).get("terminal"):
                continue
            idx = bisect.bisect_right(days, row["period_end"]) - 1
            if (
                idx < 0
                or (date.fromisoformat(row["period_end"]) - date.fromisoformat(days[idx])).days > 7
            ):
                continue
            day = days[idx]
            equity, src = trading(day)
            valid = {
                s for s in aliases[row["isin"]] if equity[row["isin"][3:7]] == {(s, row["isin"])}
            }
            checked = []
            for old, proposed in candidate_urls(row, valid):
                try:
                    raw, actual = public_copy(
                        client, proposed, accept=lambda b, r=row, s=old: header_matches(b, r, s)
                    )
                    sha = digest(raw)
                    pack(ROOT / "objects" / sha[:2] / (sha + ".gz"), raw)
                    witness = {
                        "period_end": row["period_end"],
                        "isin": row["isin"],
                        "symbol": old,
                        "market_session": day,
                        "market_source": src["url"],
                        "market_sha256": src["sha256"],
                        "exchange_accession": row["metadata"]["seqNumber"],
                    }
                    write_json(
                        receipt,
                        {
                            "url": url,
                            "retrieved_url": actual,
                            "sha256": sha,
                            "bytes": len(raw),
                            "source_security_proof": witness,
                        },
                    )
                    checked.append(
                        {"url": proposed, "retrieved": actual, "sha256": sha, "status": "recovered"}
                    )
                    break
                except (httpx.HTTPError, ValueError) as e:
                    checked.append({"url": proposed, "error": str(e)[:1200]})
            if checked:
                from .fundamental_retry import transient

                write_json(
                    record,
                    {
                        "canonical": url,
                        "attempts": (
                            json.loads(record.read_bytes()).get("attempts", [])
                            if record.exists()
                            else []
                        )
                        + checked,
                        "terminal": not any(transient(q.get("error", "")) for q in checked),
                    },
                )
