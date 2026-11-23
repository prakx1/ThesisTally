"""Reconcile first-published quarterly facts against the certified historical universe."""

from __future__ import annotations

import bisect
import calendar
import csv
import fcntl
import gzip
import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
METRICS = ("revenue", "operating_profit", "net_profit", "eps")
ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_DOMAINS = ("nseindia.com", "nseindia.in", "bseindia.com", "bseindia.in")


def first_session(stamp: datetime, sessions: list[str]) -> str | None:
    if stamp.utcoffset() is None:
        raise ValueError("A public timestamp requires a timezone")
    local = stamp.astimezone(IST)
    day = local.date().isoformat()
    if day < sessions[0]:
        return None
    i = bisect.bisect_left(sessions, day)
    if i < len(sessions) and sessions[i] == day and local.time() > time(15, 30):
        i += 1
    return sessions[i] if i < len(sessions) else None


def stock_quarters(members, sessions):
    for year in range(int(sessions[0][:4]), int(sessions[-1][:4]) + 1):
        for month in (3, 6, 9, 12):
            period = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
            if period < sessions[0] or period > sessions[-1]:
                continue
            day = sessions[bisect.bisect_right(sessions, period) - 1]
            seen = set()
            for member in members:
                if member["from_date"] <= day <= member["to_date"]:
                    isin = member["isin"]
                    if isin in seen:
                        raise ValueError(f"Overlapping membership at {day} for {isin}")
                    seen.add(isin)
                    yield {
                        "period_end": period,
                        "member_session": day,
                        "isin": isin,
                        "symbol": member["symbol"],
                        "membership_source": member["source_url"],
                    }


def has_field(row, key):
    value = row.get(key)
    return type(value) in (int, float) and math.isfinite(value)


def complete(row):
    return all(has_field(row, key) for key in METRICS)


def _source(row):
    url = urlparse(row.get("source_url") or "")
    host = url.hostname or ""
    return (
        url.scheme == "https"
        and any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)
        and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256") or "")
    )


def _clock(text):
    stamp = datetime.fromisoformat(text)
    if stamp.utcoffset() is None:
        raise ValueError("First publication requires a timezone")
    return stamp.astimezone(IST)


def _stamp_fields(row, trade_clock, sessions):
    initial = _clock(row.get("initial_disclosure_at") or row["announced_at"])
    mapping = row.get("fact_announced_at")
    clocks = {
        k: _clock(mapping[k]) if mapping is not None else trade_clock
        for k in METRICS
        if has_field(row, k)
    }
    if any(stamp < initial for stamp in clocks.values()) or (
        "eps" in clocks and clocks["eps"] != trade_clock
    ):
        raise ValueError("Inconsistent first field and EPS release clocks")
    end = max(clocks.values()) if len(clocks) == len(METRICS) else None
    if row.get("complete_available_at") and _clock(row["complete_available_at"]) != end:
        raise ValueError("Full-set publication differs from latest first field")
    return dict(
        row,
        announced_at=trade_clock.isoformat(),
        usable_session=first_session(trade_clock, sessions),
        initial_disclosure_at=initial.isoformat(),
        fact_announced_at={k: t.isoformat() for k, t in clocks.items()},
        fact_usable_sessions={k: first_session(t, sessions) for k, t in clocks.items()},
        complete_available_at=end.isoformat() if end else None,
        complete_usable_session=first_session(end, sessions) if end else None,
    )


def _documented_summary(summary, canonical):
    if summary.get("filing_status") != "initial_summary" or not _source(summary):
        return False
    if (
        not summary.get("original_record")
        or summary["original_record"] != canonical.get("id")
        or not summary.get("original_fields")
        or not canonical.get("initial_disclosure_at")
    ):
        return False
    earlier = _clock(summary["announced_at"])
    attrs = [k for k in METRICS if has_field(summary, k)]
    return bool(
        attrs
        and set(attrs) == set(summary["original_fields"])
        and _clock(canonical["initial_disclosure_at"]) == earlier
        and all(
            canonical.get(k) == summary[k]
            and (canonical.get("fact_announced_at") or {}).get(k)
            and _clock(canonical["fact_announced_at"][k]) == earlier
            for k in attrs
        )
    )


def reconcile(rows, sessions):
    """Retain the earliest original per basis. Never repair an earlier row using later values."""
    groups = defaultdict(list)
    unresolved = defaultdict(list)
    gaps = defaultdict(set)
    for row in rows:
        key = row.get("isin"), row.get("period_end")
        if not all(key):
            continue
        try:
            instant = datetime.fromisoformat(row["announced_at"])
            if instant.utcoffset() is None:
                raise ValueError("missing timestamp timezone")
            instant = instant.astimezone(IST)
            if instant.date() <= date.fromisoformat(row["period_end"]):
                raise ValueError("not a post-period release")
        except (ValueError, KeyError, TypeError):
            gaps[key].add("missing_or_invalid_public_timestamp")
            continue
        if row.get("first_reported") is not True:
            if row.get("basis") in {"consolidated", "standalone"}:
                candidate_clock = (row.get("first_notice") or {}).get("announced_at")
                if candidate_clock:
                    earlier = datetime.fromisoformat(candidate_clock)
                    if earlier.utcoffset() is not None and earlier.date() > date.fromisoformat(
                        key[1]
                    ):
                        instant = min(instant, earlier)
                unresolved[(*key, row["basis"])].append((instant, row))
            gaps[key].add(row.get("gap") or "original_release_unverified_or_revision")
            continue
        if row.get("basis") not in {"consolidated", "standalone"} or not _source(row):
            gaps[key].add("missing_basis_or_official_source_evidence")
            continue
        groups[(*key, row["basis"])].append((instant, row))
    originals = []
    for (isin, period, _basis), versions in sorted(groups.items()):
        first = min(instant for instant, _ in versions)
        choices = [
            candidate
            for instant, candidate in versions
            if instant == first
            and not any(
                t < first and not _documented_summary(unknown, candidate)
                for t, unknown in unresolved[isin, period, _basis]
            )
        ]
        if not choices:
            gaps[isin, period].add("earlier_unresolved_publication")
            continue
        if any(len({r[k] for r in choices if r.get(k) is not None}) > 1 for k in METRICS):
            gaps[isin, period].add("conflicting_first_publication")
            continue
        # Same timestamp, same reported facts. Prefer a complete source, then primary exchange.
        row = min(
            choices,
            key=lambda r: (
                not complete(r),
                r.get("exchange") != "NSE",
                r["source_url"],
                r["sha256"],
            ),
        )
        row = _stamp_fields(row, first, sessions)
        originals.append(row)
        if not complete(row):
            gaps[isin, period].add("missing_one_or_more_quarterly_facts")
    # A documented EPS can serve as future history even when the first market
    # reaction cannot be dated because the other basis had an earlier opaque release.
    verified = {(r["isin"], r["period_end"], r["basis"]) for r in originals}
    for r in originals:
        other = "standalone" if r["basis"] == "consolidated" else "consolidated"
        key = (r["isin"], r["period_end"], other)
        if key in verified or not has_field(r, "eps") or not r["usable_session"]:
            continue
        uncertain = [*unresolved.get(key, ()), *groups.get(key, ())]
        if not uncertain:
            continue
        clock, source = min(uncertain, key=lambda item: (item[0], item[1].get("id") or ""))
        possible_day = first_session(clock, sessions)
        if clock.date().isoformat() < sessions[0] or (
            possible_day and possible_day < r["usable_session"]
        ):
            r["earlier_unresolved_other_basis"] = {
                "basis": other,
                "announced_at": clock.isoformat(),
                "usable_session": possible_day,
                "source_id": source.get("id"),
                "source_url": (source.get("first_notice") or {}).get("source_url")
                or source.get("first_notice_url")
                or source.get("source_url"),
            }
            gaps[key[:2]].add("earlier_unresolved_other_basis_signal_clock")
    return originals, gaps


def season_year(period):
    return int(period[:4]) + (period[5:7] == "12")


def _ratio(covered, denominator):
    return {
        "eligible": denominator,
        "covered": covered,
        "coverage": covered / denominator if denominator else None,
        "coverage_eligible": denominator > 0 and covered * 5 >= denominator * 4,
    }


def gap_trace(position, candidates):
    """Keep source links and overlapping failures for each unfilled quarter."""
    groups = set()
    reasons = {t for t in position["gap"].split("|") if t and t != "no_verified_filing"}
    links = {"candidate_filing_url": "", "candidate_notice_url": "", "candidate_catalogue_url": ""}
    ordered = sorted(candidates, key=lambda r: (r.get("announced_at") or "", r.get("id") or ""))
    for row in ordered:
        notice = row.get("first_notice") or {}
        for key, value in (
            ("candidate_filing_url", row.get("structured_url") or row.get("source_url")),
            (
                "candidate_notice_url",
                notice.get("source_url") or row.get("first_notice_url") or row.get("notice_url"),
            ),
            (
                "candidate_catalogue_url",
                row.get("catalogue_url")
                or notice.get("catalogue_url")
                or row.get("announcement_catalogue"),
            ),
        ):
            if not links[key] and value:
                links[key] = value
        if row.get("gap"):
            reasons.add(row["gap"])
        # A board-notice-only probe has no tabular URL by construction.
        if row.get("structured_parse_gap") and not str(row.get("id") or "").startswith("notice/"):
            reasons.add(row["structured_parse_gap"])
    if not position["covered"]:
        if not candidates:
            groups.add("no_attributed_formal_or_board_record")
        for reason in reasons:
            if reason == "first_release_attachment_unavailable_or_revised":
                groups.add("attachment_or_revision_undistinguished")
            elif reason in (
                "missing_original_attachment",
                "structured_attachment_unavailable",
                "first_release_attachment_unavailable",
            ):
                groups.add("attachment_unavailable")
            elif reason in (
                "first_release_facts_unreconciled",
                "missing_one_or_more_quarterly_facts",
            ) or any(
                k in reason.lower()
                for k in (
                    "quarter",
                    "duration",
                    "isin",
                    "identity",
                    "context",
                    "unit",
                    "html",
                    "basis",
                    "xbrl",
                    "layout",
                )
            ):
                groups.add("unresolved_fields_period_or_identity")
            else:
                groups.add("unverified_original_or_timing")
        if not groups:
            groups.add("unverified_original_or_timing")
        position["gap"] = "|".join(sorted(reasons or {"no_verified_filing"}))
    position.update(**links, gap_groups="|".join(sorted(groups)))


def measure(members, sessions, filings, notice_probes=()):
    all_records = [
        *filings,
        *(
            dict(
                probe,
                first_reported=False,
                announced_at=probe.get("first_notice_at") or probe.get("announced_at"),
            )
            for probe in notice_probes
        ),
    ]
    originals, gaps = reconcile(all_records, sessions)
    sources = defaultdict(list)
    for row in all_records:
        sources[row.get("isin"), row.get("period_end")].append(row)
    by_key = defaultdict(list)
    for row in originals:
        by_key[row["isin"], row["period_end"]].append(row)
    positions = list(stock_quarters(members, sessions))
    year_groups, window_groups, stocks = defaultdict(list), defaultdict(list), defaultdict(list)
    for position in positions:
        period, isin = position["period_end"], position["isin"]
        year, window = int(period[:4]), season_year(period)
        window_end_index = bisect.bisect_right(sessions, f"{window}-12-31") - 1
        window_end = sessions[window_end_index]
        rows = [
            r
            for r in by_key[isin, period]
            if any(t and t <= sessions[-1] for t in r["fact_usable_sessions"].values())
        ]
        # Choose a complete first publication known at the time. An original for a
        # different accounting basis can never retroactively replace that timestamp.
        chosen = min(
            (
                r
                for r in rows
                if r["complete_usable_session"] and r["complete_usable_session"] <= sessions[-1]
            ),
            key=lambda r: (r["complete_available_at"], r["basis"] != "consolidated"),
            default=None,
        )
        cause = sorted(gaps[isin, period])
        if by_key[isin, period] and not cause and not chosen:
            cause = ["no_usable_session_before_snapshot_close"]
        position.update(
            covered=bool(chosen),
            basis=chosen["basis"] if chosen else "",
            announced_at=chosen["announced_at"] if chosen else "",
            usable_session=chosen["usable_session"] if chosen else "",
            complete_available_at=chosen["complete_available_at"] if chosen else "",
            complete_usable_session=chosen["complete_usable_session"] if chosen else "",
            source_url=chosen["source_url"] if chosen else "",
            gap="" if chosen else "|".join(cause or ["no_verified_filing"]),
            filing_season_year=window,
            covered_by_window_end=bool(chosen and chosen["complete_usable_session"] <= window_end),
        )
        gap_trace(position, sources[isin, period])
        for field in METRICS:
            known = min(
                (
                    r
                    for r in rows
                    if r["fact_usable_sessions"].get(field)
                    and r["fact_usable_sessions"][field] <= sessions[-1]
                ),
                key=lambda r: (r["fact_announced_at"][field], r["basis"] != "consolidated"),
                default=None,
            )
            position["known_" + field] = known is not None
            position[field + "_basis"] = known["basis"] if known else ""
            position[field + "_announced_at"] = known["fact_announced_at"][field] if known else ""
            position[field + "_source_url"] = (
                (
                    (known.get("fact_sources") or {}).get(field, {}).get("source_url")
                    or known["source_url"]
                )
                if known
                else ""
            )
        year_groups[year].append(position)
        window_groups[window].append(position)
        stocks[isin].append(position)
    years = {
        str(k): _ratio(sum(r["covered"] for r in v), len(v)) for k, v in sorted(year_groups.items())
    }
    windows = {
        str(k): dict(
            **_ratio(sum(r["covered_by_window_end"] for r in v), len(v)),
            periods=sorted({r["period_end"] for r in v}),
        )
        for k, v in sorted(window_groups.items())
        if k >= 2017
    }
    return originals, positions, years, windows, stocks


def filing_format(record):
    link = urlparse(record.get("structured_url") or "").path.lower()
    if link.endswith((".html", ".htm")):
        return "legacy HTML"
    if link.endswith(".xml"):
        return (
            "integrated XBRL"
            if (record.get("id") or "").startswith("integrated/")
            else "legacy XBRL"
        )
    if (record.get("id") or "").startswith("notice/"):
        return "board notice only"
    return "formal catalogue without structured attachment"


def by_filing_format(positions, records, originals):
    """Distinct eligible quarters within each catalogue format. Formats may overlap."""
    formats = defaultdict(set)
    certified = defaultdict(set)
    for r in records:
        formats[r.get("isin"), r.get("period_end")].add(filing_format(r))
    for r in originals:
        if r["complete_usable_session"]:
            certified[r["isin"], r["period_end"]].add(filing_format(r))
    by_stock = defaultdict(Counter)
    by_format = defaultdict(Counter)
    for p in positions:
        key = p["isin"], p["period_end"]
        gaps = set(p["gap_groups"].split("|"))
        for fmt in formats[key] or {"no attributed record"}:
            counts = {
                "eligible_with_format": 1,
                "covered_any_format": int(p["covered"]),
                "certified_original_in_this_format": int(fmt in certified[key]),
                "gap_attachment_unavailable": int("attachment_unavailable" in gaps),
                "gap_facts_or_identity": int("unresolved_fields_period_or_identity" in gaps),
                "gap_first_publication_or_time": int("unverified_original_or_timing" in gaps),
            }
            by_stock[p["isin"], fmt].update(counts)
            by_format[fmt].update(counts)
    return [
        dict(isin=isin, filing_format=fmt, **counts)
        for (isin, fmt), counts in sorted(by_stock.items())
    ], dict(sorted(by_format.items()))


def csv_text(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_bytes(directory, exchange, expected_sha=None):
    if exchange not in {"nse", "bse"}:
        raise ValueError("Unknown results exchange")
    source = directory / exchange
    if exchange == "nse":
        published = json.loads((source / "filings-receipt.json").read_bytes())["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", published) or (
            expected_sha is not None and expected_sha != published
        ):
            raise ValueError("NSE filing receipt differs from the expected SHA-256")
        if (source / "withdrawals" / (published + ".json")).is_file():
            raise ValueError(f"The source exchange ledger edition {published} was withdrawn")
        expected_sha = published
    line_file = source / "filings.jsonl"
    if line_file.exists():
        raw = line_file.read_bytes()
    elif exchange == "nse":
        raw = gzip.decompress((source / "vintages" / (expected_sha + ".jsonl.gz")).read_bytes())
    else:
        raise FileNotFoundError(line_file)
    if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("Filing ledger bytes differ from the certified SHA-256")
    return raw


def checkpoint_provenance(nse, ledger_sha):
    folder = nse / "checkpoints" / ledger_sha
    pointer = nse / "source-manifests/index.json"
    path = None
    if pointer.is_file():
        current = pointer.read_bytes()
        candidate = folder / hashlib.sha256(current).hexdigest() / "index.json"
        if candidate.is_file():
            path = candidate
    if path is None:
        eligible = list(folder.glob("*/index.json"))
        if len(eligible) != 1:
            raise ValueError("Coverage requires one pinned matching source census for this ledger")
        path = eligible[0]
    report = json.loads(path.read_bytes())
    census_sha = path.parent.name
    source = gzip.decompress((path.parent / "source-index.json.gz").read_bytes())
    census = json.loads(source)
    if (
        report["filings_sha256"] != ledger_sha
        or report["source_census_sha256"] != census_sha
        or report["filings_receipt_sha256"] != _sha(nse / "filings-receipt.json")
        or hashlib.sha256(source).hexdigest() != census_sha
        or census.get("filings_sha256") != ledger_sha
        or census["notice_acquisition"] != report["source_acquisition"]
    ):
        raise ValueError(
            "Coverage checkpoint conflicts with immutable filing or acquisition evidence"
        )
    return {
        "source_census_sha256": census_sha,
        "checkpoint_index_sha256": _sha(path),
        "source_acquisition": report["source_acquisition"],
    }


def _materialize(directory, output, membership, preflight):
    cert = json.loads(preflight.read_text())
    if _sha(membership) != cert["canonical_membership"]["published_sha256"]:
        raise ValueError("Universe hash differs from the certified historical universe")
    sessions = cert["sessions"]
    if sessions != sorted(set(sessions)) or sessions[-1] != cert["end"] or not cert["complete"]:
        raise ValueError("Invalid archived session calendar")
    sources = {}
    records = []
    for market in ("nse", "bse"):
        raw = ledger_bytes(directory, market)
        sources[market] = {"sha256": hashlib.sha256(raw).hexdigest()}
        for line in io.BytesIO(raw):
            records.append(dict(json.loads(line), exchange=market.upper()))
    with membership.open() as f:
        members = list(csv.DictReader(f))
    probe_path = directory / "nse" / "notice-probe-gaps.json"
    upstream = json.loads((directory / "nse" / "filings-receipt.json").read_bytes())
    if upstream.get("field_reviews"):
        sources["nse"].update(checkpoint_provenance(directory / "nse", sources["nse"]["sha256"]))
    probe_sha = upstream.get("notice_probe_gaps_sha256")
    probes = []
    if probe_sha:
        if _sha(probe_path) != probe_sha:
            raise ValueError("Unresolved first-notice probes differ from the filing receipt")
        probes = json.loads(probe_path.read_bytes())
    originals, positions, years, windows, stocks = measure(members, sessions, records, probes)
    symbols = defaultdict(set)
    for member in members:
        symbols[member["isin"]].add(member["symbol"])
    format_rows, format_stats = by_filing_format(positions, [*records, *probes], originals)
    stock_years = defaultdict(list)
    for position in positions:
        stock_years[position["isin"], position["period_end"][:4]].append(position)
    for name, rows in (
        ("quarter-coverage.csv", positions),
        ("stock-format-coverage.csv", format_rows),
        (
            "stock-year-coverage.csv",
            [
                dict(
                    isin=isin,
                    symbols="|".join(sorted({row["symbol"] for row in quarters})),
                    year=year,
                    **_ratio(sum(row["covered"] for row in quarters), len(quarters)),
                )
                for (isin, year), quarters in sorted(stock_years.items())
            ],
        ),
        (
            "stock-coverage.csv",
            [
                dict(
                    isin=isin,
                    symbols="|".join(sorted(symbols[isin])),
                    **_ratio(sum(r["covered"] for r in rs), len(rs)),
                )
                for isin in sorted(symbols)
                for rs in [stocks.get(isin, [])]
            ],
        ),
    ):
        (output / name).write_text(csv_text(rows))
    with (output / "first-releases.jsonl").open("w") as f:
        for row in originals:
            f.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    receipt = {
        "as_of": cert["end"],
        "membership_sha256": _sha(membership),
        "preflight_sha256": _sha(preflight),
        "filing_sources": sources,
        "products": {
            name: _sha(output / name)
            for name in (
                "first-releases.jsonl",
                "quarter-coverage.csv",
                "stock-coverage.csv",
                "stock-year-coverage.csv",
                "stock-format-coverage.csv",
            )
        },
        "candidate_formats": format_stats,
        "years": years,
        "windows": windows,
        "first_basis_records": len(originals),
        "all_source_records": len(records),
        "notice_probe_gaps_sha256": probe_sha,
        "canonical_eps_basis_records_including_warmup": sum(
            has_field(row, "eps") for row in originals
        ),
        "eps_known_while_four_field_coverage_missing": sum(
            p["known_eps"] and not p["covered"] for p in positions
        ),
        "eps_is_only_known_field": sum(
            p["known_eps"]
            and not any(p["known_" + k] for k in ("revenue", "net_profit", "operating_profit"))
            for p in positions
        ),
        "practical_gap_groups": dict(
            Counter(g for p in positions for g in p["gap_groups"].split("|") if g)
        ),
        "known_fields": {f: sum(p["known_" + f] for p in positions) for f in METRICS},
        "membership_isins": len(symbols),
        "isins_with_quarter_end_membership": len(stocks),
        "eligible": len(positions),
        "covered": sum(r["covered"] for r in positions),
        "reasons": dict(Counter(p["gap"] for p in positions if p["gap"])),
    }
    (output / "coverage.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _archive(directory, sha, raw):
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("Input changed during coverage measurement; rerun")
    path = directory / (sha + ".gz")
    if path.exists():
        if gzip.decompress(path.read_bytes()) != raw:
            raise ValueError("Archived coverage object differs from its hash")
    else:
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(raw, compresslevel=5, mtime=0))
        tmp.replace(path)


def build(directory, membership, preflight):
    archive = directory / "coverage-history"
    archive.mkdir(exist_ok=True)
    objects = archive / "objects"
    objects.mkdir(exist_ok=True)
    with (
        (archive / ".publish.lock").open("a") as lock,
        TemporaryDirectory(dir=directory) as scratch,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX)
        output = Path(scratch)
        receipt = _materialize(directory, output, membership, preflight)
        if receipt["notice_probe_gaps_sha256"]:
            _archive(
                objects,
                receipt["notice_probe_gaps_sha256"],
                (directory / "nse" / "notice-probe-gaps.json").read_bytes(),
            )
        for source, meta in receipt["filing_sources"].items():
            _archive(objects, meta["sha256"], ledger_bytes(directory, source, meta["sha256"]))
        for name, sha in receipt["products"].items():
            _archive(objects, sha, (output / name).read_bytes())
        record = output / "coverage.json"
        version = archive / (_sha(record) + ".json")
        if version.exists() and version.read_bytes() != record.read_bytes():
            raise ValueError("Coverage snapshot conflicts with its archived receipt")
        if not version.exists():
            stage = output / "receipt-copy.json"
            stage.write_bytes(record.read_bytes())
            stage.replace(version)
        for name in (*receipt["products"], "coverage.json"):
            (output / name).replace(directory / name)
    return receipt


if __name__ == "__main__":
    build(
        ROOT / "data/fundamentals",
        ROOT / "data/universes/nifty200_membership.csv",
        ROOT / "data/baseline/preflight.json",
    )
