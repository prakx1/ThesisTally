"""Extract published next-session static percentage changes from dated NSE review notices."""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from .legacy_evidence import verified_isin, workbooks
from .legacy_surveillance import API, ARCHIVE, Cache, EmptyResponse, sha, write_json

START = date(2013, 12, 1)
END = date(2019, 12, 29)


def catalogue(folder):
    source = Cache(folder)
    months = []
    d = START
    while d <= END:
        end = min(END, d.replace(day=calendar.monthrange(d.year, d.month)[1]))
        months.append((d, end))
        d = end + timedelta(days=1)

    def retrieve(span):
        lo, hi = span
        for_suffix = f"{lo.year}-{lo.month:02}"
        quarters = (
            folder.parent / "legacy-surveillance" / "catalogue-verified" / (for_suffix + ".json")
        )
        if quarters.exists():
            rows = json.loads(quarters.read_bytes())["data"]
        else:
            url = (
                API
                + "?"
                + urlencode(
                    {"fromDate": lo.strftime("%d-%m-%Y"), "toDate": hi.strftime("%d-%m-%Y")}
                )
            )
            raw = source.get("catalogue/" + for_suffix + ".json", url)
            monthly = json.loads(raw)
            if monthly["fromDate"] != lo.strftime("%d-%m-%Y") or monthly["toDate"] != hi.strftime(
                "%d-%m-%Y"
            ):
                raise ValueError(f"Circular inventory differs for {lo}")
            rows = monthly["data"]
        return [
            r
            for r in rows
            if date.fromisoformat(r["cirDate"]) <= END
            and re.search(
                r"changes? in (?:the )?price bands?|revision (?:of|in) (?:the )?price bands?",
                r["sub"],
                re.IGNORECASE,
            )
        ]

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = sorted(
                (r for group in pool.map(retrieve, months) for r in group),
                key=lambda r: r["cirDate"],
            )
        for r in rows:
            name = r["circFilename"]
            if not re.fullmatch(r"SURV\d+\.(zip|pdf)", name, re.IGNORECASE):
                raise ValueError(f"Unrecognized price-band notice {name}")
            source.get("raw/" + name, ARCHIVE + name)
        write_json(folder / "notice-index.json", rows)
        return rows
    finally:
        source.client.close()


def daily(folder, root):
    sessions = sorted(date.fromisoformat(p.name) for p in (root / "sessions").glob("????-??-??"))
    start = date.fromisoformat(
        json.loads((root / "sessions" / str(sessions[0]) / "manifest.json").read_bytes())[
            "prior_day"
        ]
    )
    days = [start] + [
        day
        for day in sessions
        if day <= date(2020, 1, 31) or str(day) in {"2021-12-10", "2022-05-11"}
    ]
    cache = Cache(folder, csv_header=b"Sr. No,Symbol,Series,Security Name,From,To")

    def date_file(day):
        name = f"eq_band_changes_{day:%d%m%Y}.csv"
        url = "https://nsearchives.nseindia.in/content/equities/" + name
        gap = folder / "missing" / (name + ".json")
        if gap.is_file():
            return (str(day), json.loads(gap.read_bytes()))
        try:
            raw = cache.get("raw/" + name, url)
            return str(day), {"url": url, "sha256": sha(raw), "file": "raw/" + name, "status": 200}
        except httpx.HTTPStatusError as exc:
            v = {"url": url, "status": exc.response.status_code}
            if exc.response.status_code == 404:
                write_json(gap, v)
            return str(day), v
        except EmptyResponse:
            v = {"url": url, "status": "empty_http_200", "sha256": sha(b"")}
            write_json(gap, v)
            return str(day), v
        except (ValueError, httpx.HTTPError) as exc:
            return str(day), {"url": url, "status": "read_error", "error": str(exc)}

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = {}
            for i, (day, record) in enumerate(pool.map(date_file, days), 1):
                records[day] = record
                if i % 250 == 0:
                    print(i, "daily price band revisions checked", flush=True)
        write_json(folder / "daily-index.json", records)
    finally:
        cache.client.close()


def effective_date(title):
    match = re.search(r"\bfrom\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})\s*$", title.strip(), re.IGNORECASE)
    if not match:
        return None
    value = match[1].replace(",", "")
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def band_value(label):
    text = label.strip().removesuffix("%").strip()
    return int(text) if text in {"2", "5", "10", "20"} else None


def read_review(raw):
    """Only the explicit EQ row/ISIN/Band column can establish a static label."""
    result = []
    for sheets in workbooks(raw):
        for rows in sheets.values():
            columns = {}
            for row in rows:
                names = {v.strip().lower(): k for k, v in row.items()}
                if {"symbol", "series", "isin", "band"} <= names.keys():
                    columns = names
                    continue
                if not columns or row.get(columns["series"], "").strip() != "EQ":
                    continue
                isin = row.get(columns["isin"], "").strip()
                symbol = row.get(columns["symbol"], "").strip()
                if symbol and verified_isin(isin):
                    result.append((symbol, isin, band_value(row.get(columns["band"], ""))))
    return result


def daily_labels(raw):
    source = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"), newline=""))
    if not {"Symbol", "Series", "From", "To"} <= set(source.fieldnames or ()):
        raise ValueError("Dated band changes lack numeric before/after columns")
    result = []
    for row in source:
        if row.get("Sr. No") == "null" and not any(
            row.get(k) for k in ("Symbol", "Series", "From", "To")
        ):
            continue
        if any(row.get(k) is None for k in ("Symbol", "Series", "From", "To")):
            raise ValueError("Malformed NSE band-change row")
        if row["Series"].strip() == "EQ":
            result.append((row["Symbol"].strip(), band_value(row["To"])))
    return result


def inputs(folder, records):
    by_effective = defaultdict(list)
    unknown = []
    for row in records:
        published = date.fromisoformat(row["cirDate"])
        effective = effective_date(row["sub"])
        name = row["circFilename"]
        if not effective or effective <= published:
            unknown.append({"file": name, "reason": "inexact or nonfuture effective date"})
            continue
        p = folder / "raw" / name
        raw = p.read_bytes()
        meta = json.loads(p.with_suffix(p.suffix + ".source.json").read_bytes())
        if sha(raw) != meta["sha256"] or meta["url"] != ARCHIVE + name:
            raise ValueError("Corrupt band review " + name)
        rates = read_review(raw)
        if not rates:
            unknown.append({"file": name, "reason": "no verified EQ/ISIN/band table"})
            continue
        by_effective[effective].append((published, rates, meta))
    return by_effective, unknown


def eligible_sources(session, previous):
    def members(p):
        with (p / "members.csv").open(newline="") as f:
            return {r["Symbol"].strip(): r["ISIN Code"].strip() for r in csv.DictReader(f)}

    old = members(previous)
    new = members(session)
    symbols = {}
    with (previous / "bhavcopy.csv").open(errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            sym, isin = r["TckrSymb"].strip(), r["ISIN"].strip()
            if (
                r["SctySrs"].strip() == "EQ"
                and old.get(sym) == new.get(sym) == isin
                and int(float(r["TtlTradgVol"])) > 0
                and verified_isin(isin)
            ):
                if sym in symbols:
                    raise ValueError("Duplicate historical equity identity " + sym)
                symbols[sym] = isin
    return symbols


def verify_vintage_dates(folder, root):
    """Compare the ISIN review against both CSV dates without using price outcomes."""
    notices = json.loads((folder / "notice-index.json").read_bytes())
    reviews, _ = inputs(folder, notices)
    cache = Cache(folder, csv_header=b"Sr. No,Symbol,Series,Security Name,From,To")

    def check(item):
        day, notices = item
        session = root / "sessions" / str(day)
        prior = date.fromisoformat(
            json.loads((session / "manifest.json").read_bytes())["prior_day"]
        )
        stated = {
            (sym, rate) for _, rows, _ in notices for sym, _, rate in rows if rate is not None
        }

        def observed(d):
            name = f"eq_band_changes_{d:%d%m%Y}.csv"
            url = "https://nsearchives.nseindia.in/content/equities/" + name
            data = cache.get("raw/" + name, url)
            changes = {(s, r) for s, r in daily_labels(data) if r is not None}
            return {
                "url": url,
                "sha256": sha(data),
                "listed_changes": len(changes),
                "match_circular": len(changes & stated),
            }

        return str(day), {
            "prior": str(prior),
            "circular_eq_static_rows": len(stated),
            "prior_file": observed(prior),
            "effective_file": observed(day),
            "notice_sources": [meta for _, _, meta in notices],
        }

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            checks = dict(pool.map(check, reviews.items()))
    finally:
        cache.client.close()
    if any(
        v["effective_file"]["match_circular"] <= v["prior_file"]["match_circular"]
        for v in checks.values()
    ):
        raise ValueError(
            "Effective-date semantics could not be corroborated for all historical review vintages"
        )
    write_json(folder / "vintage-date-verification.json", checks)
    return checks


def known_by_prior_close(last_modified, prior):
    """Require the exact version's origin timestamp within prior session EOD (IST)."""
    if not last_modified:
        return False
    try:
        stamp = parsedate_to_datetime(last_modified).astimezone(ZoneInfo("Asia/Kolkata"))
    except (TypeError, ValueError):
        return False
    return stamp.date() == prior and (stamp.hour, stamp.minute) >= (15, 30)


def version_receipt(folder, cache, record):
    name = Path(record["file"]).name
    raw = cache.get("version/" + name, record["url"])
    receipt = json.loads((folder / "version" / (name + ".source.json")).read_bytes())
    if sha(raw) != record["sha256"] or receipt["sha256"] != record["sha256"]:
        raise ValueError("The exchange changed daily band-revision bytes: " + name)
    return receipt


def resolve(day, prior, identities, reviews, daily):
    rows = defaultdict(list)
    if daily:
        records, source = daily
        if source.get("effective") != str(day) or not known_by_prior_close(
            source.get("last_modified"), prior
        ):
            raise ValueError(
                "Daily band revision lacks applicable-date and prior-close version evidence"
            )
        for sym, rate in records:
            if sym in identities:
                rows[sym].append((rate, source, str(prior)))
    for published, records, source in reviews:
        if published > prior:
            continue
        for sym, isin, rate in records:
            if identities.get(sym) == isin:
                rows[sym].append((rate, source, str(published)))
    labels = {}
    conflicts = []
    for symbol, matches in rows.items():
        rates = {r[0] for r in matches}
        if len(rates) > 1:
            conflicts.append(symbol)
            continue
        rate = rates.pop()
        if rate is None:
            continue
        sources = {
            source["url"]: {**source, "published": published} for _, source, published in matches
        }
        labels[symbol] = {
            "isin": identities[symbol],
            "percent": rate,
            "published": max(v["published"] for v in sources.values()),
            "sources": [sources[k] for k in sorted(sources)],
        }
    return labels, conflicts


def prepare(root, sessions):
    root = Path(root)
    folder = root / "baseline/price-band-reviews"
    notice_index = folder / "notice-index.json"
    notices = json.loads(notice_index.read_bytes()) if notice_index.is_file() else []
    if not notices or any(not (folder / "raw" / r["circFilename"]).is_file() for r in notices):
        notices = catalogue(folder)
    daily_index = folder / "daily-index.json"
    indexed = json.loads(daily_index.read_bytes()) if daily_index.is_file() else {}
    if not indexed or any(
        not (folder / r["file"]).is_file() for r in indexed.values() if r["status"] == 200
    ):
        daily(folder, root)
        indexed = json.loads(daily_index.read_bytes())
    scheduled, unknown = inputs(folder, notices)
    compared = verify_vintage_dates(folder, root)
    dest = folder / "labels"
    archived = root / "baseline/market/sessions"
    months = Counter()
    stats = Counter()
    digests = {}
    duplicates = {}
    no_prior_version = {}
    cache = Cache(
        folder, csv_header=b"Sr. No,Symbol,Series,Security Name,From,To", capture_modified=True
    )
    for stamp in sorted({str(d) for d in sessions}):
        day = date.fromisoformat(stamp)
        session = archived / stamp
        prior = date.fromisoformat(
            json.loads((session / "manifest.json").read_bytes())["prior_day"]
        )
        previous = archived / str(prior)
        record = indexed.get(stamp)
        reviews = scheduled.get(day, [])
        if not previous.is_dir() or (not reviews and (not record or record["status"] != 200)):
            continue
        published_symbols = {sym for _, entries, _ in reviews for sym, _, _ in entries}
        parsed = []
        daily_data = None
        if record and record["status"] == 200:
            file = Path(record["file"])
            if file.name != f"eq_band_changes_{day:%d%m%Y}.csv":
                raise ValueError("Misdated NSE change file on " + stamp)
            raw = (folder / file).read_bytes()
            if sha(raw) != record["sha256"]:
                raise ValueError("Corrupt daily revision " + stamp)
            parsed = daily_labels(raw)
            published_symbols.update(s for s, _ in parsed)
        with (session / "members.csv").open(newline="") as file:
            if not any(r["Symbol"].strip() in published_symbols for r in csv.DictReader(file)):
                continue
        observed = eligible_sources(session, previous)
        candidates = [(s, r) for s, r in parsed if s in observed]
        if candidates:
            version = version_receipt(folder, cache, record)
            if known_by_prior_close(version.get("last_modified"), prior):
                daily_data = (candidates, version | {"effective": stamp})
            else:
                no_prior_version[stamp] = {
                    "source": version,
                    "symbols": sorted({s for s, _ in candidates}),
                }
        labels, conflicts = resolve(day, prior, observed, reviews, daily_data)
        if conflicts:
            duplicates[stamp] = conflicts
        if not labels:
            continue
        for item in labels.values():
            key = "+".join(
                "daily" if "eq_band_changes" in s["url"] else "circular" for s in item["sources"]
            )
            stats[key] += 1
        proof = {"day": stamp, "prior": str(prior), "labels": labels}
        f = dest / (stamp + ".json")
        write_json(f, proof)
        digests[stamp] = sha(f.read_bytes())
        months[stamp[:4]] += len(labels)
    cache.client.close()
    write_json(folder / "unproven-publication-dates.json", no_prior_version)
    out = {
        "complete": True,
        "unproven_publication_dates": len(no_prior_version),
        "unproven_publication_sha256": sha(
            (folder / "unproven-publication-dates.json").read_bytes()
        ),
        "days": digests,
        "records": sum(months.values()),
        "vintage_dates_verified": len(compared),
        "by_year": dict(months),
        "source_counts": dict(stats),
        "excluded_conflicts": duplicates,
        "unsupported_notices": unknown,
        "daily_status": dict(Counter(str(r["status"]) for r in indexed.values())),
        "input_sha256": {
            n: sha((folder / n).read_bytes())
            for n in ("notice-index.json", "daily-index.json", "vintage-date-verification.json")
        },
    }
    path = folder / "coverage.json"
    write_json(path, out)
    return {k: v for k, v in out.items() if k not in {"days", "excluded_conflicts"}} | {
        "coverage_sha256": sha(path.read_bytes()),
        "sessions": len(digests),
        "conflicting_observations": sum(map(len, duplicates.values())),
    }


@lru_cache(maxsize=3)
def _window(path, mtime, size):
    return json.loads(path.read_bytes())["days"]


def labels(root, effective_day):
    folder = Path(root) / "baseline/price-band-reviews"
    path = folder / "coverage.json"
    stat = path.stat()
    expected = _window(path, stat.st_mtime_ns, stat.st_size).get(str(effective_day))
    if expected is None:
        return {}
    raw = (folder / "labels" / (str(effective_day) + ".json")).read_bytes()
    if sha(raw) != expected:
        raise ValueError("Unverified price-band revisions for " + str(effective_day))
    return json.loads(raw)["labels"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/baseline/price-band-reviews"))
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        root = args.root.parents[1]
        print(
            json.dumps(
                prepare(
                    root,
                    [
                        p.name
                        for p in (root / "baseline/market/sessions").glob("????-??-??")
                        if p.name < "2024-02-07"
                    ],
                ),
                indent=2,
            )
        )
    elif args.daily:
        daily(args.root, Path("data"))
    else:
        print(len(catalogue(args.root)), "documented notices")
