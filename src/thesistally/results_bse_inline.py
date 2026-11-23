"""Reconcile BSE's two publication catalogues for the 2025/26 D2 coverage census."""

import argparse
import calendar
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from . import results_bse_census as census
from .discovery_registry import digest, write_once
from .results_bse_facts import verify_facts
from .results_forward_bse import API, HEADERS, _filing_url

CATALOGUE = API + "Integratedfinancedata/w?scripcode="
ROOT = census.ROOT / "inline"
TARGETS = census.ROOT / "priority-targets.jsonl"
ALL_CODES = census.ROOT / "priority-all-issuer-codes.json"
GLOBAL_TARGETS = census.ROOT / "global-targets.jsonl"
FILE_CONTROLS = {
    census.WEB + "/XBRLFILES/IFIndasDuplicateUploadDocument/"
    "Integrated_Finance_Ind_As_532540_810202622559_IFIndAs.html": "b54ccc03de585f743212f8f7b4a2359eff17f7c2914816aaeb82ee705e92bfd8",
    census.WEB
    + "/XBRLFILES/FourOneUploadDocument/Banking_500180_197202020320.xml": "547335618085f97adea93290f3adc24fda9722ad108c654832513048771d4352",
}


def load_targets():
    rows = [json.loads(line) for line in TARGETS.read_bytes().splitlines()]
    if any(
        r["season"] not in {"2025", "2026"} or not re.fullmatch(r"\d{6}", r["scrip_code"])
        for r in rows
    ):
        raise ValueError("Unexpected year or issuer in D2 inline coverage request")
    return rows


def _get(client, url):
    receipt = census.request(client, url)
    if url.startswith(CATALOGUE) and receipt[2] == 200:
        try:
            body = json.loads(receipt[4])
            if not isinstance(body, dict) or not isinstance(body.get("Table"), list):
                raise TypeError("Missing issuer table")
            kind = "inline_catalogue"
        except (ValueError, TypeError):
            kind = "unverified_catalogue"
        receipt = (*receipt[:3], kind, *receipt[4:])
    return receipt


def check_controls(db, http):
    """Check known downloadable originals with the queue's actual client and source settings."""
    for url in (*FILE_CONTROLS, CATALOGUE + "532540"):
        receipt = _get(http, url)
        if url in FILE_CONTROLS and receipt[3] == "xbrl":
            classification = (
                "known_original_bytes"
                if digest(receipt[4]) == FILE_CONTROLS[url]
                else "different_xbrl_bytes"
            )
            receipt = (*receipt[:3], classification, *receipt[4:])
        with db:
            census.save(db, receipt)


def include_target_control_bytes(db, controls, urls):
    """An exact target response is evidence regardless of which request observed it."""
    for row in controls.execute("""SELECT url,observed,status,sha,headers FROM observations
            WHERE kind IN ('known_original_bytes','different_xbrl_bytes') ORDER BY id"""):
        url, observed, status, sha, headers = row
        if (
            url not in urls
            or db.execute(
                "SELECT 1 FROM observations WHERE url=? AND sha=? AND kind='xbrl'", (url, sha)
            ).fetchone()
        ):
            continue
        with db:
            census.save(
                db,
                (
                    url,
                    observed,
                    status,
                    "xbrl",
                    census.raw_by_hash(controls, sha),
                    json.loads(headers),
                    None,
                ),
            )


def collect(db, urls, kind, max_attempts, *, controls=None):
    done = {r[0] for r in db.execute("SELECT DISTINCT url FROM observations WHERE kind=?", (kind,))}
    counts = dict(db.execute("SELECT url,COUNT(*) FROM observations GROUP BY url"))
    pending = [u for u in sorted(set(urls)) if u not in done and counts.get(u, 0) < max_attempts]
    if not pending:
        return
    # The independent native-XML census runs alongside this source. Keep a modest
    # request rate here while sharing the census' refusal/transport retry semantics.
    with (
        httpx.Client(
            headers=HEADERS,
            timeout=15,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=0),
        ) as http,
        ThreadPoolExecutor(max_workers=8) as workers,
    ):
        active = set()
        for index, url in enumerate(pending):
            if controls is not None and index % 250 == 0:
                check_controls(controls, http)
            if len(active) == 8:
                ready, active = wait(active, return_when=FIRST_COMPLETED)
                _store_batch(db, ready)
            active.add(workers.submit(_get, http, url))
            time.sleep(0.32)
        _store_batch(db, active)


def _store_batch(db, batch):
    for future in batch:
        row = future.result()
        with db:
            census.save(db, row)
        if row[2] == 429:
            delay = row[5].get("retry-after", "")
            time.sleep(float(delay) if delay.isdigit() else 60)


def fetch_catalogues():
    with closing(census.connection(ROOT)) as db:
        for attempt in range(1, 4):
            collect(db, catalogue_urls(), "inline_catalogue", attempt)


def catalogue_urls():
    codes = json.loads(ALL_CODES.read_bytes())
    if (
        not isinstance(codes, list)
        or not codes
        or any(not re.fullmatch(r"[0-9]{6}", c) for c in codes)
    ):
        raise ValueError("Invalid issuer list for two-window coverage count")
    return [CATALOGUE + c for c in sorted(set(codes))]


def integrated_editions(rows, code):
    """Independently dated originals/revisions, including earlier or unmatched BSE filings."""
    by_period = defaultdict(dict)
    scopes = {"qtr": "Quarter", "hly": "Half Year", "yearly": "Year", "ninemths": "Nine Months"}
    for item in rows:
        if not isinstance(item, dict):
            raise TypeError("Invalid BSE integrated catalogue row")
        if item.get("seourl") and urlsplit(item["seourl"]).path.rstrip("/").split("/")[-1] != code:
            raise ValueError("BSE integrated issuer mismatch")
        label = re.fullmatch(
            r"(Consolidated|Standalone)-([A-Za-z]{3})-(Qtr|Hly|Yearly|NineMths)-(\d{4})",
            item.get("Quarter_Name") or "",
            flags=re.IGNORECASE,
        )
        if not label:
            continue
        basis, mon, scope, year = label.groups()
        months = list(calendar.month_abbr)
        if mon.title() not in months:
            continue
        month, year = months.index(mon.title()), int(year)
        period = date(year, month, calendar.monthrange(year, month)[1])
        status = str(item.get("status") or "").strip()
        reason = (item.get("revised_reson") or "").strip()
        created, revised = (
            census.stamp(item.get("filing_date_time")),
            census.stamp(item.get("revised_date_time")),
        )
        marker = status.lower() != "new" or bool(item.get("revised_date_time")) or bool(reason)
        when = (revised or created) if marker else created
        if when and when.date() <= period:
            when = None
        for url in sorted({_filing_url(item.get(k)) for k in ("XbrlFile", "xbrlurl")} - {None}):
            moment = when.isoformat() if when else None
            grouped = by_period[period.isoformat(), basis.lower()]
            key = (url, moment, status, marker)
            if key not in grouped:
                grouped[key] = {
                    "url": url,
                    "at": moment,
                    "new": not marker and created is not None,
                    "status": status,
                    "has_revision_marker": marker,
                    "reason": reason,
                    "filing_scope": scopes[scope.lower()],
                    "venue": "BSE Integrated",
                }
            elif scope.lower() == "qtr":
                grouped[key]["filing_scope"] = "Quarter"
    return {k: list(v.values()) for k, v in by_period.items()}


def _accession_equal(archive_url, bound_url, code):
    a = PurePosixPath(urlsplit(archive_url).path)
    b = PurePosixPath(urlsplit(bound_url).path)
    if not _filing_url(archive_url) or not _filing_url(bound_url):
        return False
    if not re.search(rf"_{code}_\d{{8,16}}$", a.stem):
        return False
    # 'Duplicate' is BSE's published rendering path. The suffix is its specific
    # taxonomy; the full source submission name including accession remains intact.
    if b.stem == a.stem:
        return True
    return any(
        b.stem == a.stem + "_" + family
        for family in ("IFIndAs", "IFNBFC", "IFBanking", "IFOtherthan", "IFInsurance")
    )


def match_original(rows, target):
    scrip, period, basis = (
        target["scrip_code"],
        date.fromisoformat(target["period_end"]),
        target["basis"],
    )
    wanted = f"{basis}-{calendar.month_abbr[period.month]}-Qtr-{period.year}".casefold()
    candidates = []
    revisions = set()
    for row in rows:
        if not isinstance(row, dict):
            return None, "invalid_issuer_catalogue_row"
        paths = {u for k in ("XbrlFile", "xbrlurl") if (u := _filing_url(row.get(k)))}
        status = (row.get("status") or "").strip().lower()
        if (
            status != "new"
            or row.get("revised_date_time")
            or (row.get("revised_reson") or "").strip()
        ):
            revisions.update(paths)
        if str(row.get("Quarter_Name") or "").casefold() != wanted:
            continue
        # A market ticker by itself cannot certify an issuer; the lookup, the
        # issuer URL (if present), and the document's accession agree on BSE code.
        if row.get("seourl") and urlsplit(row["seourl"]).path.rstrip("/").split("/")[-1] != scrip:
            return None, "issuer_catalogue_mismatch"
        new = (
            status == "new"
            and not row.get("revised_date_time")
            and not (row.get("revised_reson") or "").strip()
        )
        at = census.stamp(row.get("filing_date_time" if new else "revised_date_time"))
        if not at:
            return None, "undated_integrated_edition"
        candidates.append((at, new, paths, row))
    if not candidates:
        return None, "no_integrated_quarter"
    first = min(c[0] for c in candidates)
    if first != census.stamp(target["published_at"]):
        return None, "original_publication_clocks_differ"
    same = [x for x in candidates if x[0] == first]
    if any(not x[1] for x in same):
        return None, "ambiguous_or_revised_first_integrated_edition"
    urls = {u for c in same for u in c[2]}
    if len(urls) != 1 or any(not x[2] for x in same):
        return None, "ambiguous_or_missing_integrated_original_link"
    url = next(iter(urls))
    if url in revisions:
        return None, "integrated_filename_reused_by_revision"
    if not _accession_equal(target["archive_url"], url, scrip):
        return None, "edition_accession_differs"
    return {
        "inline_url": url,
        "publisher_row": same[0][3],
        "linked_edition_time": first.isoformat(),
    }, "matched"


def catalogue_map(db):
    grouped = defaultdict(set)
    for url, sha in db.execute(
        "SELECT DISTINCT url,sha FROM observations WHERE kind='inline_catalogue'"
    ):
        grouped[url].add(sha)
    return {
        url: (sha, json.loads(census.raw_by_hash(db, sha))["Table"])
        for url, shas in grouped.items()
        if len(shas) == 1
        for sha in shas
    }


def native_ok(main, good, target):
    checked, _reason = census.original_bytes(
        main, target["archive_url"], good[target["archive_url"]], target["published_at"]
    )
    if not checked:
        return False
    _, raw = checked
    values, _ = verify_facts(
        raw, **{k: target[k] for k in ("isin", "symbol", "period_end", "basis", "scrip_code")}
    )
    return values is not None


def links(db, main, targets):
    catalog = catalogue_map(db)
    _, good = census.inventory(main)
    rows = []
    for target in targets:
        url = CATALOGUE + target["scrip_code"]
        row = {**target, "integrated_catalogue_url": url}
        cat = catalog.get(url)
        if cat:
            sha, entries = cat
            proof, why = match_original(entries, target)
            row["integrated_catalogue_sha256"] = sha
            if proof:
                row.update(proof)
            row["reason"] = why
        else:
            row["reason"] = "missing_or_ambiguous_integrated_catalogue"
        if row.get("inline_url"):
            row["native_original_already_verified"] = native_ok(main, good, target)
        rows.append(row)
    return rows


def import_receipts(into, from_root=ROOT):
    """Copy distinct signed source objects/observations into the portable census archive."""
    path = (from_root / "source-archive.sqlite3").resolve(strict=True)
    into.execute("ATTACH DATABASE ? AS inline_sources", (str(path),))
    try:
        with into:
            into.execute(
                "INSERT OR IGNORE INTO objects SELECT sha, body FROM inline_sources.objects"
            )
            into.execute("""INSERT INTO observations(url,observed,status,kind,sha,headers,error)
                SELECT s.url,s.observed,s.status,s.kind,s.sha,s.headers,s.error FROM inline_sources.observations s
                WHERE NOT EXISTS (SELECT 1 FROM main.observations o WHERE o.url=s.url AND o.observed=s.observed
                  AND o.kind=s.kind AND o.sha IS s.sha)""")
    finally:
        into.execute("DETACH DATABASE inline_sources")


def save_plan(records):
    raw = b"".join(
        (json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode() for r in records
    )
    output = ROOT / "reports" / digest(raw) / "original-links.jsonl"
    write_once(output, raw)
    return output


def run(*, attempts, catalogues_only=False):
    targets = load_targets()
    # The source archive remains immutable. Re-running a newer target list simply adds receipts.
    with closing(census.connection(ROOT)) as db:
        collect(db, catalogue_urls(), "inline_catalogue", attempts)
        uri = (census.ROOT / "source-archive.sqlite3").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as main:
            records = links(db, main, targets)
        report = save_plan(records)
        print(
            "Current edition-link proof", report, Counter(r["reason"] for r in records), flush=True
        )
        if not catalogues_only:
            collect(
                db,
                [
                    r["inline_url"]
                    for r in records
                    if r.get("inline_url")
                    and not r["native_original_already_verified"]
                    and r["inline_url"] != r["archive_url"]
                ],
                "xbrl",
                attempts,
            )
    return report


def global_originals(attempts):
    """Fetch only the named integrated originals selected by the joint venue clocks."""
    rows = [json.loads(line) for line in GLOBAL_TARGETS.read_bytes().splitlines()]
    items = [r for r in rows if r["venue"] == "BSE Integrated"]
    if any(r["season"] not in {"2025", "2026"} or _filing_url(r["url"]) != r["url"] for r in items):
        raise ValueError("Unexpected year or official URL in the global integrated queue")
    with (
        closing(census.connection(ROOT)) as db,
        closing(census.connection(ROOT / "controls")) as controls,
    ):
        urls = {r["url"] for r in items}
        for run_no in range(1, attempts + 1):
            collect(db, urls, "xbrl", run_no, controls=controls)
            include_target_control_bytes(db, controls, urls)
            seen, good = census.inventory(db)
            print(
                f"Integrated attempt {run_no} over {len(urls)} original URLs, "
                f"{sum(bool(good[u]) for u in urls)} with validated XBRL bytes, "
                f"{sum(seen[u] >= run_no for u in urls)} with {run_no}+ observations",
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--catalogues-only", action="store_true")
    parser.add_argument("--global-originals", action="store_true")
    opts = parser.parse_args()
    if opts.global_originals:
        global_originals(opts.attempts)
        return
    for attempt in range(1, opts.attempts + 1):
        run(attempts=attempt, catalogues_only=opts.catalogues_only)


if __name__ == "__main__":
    main()
