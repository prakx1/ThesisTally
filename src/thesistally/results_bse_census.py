"""Supplement the signed D2 XBRL count from BSE's edition-level historical archive."""

import calendar
import csv
import gzip
import io
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from .discovery_guard import REPO
from .discovery_registry import digest, encoded, write_once
from .fundamental_coverage import IST, METRICS, csv_text, first_session, has_field, ledger_bytes
from .fundamental_notice_proof import REVISION
from .results_forward_bse import HEADERS
from .results_xbrl_coverage import candidates, original_in_basis

ARCHIVE = "https://api.bseindia.com/BseIndiaAPI/api/Result_Arch_ng/w?SCRIP_CD="
WEB = "https://www.bseindia.com"
ROOT = REPO / "data/fundamentals/d2-bse"
NSE = REPO / "data/fundamentals/d2-xbrl"
SOURCE = "f933119325f4b163e3ed8c5659b3b87fd836bab9b0cbf059d58fd63343f23410"
BASIS = ("consolidated", "standalone")
SEASONS = (2022, 2024, 2023, 2020, 2021, 2019, 2018, 2017, 2025, 2026)
XR = "{http://www.xbrl.org/2003/instance}"
IX = ("{http://www.xbrl.org/2013/inlineXBRL}", "{http://www.xbrl.org/2008/inlineXBRL}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (sha TEXT PRIMARY KEY, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY, url TEXT NOT NULL, observed TEXT NOT NULL, status INTEGER,
    kind TEXT NOT NULL, sha TEXT, headers TEXT NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS url_observations ON observations(url,kind,id);
"""


def connection(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "source-archive.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def stamp(raw):
    if not isinstance(raw, str) or "T" not in raw:
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at.replace(tzinfo=IST) if at.tzinfo is None else at.astimezone(IST)


def entries(page, code):
    """Group original and revised issuer submissions by accounting scope and quarter."""
    out = defaultdict(list)
    for item in page["Table"]:
        if "scripcode" not in item or str(item["scripcode"]) != code:
            raise ValueError("Issuer disagreement in BSE result-archive catalogue")
        label = item.get("Quarter") or ""
        match = re.match(r"^(Consolidated|Standalone)-([A-Z][a-z]{2})-(\d{2});", label)
        if not match or item.get("Fld_QuarterName") not in {"Quarter", "Year", "Half Year"}:
            continue
        basis, month, year = match.groups()
        if month not in calendar.month_abbr:
            continue
        m, y = list(calendar.month_abbr).index(month), 2000 + int(year)
        period = date(y, m, calendar.monthrange(y, m)[1]).isoformat()
        basis = basis.lower()
        url = item.get("conso_xbrl_link" if basis == "consolidated" else "stand_xbrl_link") or ""
        parts = urlsplit(urljoin(WEB, url))
        if (
            parts.netloc != "www.bseindia.com"
            or parts.scheme != "https"
            or not parts.path.upper().startswith("/XBRLFILES/")
            or not parts.path.lower().endswith((".xml", ".xhtml", ".html", ".htm"))
        ):
            continue
        status = item.get("Status")
        created, revised = stamp(item.get("Filing_Date_Time")), stamp(item.get("Revised_Date_Time"))
        original = (
            status == "New"
            and created is not None
            and revised is None
            and not (item.get("Revision_Reason") or "").strip()
        )
        when = created if original else revised or created
        if when and when.date().isoformat() <= period:
            when = None
        out[(period, basis)].append(
            {
                "url": parts.geturl(),
                "at": when.isoformat() if when else None,
                "new": original,
                "status": status,
                "has_revision_marker": status != "New"
                or bool(revised)
                or bool((item.get("Revision_Reason") or "").strip()),
                "reason": item.get("Revision_Reason"),
                "filing_scope": item["Fld_QuarterName"],
            }
        )
    return out


def first_editions(rows, cutoff, sessions):
    if not rows:
        return [], "no_named_xbrl"
    timed_urls = {r["url"] for r in rows if r["at"] and r["new"]}
    # A named original with unknown clock could precede all visible editions.
    if any(not r["at"] and r["status"] == "New" and r["url"] not in timed_urls for r in rows):
        return [], "undated_named_original"
    usable = [
        (r, first_session(datetime.fromisoformat(r["at"]), sessions)) for r in rows if r["at"]
    ]
    usable = [(r, s) for r, s in usable if s and s <= cutoff]
    if not usable:
        return [], "no_named_edition_by_cutoff"
    earliest = min(r["at"] for r, _ in usable)
    first = {r["url"]: (r, s) for r, s in usable if r["at"] == earliest and r["new"]}
    if not first:
        return [], "first_edition_is_revised_or_original_unverified"
    # A subsequently revised URL with unchanged filename cannot establish original bytes.
    revised_urls = {r["url"] for r in rows if r["has_revision_marker"]}
    safe = [first[u] for u in sorted(first) if u not in revised_urls]
    return safe, "" if safe else "original_filename_reused_by_revision"


def classify(url, status, raw):
    if status != 200:
        return "http_error"
    if url.startswith(ARCHIVE):
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("Table"), list):
                entries(data, url.removeprefix(ARCHIVE))
                return "catalogue"
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return "unverified_catalogue"
    if b"xbrl" not in raw[:32000].lower():
        return "not_xbrl"
    try:
        tree = ElementTree.fromstring(raw)
    except (ValueError, ElementTree.ParseError):
        return "not_xbrl"
    if tree.tag == XR + "xbrl" or (
        any(tree.iter(XR + "context")) and any(v.tag.startswith(IX) for v in tree.iter())
    ):
        return "xbrl"
    return "not_xbrl"


def request(client, url):
    try:
        response = client.get(url)
        raw = response.content
        return (
            url,
            datetime.now(UTC).isoformat(),
            response.status_code,
            classify(url, response.status_code, raw),
            raw,
            {
                k: v
                for k, v in response.headers.items()
                if k
                in {"content-type", "date", "etag", "last-modified", "akamai-grn", "retry-after"}
            },
            None,
        )
    except httpx.HTTPError as exc:
        return (
            url,
            datetime.now(UTC).isoformat(),
            None,
            "transport_error",
            None,
            {},
            type(exc).__name__,
        )


def save(db, receipt):
    url, observed, status, kind, raw, headers, error = receipt
    sha = digest(raw) if raw is not None else None
    if sha and not db.execute("SELECT 1 FROM objects WHERE sha=?", (sha,)).fetchone():
        db.execute(
            "INSERT INTO objects VALUES(?,?)", (sha, gzip.compress(raw, compresslevel=2, mtime=0))
        )
    db.execute(
        "INSERT INTO observations(url,observed,status,kind,sha,headers,error) VALUES (?,?,?,?,?,?,?)",
        (url, observed, status, kind, sha, json.dumps(headers, sort_keys=True), error),
    )


def raw_by_hash(db, sha):
    row = db.execute("SELECT body FROM objects WHERE sha=?", (sha,)).fetchone()
    raw = gzip.decompress(row[0])
    if digest(raw) != sha:
        raise ValueError("BSE archived object hash mismatch")
    return raw


def inventory(db):
    attempts = Counter()
    good = defaultdict(set)
    for url, kind, sha, count in db.execute(
        "SELECT url,kind,sha,COUNT(*) FROM observations GROUP BY url,kind,sha"
    ):
        attempts[url] += count
        if kind in {"catalogue", "xbrl"}:
            good[url].add(sha)
    return attempts, good


def signed_inputs():
    raw = (NSE / "coverage.json").read_bytes()
    if digest(raw) != SOURCE:
        raise ValueError("Signed NSE D2 report changed")
    prior = json.loads(raw)
    raw = (NSE / "stock-quarters.csv").read_bytes()
    if digest(raw) != prior["stock_quarters_sha256"]:
        raise ValueError("Signed stock-quarter count changed")
    calendar_raw = (REPO / "data/baseline/preflight.json").read_bytes()
    if digest(calendar_raw) != prior["preflight_sha256"]:
        raise ValueError("Signed NSE calendar changed")
    gaps = [
        r
        for r in csv.DictReader(raw.decode().splitlines())
        if r["covered"] == "False" and int(r["season"]) in SEASONS
    ]
    return prior, gaps, json.loads(calendar_raw)["sessions"]


def leads(root):
    out = defaultdict(set)
    for path in sorted(root.glob("issuer-leads*.json")):
        for v in json.loads(path.read_bytes())["leads"]:
            if not re.fullmatch(r"\d{6}", str(v["scrip_code"])):
                raise ValueError("Invalid BSE issuer routing lead")
            out[v["isin"]].add(str(v["scrip_code"]))
    if not out:
        raise ValueError("BSE issuer routing leads unavailable")
    return out


def collect(db, urls, expected, max_attempts):
    attempts, good = inventory(db)
    todo = [u for u in dict.fromkeys(urls) if not good[u] and attempts[u] < max_attempts]
    if not todo:
        return
    # Reuse the TLS context but rotate connections across ordinary CDN retries.
    with (
        httpx.Client(
            headers={**HEADERS, "Cache-Control": "no-cache"},
            timeout=14,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=0),
        ) as client,
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        pending = set()
        completed = 0
        queue = iter(todo)
        depleted = False
        while not depleted or pending:
            while not depleted and len(pending) < 8:
                url = next(queue, None)
                if url is None:
                    depleted = True
                    break
                pending.add(pool.submit(request, client, url))
                time.sleep(0.22)
            if not pending:
                break
            ready, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in ready:
                row = future.result()
                with db:
                    save(db, row)
                completed += 1
                if row[2] == 429:
                    delay = row[5].get("retry-after", "")
                    time.sleep(float(delay) if delay.isdigit() else 60)
            if completed == len(todo) or completed // 100 != (completed - len(ready)) // 100:
                print(f"{expected}: {completed}/{len(todo)}", flush=True)


@lru_cache(maxsize=1)
def nse_editions():
    prior, _, _ = signed_inputs()
    raw = ledger_bytes(REPO / "data/fundamentals", "nse", prior["filing_ledgers"]["nse"])
    cohorts = candidates(json.loads(line) for line in io.BytesIO(raw))
    first = {}
    for (isin, period), records in cohorts.items():
        for basis in BASIS:
            rows = [r for r in records if r["basis"] == basis]
            if not rows:
                continue
            earliest = min(datetime.fromisoformat(r["filed_at"]) for r in rows)
            originals = [
                r
                for r in rows
                if datetime.fromisoformat(r["filed_at"]) == earliest
                and r["filing_status"] != "revision"
                and r.get("exchange_filing_type") != "Revision"
                and not REVISION.search(r.get("exchange_revision_remark") or "")
            ]
            fields = {
                key: {
                    r["structured_values"][key]
                    for r in originals
                    if r.get("structured_sha256") and has_field(r["structured_values"], key)
                }
                for key in METRICS
            }
            first[(isin, period, basis)] = (
                earliest,
                bool(originals),
                fields,
                original_in_basis(rows)[0],
            )
    return first


def chronology(doc, nse):
    if not nse:
        return "", {}
    first, original, fields = nse[:3]
    moment = datetime.fromisoformat(doc["at"])
    if first < moment:
        return "earlier_nse_xbrl", {}
    if first > moment:
        return "", {}
    if not original:
        return "simultaneous_nse_original_unverified", {}
    return "", fields


def catalogues(db, lead_map):
    _, good = inventory(db)
    catalog = {}
    for code in sorted({c for codes in lead_map.values() for c in codes}):
        shas = good[ARCHIVE + code]
        if len(shas) == 1:
            sha = next(iter(shas))
            catalog[code] = (sha, entries(json.loads(raw_by_hash(db, sha)), code))
    return catalog


def plan(db, gaps, lead_map, sessions, *, catalog=None):
    nse = nse_editions()
    if catalog is None:
        catalog = catalogues(db, lead_map)
    items = []
    for r in sorted(
        gaps, key=lambda q: (SEASONS.index(int(q["season"])), q["period_end"], q["isin"])
    ):
        alternatives = {}
        rejections = set()
        for basis in BASIS:
            files = []
            for code in sorted(lead_map[r["isin"]]):
                if code not in catalog:
                    continue
                sha, source = catalog[code]
                first, reason = first_editions(
                    source.get((r["period_end"], basis), []), r["window_end"], sessions
                )
                if reason:
                    rejections.add(reason)
                for doc, session in first:
                    gate, coincident = chronology(doc, nse.get((r["isin"], r["period_end"], basis)))
                    if gate:
                        rejections.add(gate)
                        continue
                    files.append(
                        {
                            **doc,
                            "usable": session,
                            "catalogue_sha256": doc.get("publisher_sha", sha),
                            "code": code,
                            "basis": basis,
                            "coincident_nse_fields": coincident,
                        }
                    )
            alternatives[basis] = files
        items.append((r | {"bse_source_rejections": "|".join(sorted(rejections))}, alternatives))
    return items


def main():
    # All paths are fixed to the linked research worktree. The public-source launcher enforces this boundary.
    _, gaps, sessions = signed_inputs()
    lead_map = leads(ROOT)
    with connection(ROOT) as db:
        codes = sorted({c for r in gaps for c in lead_map[r["isin"]]})
        for run in range(1, 4):
            collect(db, [ARCHIVE + c for c in codes], "catalogue", run)
        items = plan(db, gaps, lead_map, sessions)
        urls = [d["url"] for _, a in items for b in BASIS for d in a[b]]
        # Prioritize closest windows, while allowing retries to reach every eligible URL.
        for run in range(1, 4):
            collect(db, urls, "xbrl attempt " + str(run), run)


def original_bytes(db, url, hashes, at, *, require_dated=False):
    """Return edition evidence, with a dated publisher object and no hash conflict."""
    if len(hashes) != 1:
        return None, "unretrieved_original" if not hashes else "conflicting_object_versions"
    sha = next(iter(hashes))
    headers = [
        json.loads(r[0])
        for r in db.execute(
            "SELECT headers FROM observations WHERE url=? AND sha=? AND kind='xbrl'", (url, sha)
        )
    ]
    dates = []
    for h in headers:
        if h.get("last-modified"):
            try:
                seen = parsedate_to_datetime(h["last-modified"])
                if seen.utcoffset() is None:
                    return None, "unverifiable_last_modified"
                dates.append(seen)
            except (TypeError, ValueError, OverflowError):
                return None, "unverifiable_last_modified"
    if require_dated and not dates:
        return None, "undated_bytes_for_conflicting_publication_clocks"
    if dates and max(dates) > datetime.fromisoformat(at):
        return None, "object_modified_after_filing"
    return (sha, raw_by_hash(db, sha)), "verified_edition"


def evaluate(db, case, alts, good, inline=None):
    from .results_bse_facts import verify_facts
    from .results_bse_inline import CATALOGUE, match_original

    reasons = set()
    for basis in BASIS:
        matches = []
        contradiction = False
        first_at = min((i["at"] for i in alts[basis]), default=None)
        for item in alts[basis]:
            if item["at"] != first_at:
                continue
            options = {item["url"]: ""}
            if inline and (cat := inline.get(CATALOGUE + item["code"])):
                target = {
                    "basis": basis,
                    "published_at": item["at"],
                    "period_end": case["period_end"],
                    "archive_url": item["url"],
                    "scrip_code": item["code"],
                }
                alternate, _ = match_original(cat[1], target)
                if alternate:
                    options[alternate["inline_url"]] = cat[0]
            for url, source_sha in options.items():
                checked, reason = original_bytes(
                    db,
                    url,
                    good[url],
                    item["at"],
                    require_dated=item.get("requires_dated_bytes", False),
                )
                if checked is None:
                    reasons.add(reason)
                    continue
                sha, raw = checked
                values, reason = verify_facts(
                    raw,
                    symbol=case["symbol"],
                    isin=case["isin"],
                    period_end=case["period_end"],
                    basis=basis,
                    scrip_code=item["code"],
                )
                if values and any(
                    any(x != values[k] for x in vals)
                    for k, vals in item["coincident_nse_fields"].items()
                ):
                    contradiction = True
                    reasons.add("conflicting_simultaneous_nse_and_bse_originals")
                    continue
                if values:
                    matches.append(
                        (
                            item | {"asset_url": url, "inline_catalogue_sha256": source_sha},
                            sha,
                            values,
                        )
                    )
                else:
                    reasons.add(reason)
        if matches and not contradiction:
            at = min(m[0]["at"] for m in matches)
            first = [m for m in matches if m[0]["at"] == at]
            if (
                len(
                    {
                        tuple(m[2][k] for k in ("revenue", "operating_profit", "net_profit", "eps"))
                        for m in first
                    }
                )
                != 1
            ):
                reasons.add("conflicting_simultaneous_first_issues")
                continue
            i, sha, v = min(first, key=lambda m: m[0]["url"])
            return {
                "covered": True,
                "basis": basis,
                "filed_at": i["at"],
                "usable_session": i["usable"],
                "scrip_code": i["code"],
                "catalogue_sha256": i["catalogue_sha256"],
                "url": i["asset_url"],
                "edition_url": i["url"],
                "inline_catalogue_sha256": i["inline_catalogue_sha256"],
                "sha256": sha,
                "revenue": v["revenue"],
                "operating_profit": v["operating_profit"],
                "net_profit": v["net_profit"],
                "eps": v["eps"],
                "tags": json.dumps(v["tags"], sort_keys=True),
                "reason": "",
            }
    return {
        "covered": False,
        "basis": "",
        "filed_at": "",
        "usable_session": "",
        "scrip_code": "",
        "catalogue_sha256": "",
        "url": "",
        "edition_url": "",
        "inline_catalogue_sha256": "",
        "sha256": "",
        "revenue": "",
        "operating_profit": "",
        "net_profit": "",
        "eps": "",
        "tags": "",
        "reason": "|".join(sorted(reasons)) if reasons else "no_eligible_named_original_by_cutoff",
    }


def report(*, root=ROOT):
    prior, gaps, sessions = signed_inputs()
    from .results_bse_inline import catalogue_map
    from .results_bse_priority import YEARS, merge_catalogues, reconcile

    routing = leads(root)
    with connection(root) as db:
        db.execute("BEGIN")
        attempts, good = inventory(db)
        catalog = catalogues(db, routing)
        integrated = catalogue_map(db)
        catalog, integrated_codes = merge_catalogues(catalog, integrated)
        priority_rows, earlier_targets, unresolved_priority_urls = reconcile(
            db, catalog, routing, sessions, good, integrated, integrated_codes
        )
        anchored = Counter(int(r["season"]) for r in priority_rows if r["supported"])
        settled_by_bse = Counter(int(r["season"]) for r in priority_rows if r["venue"] == "BSE")
        unsettled = Counter(int(r["season"]) for r in priority_rows if not r["supported"])
        records = plan(db, gaps, routing, sessions, catalog=catalog)
        planned_documents = {i["url"] for _, alts in records for v in alts.values() for i in v}
        target_catalogues = {ARCHIVE + c for r in gaps for c in routing[r["isin"]]}
        attempted = {
            str(n): sum(attempts[u] == n and not good[u] for u in planned_documents)
            for n in range(3)
        }
        schedule = {
            "previously_covered_contender_urls": len(earlier_targets),
            "unresolved_priority_urls": len(unresolved_priority_urls),
            "unresolved_priority_urls_with_fewer_than_three_attempts": sum(
                not good[u] and attempts[u] < 3 for u in unresolved_priority_urls
            ),
            "target_catalogues": len(target_catalogues),
            "archived_catalogues": sum(bool(good[u]) for u in target_catalogues),
            "original_document_urls": len(planned_documents),
            "original_urls_with_bytes": sum(bool(good[u]) for u in planned_documents),
            "unverified_urls_with_fewer_than_three_attempts": attempted,
            "unverified_urls_with_at_least_three_attempts": sum(
                attempts[u] >= 3 and not good[u] for u in planned_documents
            ),
            "incomplete_catalogue_requests": sum(
                not good[u] and attempts[u] < 3 for u in target_catalogues
            ),
        }
        detail = []
        unresolved_gap_urls = set()
        for case, alts in records:
            evidence = evaluate(db, case, alts, good, integrated)
            codes = sorted(routing[case["isin"]])
            cats = [c for c in codes if len(good[ARCHIVE + c]) == 1]
            urls = {i["url"] for values in alts.values() for i in values}
            if evidence["covered"] and (
                len(cats) != len(codes)
                or (int(case["season"]) in YEARS and any(c not in integrated_codes for c in codes))
            ):
                evidence["covered"] = False
                evidence["reason"] = "unresolved_competing_exchange_catalogue"
            if not codes:
                evidence["reason"] = "issuer_routing_unavailable"
            elif not cats:
                evidence["reason"] = "issuer_catalogue_unavailable"
            elif not evidence["covered"] and case["bse_source_rejections"]:
                current = set(evidence["reason"].split("|")) - {
                    "no_eligible_named_original_by_cutoff"
                }
                evidence["reason"] = "|".join(
                    sorted(current | set(case["bse_source_rejections"].split("|")))
                )
            if not evidence["covered"]:
                unresolved_gap_urls.update(urls)
            detail.append(
                {k: case[k] for k in ("season", "isin", "symbol", "period_end", "window_end")}
                | {
                    "candidate_codes": "|".join(codes),
                    "verified_catalogues": len(cats),
                    "original_urls": len(urls),
                    "verified_xbrl_objects": sum(bool(good[u]) for u in urls),
                    "original_urls_attempted": sum(bool(attempts[u]) for u in urls),
                }
                | evidence
            )
        schedule["unresolved_gap_urls_with_fewer_than_three_attempts"] = sum(
            not good[u] and attempts[u] < 3 for u in unresolved_gap_urls
        )
        statuses = {
            str(r[0]): r[1]
            for r in db.execute("SELECT kind,COUNT(*) FROM observations GROUP BY kind")
        }
    windows = []
    for n in prior["windows"]:
        year = n["season"]
        cases = [r for r in detail if int(r["season"]) == year]
        covered = sum(r["covered"] for r in cases)
        unresolved = Counter(r["reason"] for r in cases if not r["covered"])
        windows.append(
            {
                "year": year,
                "eligible": n["eligible"],
                "nse_verified": n["covered"],
                "bse_recovered": covered,
                "gap_quarters_with_edition_candidates": sum(
                    bool(r["original_urls"]) for r in cases
                ),
                "first_edition_urls_across_quarters": sum(r["original_urls"] for r in cases),
                "gap_quarters_with_xml_bytes": sum(bool(r["verified_xbrl_objects"]) for r in cases),
                "signed_nse_with_unsettled_cross_venue_priority": unsettled[year],
                "previously_covered_global_first_confirmed_from_bse": settled_by_bse[year],
                "verified_global_first_lower": anchored[year] + covered,
                "verified_global_first_upper": n["covered"] + covered,
                "combined_verified": covered + anchored[year],
                "signed_nse_plus_gap_verified": covered + n["covered"],
                "needed": n["required"],
                "coverage_pct": 100 * (covered + anchored[year]) / n["eligible"],
                "named_gap_ceiling_if_all_missing_bytes_were_verifiable": n["covered"]
                + sum(bool(r["original_urls"]) for r in cases),
                "pass": anchored[year] + covered >= n["required"],
                "signed_nse_plus_eligible_bse_gaps_pass": covered + n["covered"] >= n["required"],
                "uncovered_reasons": dict(sorted(unresolved.items())),
            }
        )
    detail_csv = csv_text(detail).encode()
    priority_csv = csv_text(priority_rows).encode()
    result = {
        "signed_nse_count_sha256": SOURCE,
        "stock_quarters_sha256": digest(detail_csv),
        "previously_covered_sha256": digest(priority_csv),
        "signed_nse_plus_eligible_bse_gaps_passing_windows": sum(
            r["signed_nse_plus_eligible_bse_gaps_pass"] for r in windows
        ),
        "passing_windows": sum(r["pass"] for r in windows),
        "required_windows": 5,
        "strict_global_first_windows_lower": sum(
            w["verified_global_first_lower"] >= w["needed"] for w in windows
        ),
        "strict_global_first_windows_upper": sum(
            w["verified_global_first_upper"] >= w["needed"] for w in windows
        ),
        "windows": windows,
        "observations": statuses,
        "bse_gap_cohort": len(detail),
        "request_coverage": schedule,
    }
    out = root / "reports" / digest(encoded(result))
    write_once(out / "coverage.json", encoded(result))
    write_once(out / "stock-quarters.csv", detail_csv)
    write_once(out / "previously-covered.csv", priority_csv)
    write_once(
        out / "windows.csv",
        csv_text(
            [{k: v for k, v in w.items() if k != "uncovered_reasons"} for w in windows]
        ).encode(),
    )
    print(
        json.dumps(
            {
                "path": str(out),
                "windows": [(w["year"], w["bse_recovered"], w["pass"]) for w in windows],
                "passing": result["passing_windows"],
                "observations": statuses,
            },
            sort_keys=True,
        )
    )
    return result
