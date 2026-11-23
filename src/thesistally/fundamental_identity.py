"""Certify old exchange issuer aliases against historical ordinary-equity bhavcopies."""

from __future__ import annotations

import bisect
import csv
import io
import json
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlsplit

from .discovery_worker import archive_root
from .fundamental_nse import REPO, ROOT, digest, load_plans, members, write_json
from .history import extract_bhav


def exchange_equity(raw, day):
    table = csv.DictReader(io.StringIO(extract_bhav(raw).decode("utf-8-sig")))
    identities = defaultdict(set)
    for row in table:
        if (row.get("SERIES") or row.get("SctySrs") or "").strip() != "EQ":
            continue
        actual = (row.get("TradDt") or row.get("TIMESTAMP") or "").strip().upper()
        if actual not in {
            str(day),
            day.strftime("%d-%b-%Y").upper(),
            day.strftime("%d-%b-%y").upper(),
        }:
            raise ValueError("Bhavcopy identity has a different trading day")
        isin = row["ISIN"].strip()
        symbol = (row.get("SYMBOL") or row.get("TckrSymb") or "").strip()
        if len(isin) == 12 and isin[:2] == "IN":
            identities[isin[3:7]].add((symbol, isin))
    return identities


def regular_sessions(archive):
    """Keep a known trading session on the calendar even when its raw bhavcopy is missing."""
    return {
        date.fromisoformat(p.name)
        for segment in ("baseline", "pilot")
        for p in (archive / segment / "market/sessions").iterdir()
        if p.is_dir() and len(p.name) == 10
    }


def date_witness(source, requests, *, supplement=None, calendar_days=()):
    """Use reports published by the as-of date. Handle an intraday filing with yesterday's tape."""
    folders = {
        date.fromisoformat(f.name): f for f in source.iterdir() if f.is_dir() and len(f.name) == 10
    }
    if supplement and supplement.is_dir():
        for folder in supplement.iterdir():
            if folder.is_dir() and len(folder.name) == 10 and (folder / "sources.json").is_file():
                day = date.fromisoformat(folder.name)
                if day not in folders or not (folders[day] / "sources.json").is_file():
                    folders[day] = folder
    days = sorted(set(folders).union(calendar_days))
    needed = defaultdict(set)
    for symbol, isin, end, first in requests:
        endpoint, at = date.fromisoformat(end), date.fromisoformat(first)
        wanted = []
        for last in (min(endpoint, at - timedelta(days=1)), at - timedelta(days=1)):
            idx = bisect.bisect_right(days, last) - 1
            if idx < 0 or (last - days[idx]).days > 7:
                break
            wanted.append(days[idx])
        if len(wanted) == 2:
            for day in wanted:
                needed[day].add((symbol, isin))
    out = {}
    for day, securities in sorted(needed.items()):
        folder = folders.get(day)
        if not folder or not (folder / "sources.json").is_file():
            continue
        receipt = json.loads((folder / "sources.json").read_bytes()).get("bhavcopy.zip") or {}
        raw = (folder / "bhavcopy.zip").read_bytes()
        url = receipt.get("url", "")
        actual = digest(raw)
        if actual != receipt.get("sha256") or urlsplit(url).hostname not in {
            "nsearchives.nseindia.com",
            "archives.nseindia.com",
            "nsearchives.nseindia.in",
        }:
            raise ValueError("Historical bhavcopy fingerprint or official URL mismatch")
        by_issuer = exchange_equity(raw, day)
        verified = {sym: isin for sym, isin in securities if by_issuer[isin[3:7]] == {(sym, isin)}}
        out[str(day)] = {"url": url, "sha256": actual, "equities": verified}
    return out, [str(d) for d in days]


def build():
    requests = set()
    from .fundamental_notice_proof import candidates, firsts, index_notices

    notices = index_notices()
    plans = load_plans(ROOT)
    for group in firsts(plans).values():
        row = group[0]
        if row.get("identity") != "require_original_isin":
            continue
        earlier = candidates(row, notices[row["symbol"]])
        if earlier:
            requests.add(
                (row["symbol"], row["isin"], row["period_end"], earlier[0]["announced_at"][:10])
            )
    source = archive_root() / "baseline/market/sources/nse-daily"
    result, calendar = date_witness(
        source,
        requests,
        supplement=ROOT / "identity-daily",
        calendar_days=regular_sessions(archive_root()),
    )
    write_json(
        ROOT / "identity-bhavcopies.json",
        {
            "membership_sha256": digest(
                (REPO / "data/universes/nifty200_membership.csv").read_bytes()
            ),
            "bhavcopies": result,
            "calendar": calendar,
        },
    )


def identity_witness(row, release, days, calendar, intervals):
    """Two exchange trading dates, both before publication, must resolve one EQ class for this issuer."""
    symbol, isin = row["symbol"], row["isin"]
    alias = row["metadata"].get("isin") or ""
    notice = release.get("isin") or ""
    if (
        row.get("identity") != "require_original_isin"
        or release.get("symbol") != symbol
        or not isin.startswith("INE")
        or not (alias[:7] == isin[:7] == notice[:7])
    ):
        return None
    end, at = (
        date.fromisoformat(row["period_end"]),
        date.fromisoformat(release["announced_at"][:10]),
    )
    if not any(a <= row["period_end"] <= b for a, b in intervals.get((symbol, isin), ())):
        return None
    evidence = []
    for latest in (min(end, at - timedelta(days=1)), at - timedelta(days=1)):
        idx = bisect.bisect_right(calendar, str(latest)) - 1
        if idx < 0:
            return None
        d = calendar[idx]
        src = days.get(d)
        if (
            not src
            or (latest - date.fromisoformat(d)).days > 7
            or src["equities"].get(symbol) != isin
        ):
            return None
        evidence.append({"day": d, "url": src["url"], "sha256": src["sha256"]})
    return {
        "method": "unique_ordinary_equity_in_both_dated_bhavcopies_plus_original_legal_heading",
        "catalogue_isin": alias,
        "notice_isin": notice,
        "traded_isin": isin,
        "symbol": symbol,
        "evidence": evidence,
    }


def load():
    p = ROOT / "identity-bhavcopies.json"
    if not p.is_file():
        return {}, [], {}
    cert = json.loads(p.read_bytes())
    if cert["membership_sha256"] != digest(
        (REPO / "data/universes/nifty200_membership.csv").read_bytes()
    ):
        raise ValueError("Historical membership differs from the identity witness")
    intervals = defaultdict(list)
    for m in members():
        intervals[m["symbol"], m["isin"]].append((m["from_date"], m["to_date"]))
    return cert["bhavcopies"], cert["calendar"], intervals
