"""Verify the independent audit of every provisional first-form-only issuer result."""

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from .fundamental_notice_language import accession, read_period
from .fundamental_nse import ROOT, digest, published_at, write_json

ORIGINS = {"archives.nseindia.com", "nsearchives.nseindia.in", "nsearchives.nseindia.com"}
DECISIONS = {"possible_original", "proven_original", "unrelated"}


def bind(item, decision, original, bulletin, raw, *, identity=None):
    url = item["source_url"]
    retrieved = decision["retrieved_url"]
    if (
        decision["source_url"] != url
        or not all(
            urlsplit(u).scheme == "https" and urlsplit(u).hostname in ORIGINS
            for u in (url, retrieved)
        )
        or urlsplit(retrieved).path != urlsplit(url).path
    ):
        raise ValueError("Independent audit references another archive or attachment")
    sha = digest(raw)
    if (
        sha != decision["sha256"]
        or (item.get("source_sha256") and item["source_sha256"] != sha)
        or not raw.startswith(b"%PDF")
    ):
        raise ValueError("Reviewed original document differs from its signed PDF")
    at = published_at(original.get("exchdisstime")) or published_at(original.get("an_dt"))
    keys = {
        "sequence": original.get("seq_id"),
        "isin": original.get("sm_isin"),
        "symbol": original.get("symbol"),
        "description": original.get("desc"),
        "text": original.get("attchmntText"),
        "source_url": original.get("attchmntFile"),
        "announced_at": at.isoformat() + "+05:30" if at else None,
        "catalogue_url": bulletin["url"],
        "catalogue_sha256": bulletin["sha256"],
    }
    stored_isin = item["isin"]
    if keys["isin"] != stored_isin and not (
        identity
        and identity["symbol"] == keys["symbol"]
        and identity["traded_isin"] == stored_isin
        and identity["notice_isin"] == keys["isin"]
        and len(identity["evidence"]) == 2
    ):
        raise ValueError("Reviewed ISIN lacks contemporaneous equity and index identity")
    if any(str(item[k]) != str(v) for k, v in keys.items() if k != "isin"):
        raise ValueError("Independent review disagrees with original exchange bulletin")
    if decision["timing_status"] not in DECISIONS or not decision["reason"]:
        raise ValueError("Missing original-document review decision")
    return {
        **keys,
        "accession_sha256": accession(original),
        "reviewed_traded_isin": stored_isin,
        "reviewed_trading_identity": identity,
        "attachment_sha256": sha,
        "retrieved_url": retrieved,
        "release_language": "audited_original_case",
        "declared_period_end": item["period_end"],
        "declared_basis": (
            item["basis_evidence"]
            if item["basis_evidence"] in {"standalone", "consolidated"}
            else None
        ),
        "reviewed_status": decision["timing_status"],
        "case_evidence": decision["reason"],
    }


def main():
    review = ROOT.parent / "review"
    candidates = review / "formal-original-clock-audit-source-input.json"
    decisions = review / "formal-original-clock-document-decisions.json"
    decisions_raw = json.loads(decisions.read_bytes())
    case = json.loads(candidates.read_bytes())
    by_url = {i["source_url"]: i for i in decisions_raw["items"]}
    case_items = {i["source_url"]: i for i in case["case_items"]}
    if by_url.keys() != case_items.keys() or len(by_url) != len(decisions_raw["items"]):
        raise ValueError(
            "The signed original document decisions do not cover all reviewed accessions"
        )
    by_month = defaultdict(dict)
    for item in case_items.values():
        by_month[item["catalogue_url"]][str(item["sequence"])] = item
    sources = ROOT / "notice-market-sources.json"
    from .discovery_worker import archive_root
    from .fundamental_identity import date_witness, identity_witness, regular_sessions
    from .fundamental_nse import members

    intervals = defaultdict(list)
    for m in members():
        intervals[m["symbol"], m["isin"]].append((m["from_date"], m["to_date"]))
    requests = {
        (r["symbol"], r["isin"], r["period_end"], r["announced_at"][:10])
        for r in case_items.values()
    }
    days, calendar = date_witness(
        archive_root() / "baseline/market/sources/nse-daily",
        requests,
        supplement=ROOT / "identity-daily",
        calendar_days=regular_sessions(archive_root()),
    )
    bound = []
    for receipt in json.loads(sources.read_bytes()):
        if receipt["kind"] != "period" or receipt["url"] not in by_month:
            continue
        _, items = read_period(receipt)
        needed = by_month[receipt["url"]]
        for original in items:
            if (key := str(original.get("seq_id"))) not in needed:
                continue
            item = needed[key]
            url = item["source_url"]
            name = Path(urlsplit(url).path).name
            raw = (review / "visual-working" / ("board-" + name)).read_bytes()
            alias = original.get("sm_isin")
            identity = None
            if alias != item["isin"]:
                identity = identity_witness(
                    {**item, "identity": "require_original_isin", "metadata": {"isin": alias}},
                    {
                        "symbol": original["symbol"],
                        "isin": alias,
                        "announced_at": item["announced_at"],
                    },
                    days,
                    calendar,
                    intervals,
                )
            bound.append(bind(item, by_url[url], original, receipt, raw, identity=identity))
    if {row["source_url"] for row in bound} != case_items.keys() or len(bound) != len(case_items):
        raise ValueError("A reviewed original has no unique attested exchange accession")
    bound.sort(key=lambda r: (r["symbol"], r["announced_at"], r["source_url"]))
    rows = ROOT / "notice-formal-review-rows.json"
    write_json(rows, [r for r in bound if r["reviewed_status"] != "unrelated"])
    write_json(
        ROOT / "notice-formal-review-manifest.json",
        {
            "rows_sha256": digest(rows.read_bytes()),
            "market_sources_sha256": digest(sources.read_bytes()),
            "source_edition": case["source_edition"],
            "formal_records_reviewed": case["formal_rows_reviewed"],
            "review_documents_sha256": digest(decisions.read_bytes()),
            "review_bulletins_sha256": digest(candidates.read_bytes()),
            "reviewed": bound,
        },
    )


def source_daily():
    """Fetch missing reviewed dates only after checking the historical exchange trading calendar."""
    import bisect
    from datetime import date, timedelta

    import httpx

    from .discovery_registry import write_once
    from .discovery_worker import archive_root
    from .fundamental_identity import exchange_equity
    from .fundamental_nse import HEAD, public_copy

    archive = archive_root()
    raw_source = archive / "baseline/market/sources/nse-daily"
    sessions = sorted(
        date.fromisoformat(p.name)
        for p in (archive / "pilot/market/sessions").iterdir()
        if p.is_dir() and len(p.name) == 10 and (p / "manifest.json").is_file()
    )
    subjects = json.loads(
        (ROOT.parent / "review/formal-original-clock-audit-source-input.json").read_bytes()
    )["case_items"]
    days = set()
    for item in subjects:
        at = date.fromisoformat(item["announced_at"][:10]) - timedelta(days=1)
        for bound in (date.fromisoformat(item["period_end"]), at):
            i = bisect.bisect_right(sessions, bound) - 1
            if i >= 0 and (bound - sessions[i]).days <= 7:
                days.add(sessions[i])
    with httpx.Client(headers=HEAD, timeout=30, follow_redirects=True) as client:
        for day in sorted(days):
            if (raw_source / str(day) / "sources.json").exists():
                continue
            dest = ROOT / "identity-daily" / str(day)
            if (dest / "sources.json").exists():
                continue
            url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
            raw, actual = public_copy(client, url, accept=lambda b: b.startswith(b"PK"))
            if not exchange_equity(raw, day):
                raise ValueError("Official identity source has no dated ordinary equities")
            dest.mkdir(parents=True, exist_ok=True)
            write_once(dest / "bhavcopy.zip", raw)
            write_json(
                dest / "sources.json",
                {"bhavcopy.zip": {"url": actual, "sha256": digest(raw), "catalogue_url": url}},
            )
