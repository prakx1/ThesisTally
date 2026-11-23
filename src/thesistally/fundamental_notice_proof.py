"""Bind earlier earnings release times to the original filing with numerical evidence."""

from __future__ import annotations

import calendar
import html
import json
import re
import subprocess
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlsplit

import httpx

from .fundamental_nse import HEAD, ROOT, digest, pack, public_copy, write_json
from .fundamental_period import (
    ANNUAL_ACCOUNTS,
    fiscal_year_candidate,
    normalize,
    refers_only_to_other_ends,
)

RESULT = re.compile(
    r"financial result|quarterly result|audited result|unaudited result|result.*quarter ended",
    re.IGNORECASE,
)
UNRELATED = re.compile(
    r"newspaper|publish audited results|press confer|conference call|trading window|notice of|analyst|intimation of board meeting|copy of.*advertisement|propos\w* to|intends to|will consider|investor presentation",
    re.IGNORECASE,
)
REVISION = re.compile(
    r"revis(?:ed|ion)|corrigendum|correct(?:ed|ion)|resubmi|re-submi|restat", re.IGNORECASE
)


SCHEDULING_CATEGORY = re.compile(
    r"board meeting (?:intimation|postponed|rescheduled|deferred|cancelled)|"
    r"option to submit standalone/consolidated financial results|reasons for delayed/non-submission of financial results",
    re.IGNORECASE,
)
PROSPECTIVE = re.compile(
    r"(?:meeting|board).{0,130}(?:is scheduled to|will be held|shall be held|is being rescheduled|has been postponed)|"
    r"(?:shall|will) (?:be published|consider and approve)|"
    r"^.{0,28}to consider (?:the )?(?:un)?audited financial results",
    re.IGNORECASE,
)
PUBLISHED = re.compile(
    r"(?:has|have|hereby) (?:considered and )?(?:approved|enclosed|attached|submitted)|"
    r"please find (?:enclosed|attached)|financial results (?:were|are) (?:enclosed|approved)|provisional results",
    re.IGNORECASE,
)


def prospective_notice(description, text):
    # An agenda giving the date of a future results meeting contains no release clock.
    # Preserve ambiguous or mixed notices which also disclose already-published figures.
    return bool(
        (SCHEDULING_CATEGORY.fullmatch(description.strip()) or PROSPECTIVE.search(text))
        and not PUBLISHED.search(text)
    )


def release_key(notice):
    """Identical attachment duplicated across exchange categories is one disclosure."""
    return notice["announced_at"], str(notice.get("source_url"))


def period_match(text, day):
    text = normalize(text)
    if fiscal_year_candidate(text, day):
        return True
    iso = day.isoformat()
    month = calendar.month_name[day.month]
    d, y = str(day.day), str(day.year)
    short = month[:3] + "(?:" + month[3:] + ")?"
    return bool(
        re.search(
            rf"\b(?:{d}(?:st|nd|rd|th)?\s*[-./ ,]*\s*{short}[-./ ,]+{y}|{short}[-./ ,]+{d}(?:st|nd|rd|th)?[-./ ,]+{y}|{d}[-./]0?{day.month}[-./](?:{y}|{y[-2:]})|{iso})\b",
            text,
            re.IGNORECASE,
        )
    )


def candidates(row, notices):
    """Explicit result notices may be days before the later quarterly table.

    Unspecified board outcomes just before the first tabular publication require proof
    as well. Do not certify the later table if an earlier likely filing cannot be read.
    """
    end = date.fromisoformat(row["period_end"])
    related = []
    for n in notices:
        stamp = n["announced_at"]
        if n.get("release_language") == "identical_earnings_attachment":
            continue
        if not row["period_end"] < stamp[:10] or stamp > row["filed_at"]:
            continue
        if n.get("isin") and n["isin"] != row["isin"] and n["isin"][:7] != row["isin"][:7]:
            continue
        if n.get("declared_basis") and n["declared_basis"] != row["basis"]:
            continue
        if n.get("declared_period_end") and n["declared_period_end"] != row["period_end"]:
            continue
        if n.get("declared_duration") == "year":
            continue
        if n.get("release_language") in {"legacy_results_summary", "audited_original_case"}:
            related.append(n)
            continue
        desc, text = n["description"], n["text"]
        cross_category = n.get("release_language") == "possible_original_financial_packet"
        if prospective_notice(desc, text) or (
            not cross_category and (UNRELATED.search(desc) or UNRELATED.search(text[:120]))
        ):
            continue
        mentions = period_match(text, end)
        if not mentions and refers_only_to_other_ends(text, end):
            # Only an actual conflicting calendar end establishes that the notice
            # concerns another period. A bare fiscal label does not.
            continue
        if (
            n.get("release_language")
            in {"issuer_earnings_release", "possible_original_financial_packet"}
            or RESULT.search(desc + " " + text)
            or ANNUAL_ACCOUNTS.search(text)
        ):
            related.append(n)
        elif "board meeting" in desc.lower():
            # A generic board outcome can precede the later formal XBRL by weeks.
            # Require its original document unless the notice expressly describes
            # a different agenda and does not mention results.
            other_agenda = re.search(
                r"allotment|preferential issue|raising of funds|appointment of|resignation of|divestment|issuance of|issue of debentures|acquisition of|chairman|share buyback|stock option",
                text,
                re.IGNORECASE,
            )
            if not other_agenda:
                related.append(n)
    confirmed = {(n["announced_at"], n.get("source_url"), n.get("sequence")) for n in related}
    for n in notices:
        if n.get("release_language") != "identical_earnings_attachment":
            continue
        later = n["later_matching_notice"]
        key = (later["announced_at"], later["source_url"], later["sequence"])
        if (
            key in confirmed
            and n["source_url"] == later["source_url"]
            and n["symbol"] == row["symbol"]
            and n["announced_at"] <= later["announced_at"]
            and row["period_end"] < n["announced_at"][:10]
        ):
            related.append(n)
    from .fundamental_scope import declared_other

    out = []
    for n in related:
        other = declared_other(n, row["basis"]) if row.get("basis") else None
        out.append({**n, "declared_other_basis": other} if other else n)
    return sorted(out, key=release_key)


def firsts(plans):
    out = defaultdict(list)
    for row in plans:
        if row["metadata"].get("period") == "Annual" and (row["source_url"] or "").endswith(
            ".html"
        ):
            # A dedicated old annual table contains no current-quarter column.
            continue
        out[(row["isin"], row["period_end"], row["basis"])].append(row)
    return {k: sorted(v, key=lambda x: (x["filed_at"], x["id"])) for k, v in out.items()}


def archive_attachment(notice):
    direct = str(notice.get("source_url") or "")
    if urlsplit(direct).scheme == "https":
        return direct
    link = re.search(
        r"""<a\b[^>]*?href\s*=\s*['"]?([^'">\s]+)""", notice.get("text", ""), re.IGNORECASE
    )
    if link:
        value = html.unescape(link[1])
        if value.lower().startswith("/content/corporate/"):
            return urljoin("https://archives.nseindia.com", value)
    return notice.get("source_url")


def audited_through(history, row):
    """Only an explicitly bounded official notice census can attest an empty interval."""
    from datetime import datetime
    from urllib.parse import parse_qs, urlsplit

    if history.get("error") or not history.get("total") or not history.get("from_date"):
        return None
    end = history.get("to_date")
    if not end:
        values = parse_qs(urlsplit(history.get("url") or "").query).get("to_date", [])
        if len(values) != 1:
            return None
        try:
            end = datetime.strptime(values[0], "%d-%m-%Y").date().isoformat()
        except ValueError:
            return None
    if history["from_date"] <= row["period_end"] and row["filed_at"][:10] <= end:
        return row["filed_at"]
    return None


def merged_manifest():
    primary = json.loads((ROOT / "notice-manifest.json").read_bytes())
    by_symbol = {r["symbol"]: r for r in primary}
    alternate = ROOT / "notice-fallback-manifest.json"
    if alternate.exists():
        for candidate in json.loads(alternate.read_bytes()):
            original = by_symbol.get(candidate["symbol"], {})
            if (
                candidate.get("total")
                and not candidate.get("error")
                and (
                    not original.get("total")
                    or original.get("error")
                    or candidate.get("from_date", "9999") < original.get("from_date", "2011-01-01")
                )
            ):
                by_symbol[candidate["symbol"]] = candidate
    market = ROOT / "notice-market-manifest.json"
    market_rows = ROOT / "notice-market-rows.json"
    if market.exists() and market_rows.exists():
        certificate = json.loads(market.read_bytes())
        counts = defaultdict(int)
        for r in json.loads(market_rows.read_bytes()):
            counts[r["symbol"]] += 1
        full_range = next(
            (
                pair
                for pair in certificate["segments"]
                if pair[0] <= "2011-01-01" and pair[1] >= "2026-11-20"
            ),
            None,
        )
        if full_range:
            for symbol in certificate["symbols"]:
                if not counts[symbol]:
                    continue
                existing = by_symbol.get(symbol, {})
                if (
                    existing.get("error")
                    or not existing.get("total")
                    or existing.get("from_date", "2011-01-01") > full_range[0]
                ):
                    by_symbol[symbol] = {
                        "symbol": symbol,
                        "total": counts[symbol],
                        "from_date": full_range[0],
                        "to_date": full_range[1],
                        "source": "all-market overlap-verified tape",
                        "url": "https://www.nseindia.in/api/corporate-announcements?index=equities",
                        "manifest_sha256": digest(market.read_bytes()),
                    }
    alias_manifest = ROOT / "notice-alias-manifest.json"
    if alias_manifest.is_file():
        alias = json.loads(alias_manifest.read_bytes())
        alias_rows = ROOT / "notice-alias-rows.json"
        if (
            digest(alias_rows.read_bytes()) != alias["rows_sha256"]
            or digest(market.read_bytes()) != alias["market_manifest_sha256"]
            or not any(a <= "2011-01-01" and b >= "2026-11-20" for a, b in alias["segments"])
        ):
            raise ValueError(
                "Historical alias bulletin provenance does not match the official tape"
            )
        for sym, n in alias["symbol_counts"].items():
            by_symbol[sym] = {
                "symbol": sym,
                "total": n["selected"],
                "from_date": "2011-01-01",
                "to_date": "2026-11-20",
                "source": "historical alias on the whole exchange bulletin",
                "url": "https://www.nseindia.in/api/corporate-announcements?index=equities",
                "manifest_sha256": digest(alias_manifest.read_bytes()),
            }
    return by_symbol


def index_notices():
    notes = defaultdict(dict)
    for name in (
        "notice-formal-review-rows.json",
        "notice-case-rows.json",
        "notice-extra-category-rows.json",
        "notice-alias-rows.json",
        "notice-language-rows.json",
        "notice-language-identical-rows.json",
        "notice-census.json",
        "notice-fallback-rows.json",
        "notice-market-rows.json",
    ):
        path = ROOT / name
        if path.exists():
            for n in json.loads(path.read_bytes()):
                n["source_url"] = archive_attachment(n)
                key = (
                    n["announced_at"],
                    n.get("source_url"),
                    n.get("sequence"),
                    n.get("accession_sha256")
                    if n.get("release_language") == "legacy_results_summary"
                    else None,
                )
                notes[n["symbol"]].setdefault(key, n)
    return defaultdict(list, {sym: list(rows.values()) for sym, rows in notes.items()})


def notice_text(raw):
    """Preserve source-member fingerprints for PDF, image and nested ZIP filings."""
    from .fundamental_media import archive_documents

    out = []
    for info, doc in archive_documents(raw, root=ROOT):
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=doc,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode:
            raise ValueError("Cannot read issuer statement PDF")
        out.append({**info, "pages": result.stdout.decode(errors="replace").split("\f")})
    if not out:
        raise ValueError("No supported PDF or image financial statement in the issuer release")
    return out


def import_case_packets(*, formal=False):
    import gzip
    from pathlib import Path

    from .fundamental_nse import pack

    name = "notice-formal-review" if formal else "notice-case"
    case = ROOT / (name + "-manifest.json")
    if not case.exists():
        return
    evidence = json.loads(case.read_bytes())
    raw_rows = (ROOT / (name + "-rows.json")).read_bytes()
    if digest(raw_rows) != evidence["rows_sha256"]:
        raise ValueError("Case catalogue changed")
    for row in json.loads(raw_rows):
        sha = row["attachment_sha256"]
        url = row["source_url"]
        actual = row.get("retrieved_url") or next(
            (
                v.get("retrieved_url")
                for v in evidence["sources"]
                if v["accession_sha256"] == row["accession_sha256"]
            ),
            None,
        )
        if not actual:
            continue
        if urlsplit(actual).hostname not in {
            "archives.nseindia.com",
            "nsearchives.nseindia.com",
            "nsearchives.nseindia.in",
        }:
            raise ValueError("Case packet came from unexpected archive")
        key = digest(url.encode())
        dest = ROOT / "notice-text" / (key + ".json.gz")
        if dest.exists():
            continue
        if formal:
            filename = Path(urlsplit(url).path).name
            raw = (ROOT.parent / "review" / "visual-working" / ("board-" + filename)).read_bytes()
        else:
            raw = gzip.decompress((ROOT / "notice-case-originals" / (sha + ".gz")).read_bytes())
        if digest(raw) != sha:
            raise ValueError("Original case packet fingerprint differs")
        docs = notice_text(raw)
        pack(ROOT / "notice-objects" / sha[:2] / (sha + ".gz"), raw)
        pack(
            dest,
            json.dumps(
                {"source_url": url, "retrieved_url": actual, "sha256": sha, "pdfs": docs},
                ensure_ascii=False,
            ).encode(),
        )
        write_json(
            ROOT / "notice-raw-receipts" / (key + ".json"),
            {"url": url, "retrieved_url": actual, "sha256": sha},
        )


def archive_notice(client, url, *, retry=False):
    p = ROOT / "notice-text" / (digest(url.encode()) + ".json.gz")
    if p.exists():
        return None
    unavailable = ROOT / "notice-request-failures" / (digest(url.encode()) + ".json")
    if unavailable.exists() and not retry:
        return json.loads(unavailable.read_bytes())
    try:
        decoded = []

        def valid(data):
            if not data.startswith((b"%PDF", b"PK")):
                return False
            if retry:
                try:
                    decoded.extend(notice_text(data))
                except (ValueError, subprocess.TimeoutExpired, zipfile.BadZipFile):
                    return False
            return True

        raw, retrieved = public_copy(client, url, accept=valid)
        sha = digest(raw)
        pack(ROOT / "notice-objects" / sha[:2] / (sha + ".gz"), raw)
        raw_receipt = {"url": url, "retrieved_url": retrieved, "sha256": sha}
        key = digest(url.encode())
        original = ROOT / "notice-raw-receipts" / (key + ".json")
        if original.exists() and json.loads(original.read_bytes())["sha256"] != sha:
            write_json(ROOT / "notice-raw-vintages" / key / (sha + ".json"), raw_receipt)
        elif not original.exists():
            write_json(original, raw_receipt)
        docs = decoded or notice_text(raw)
        pack(
            p,
            json.dumps(
                {"source_url": url, "retrieved_url": retrieved, "sha256": sha, "pdfs": docs},
                ensure_ascii=False,
            ).encode(),
        )
    except (
        httpx.HTTPError,
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as e:
        failure = [url, str(e)[:1200]]
        if not unavailable.exists():
            write_json(unavailable, failure)
        return failure
    return None


def first_sources(notices):
    # A body-only summary proves only its printed fields. Follow consecutive complete
    # summaries to the first full attachment at the original or a later timestamp.
    if not notices:
        return []
    from itertools import groupby

    out = []
    for _, group in groupby(notices, key=lambda n: n["announced_at"]):
        items = list(group)
        out.extend(items)
        if not all(
            (n.get("release_language") == "legacy_results_summary" and not n.get("source_url"))
            or n.get("declared_other_basis")
            for n in items
        ):
            break
    return out


def plan_documents(plans, earlier_plans):
    from .fundamental_notice_only import supplement

    notices = index_notices()
    plans = [*plans, *supplement(plans, notices)]
    todo = {}
    for group in firsts(plans).values():
        f = group[0]
        rows = candidates(f, notices[f["symbol"]])
        for row in first_sources(rows):
            url = row.get("source_url") or ""
            if urlsplit(url).hostname in {
                "nsearchives.nseindia.in",
                "nsearchives.nseindia.com",
                "archives.nseindia.com",
            } and url.lower().endswith((".pdf", ".zip")):
                todo[url] = row

    previous = set()
    for earlier in earlier_plans:
        previous.update(json.loads((ROOT / (earlier + ".json")).read_bytes()))
    todo = {url: n for url, n in todo.items() if url not in previous}
    return sorted(todo)


def fetch_notices(plans, *, batch="first-documents"):
    inventories = {
        "first-documents": ("notice-request-plan", "notice-document-gaps", (), 14),
        "market-documents": (
            "notice-market-extra-plan",
            "notice-extra-document-gaps",
            ("notice-request-plan",),
            4,
        ),
        "fiscal-documents": (
            "notice-fiscal-extra-plan",
            "notice-fiscal-document-gaps",
            (
                "notice-request-plan",
                "notice-market-extra-plan",
                "notice-clock-extra-plan",
                "notice-language-extra-plan",
                "notice-reconcile-extra-plan",
                "notice-alias-extra-plan",
                "notice-case-extra-plan",
                "notice-timing-extra-plan",
            ),
            4,
        ),
        "timing-documents": (
            "notice-timing-extra-plan",
            "notice-timing-document-gaps",
            (
                "notice-request-plan",
                "notice-market-extra-plan",
                "notice-clock-extra-plan",
                "notice-language-extra-plan",
                "notice-reconcile-extra-plan",
                "notice-alias-extra-plan",
                "notice-case-extra-plan",
            ),
            2,
        ),
        "case-documents": (
            "notice-case-extra-plan",
            "notice-case-document-gaps",
            (
                "notice-request-plan",
                "notice-market-extra-plan",
                "notice-clock-extra-plan",
                "notice-language-extra-plan",
                "notice-reconcile-extra-plan",
                "notice-alias-extra-plan",
            ),
            1,
        ),
        "alias-documents": (
            "notice-alias-extra-plan",
            "notice-alias-document-gaps",
            (
                "notice-request-plan",
                "notice-market-extra-plan",
                "notice-clock-extra-plan",
                "notice-language-extra-plan",
                "notice-reconcile-extra-plan",
            ),
            3,
        ),
        "reconcile-documents": (
            "notice-reconcile-extra-plan",
            "notice-reconcile-document-gaps",
            (
                "notice-request-plan",
                "notice-market-extra-plan",
                "notice-clock-extra-plan",
                "notice-language-extra-plan",
            ),
            3,
        ),
        "language-documents": (
            "notice-language-extra-plan",
            "notice-language-document-gaps",
            ("notice-request-plan", "notice-market-extra-plan", "notice-clock-extra-plan"),
            4,
        ),
        "calendar-documents": (
            "notice-clock-extra-plan",
            "notice-clock-document-gaps",
            ("notice-request-plan", "notice-market-extra-plan"),
            3,
        ),
    }
    prior = ("notice-fiscal-extra-plan", *inventories["fiscal-documents"][2])
    inventories["formal-documents"] = (
        "notice-formal-extra-plan",
        "notice-formal-document-gaps",
        prior,
        4,
    )
    inventories["category-documents"] = (
        "notice-categories-extra-plan",
        "notice-categories-document-gaps",
        (*prior, "notice-formal-extra-plan"),
        12,
    )
    plan_name, gap_name, earlier_plans, workers = inventories[batch]

    pinned = ROOT / (plan_name + ".json")
    if pinned.exists():
        todo = json.loads(pinned.read_bytes())
    else:
        todo = [] if batch == "formal-documents" else plan_documents(plans, earlier_plans)
        if batch in {"case-documents", "formal-documents"}:
            source_rows = (
                "notice-case-rows.json"
                if batch == "case-documents"
                else "notice-formal-review-rows.json"
            )
            existing = set()
            for earlier in earlier_plans:
                existing.update(json.loads((ROOT / (earlier + ".json")).read_bytes()))
            todo = sorted(
                set(todo)
                | {
                    r["source_url"]
                    for r in json.loads((ROOT / source_rows).read_bytes())
                    if r["source_url"] not in existing
                }
            )
        write_json(pinned, todo)
    gaps = []
    hooks = {}
    if batch == "category-documents":
        from .fundamental_notice_tail import Throttle

        throttle = Throttle()
        hooks = {"request": [throttle.before], "response": [throttle.after]}
    with (
        httpx.Client(
            headers=HEAD,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            event_hooks=hooks,
        ) as client,
        ThreadPoolExecutor(workers) as ex,
    ):
        for i, error in enumerate(ex.map(lambda u: archive_notice(client, u), sorted(todo)), 1):
            if error:
                gaps.append(error)
            if i % 100 == 0:
                print(i, len(todo), len(gaps), flush=True)
    write_json(ROOT / (gap_name + ".json"), gaps)


def market_documents():
    fetch_notices(
        json.loads((ROOT / "attachment-plan.json").read_bytes()), batch="market-documents"
    )


def formal_documents():
    from .fundamental_nse import load_plans

    import_case_packets(formal=True)
    fetch_notices(load_plans(ROOT), batch="formal-documents")


def category_documents():
    from .fundamental_nse import load_plans

    for name in ("notice-extra-category", "notice-formal-review"):
        meta = json.loads((ROOT / (name + "-manifest.json")).read_bytes())
        if meta["rows_sha256"] != digest((ROOT / (name + "-rows.json")).read_bytes()):
            raise ValueError("Earlier source inventory or attested projection changed")
    fetch_notices(load_plans(ROOT), batch="category-documents")


def fiscal_documents():
    from .fundamental_nse import load_plans

    fetch_notices(load_plans(ROOT), batch="fiscal-documents")


def timing_documents():
    from .fundamental_nse import load_plans

    fetch_notices(load_plans(ROOT), batch="timing-documents")


def case_documents():
    from .fundamental_nse import load_plans

    import_case_packets()
    fetch_notices(load_plans(ROOT), batch="case-documents")


def alias_documents():
    from .fundamental_nse import load_plans

    fetch_notices(load_plans(ROOT), batch="alias-documents")


def reconcile_documents():
    fetch_notices(
        json.loads((ROOT / "attachment-plan.json").read_bytes()), batch="reconcile-documents"
    )


def language_documents():
    fetch_notices(
        json.loads((ROOT / "attachment-plan.json").read_bytes()), batch="language-documents"
    )


def calendar_documents():
    fetch_notices(
        json.loads((ROOT / "attachment-plan.json").read_bytes()), batch="calendar-documents"
    )


def main():
    fetch_notices(json.loads((ROOT / "attachment-plan.json").read_bytes()))


if __name__ == "__main__":
    from .fundamental_sources import run

    run("first-documents")
