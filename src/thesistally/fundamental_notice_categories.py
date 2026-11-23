"""Project possible earlier results packets from the verified all-subject NSE bulletin."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .fundamental_notice_language import RELEASE, accession, read_period, sha
from .fundamental_notice_proof import archive_attachment
from .fundamental_nse import END, REPO, ROOT, members, published_at, write_json
from .fundamental_period import ANNUAL_ACCOUNTS

NEGATIVE = re.compile(
    r"newspaper|advertisement|clipping|notice of (?:the )?(?:board|meeting)|board meeting intimation|"
    r"analyst|conference call|con[- ]?call|earnings? call|audio (?:or video )?recording|webcast|transcript|"
    r"investor (?:presentat|meet)|trading window|prior intimation|providing information to|voting results|option to publish|option to submit|"
    r"schedul|intimation of (?:the )?(?:date of|board meeting)|intends? to|"
    r"business update|operational (?:update|performance)|provisional (?:volume|sales)|"
    r"press.{0,15}conference|postal ballot|e.voting|(?:proposes|proposed|shall|will) to?\s*(?:consider|announce|publish)|"
    r"(?:will|shall) (?:be held|consider|announce|publish)|to (?:consider|approve) (?:the )?(?:un[- ]?)?audited|subsidiar|joint venture|"
    r"annual (?:general meeting|report)|notice of.{0,20}AGM|non.submission|delay in submission|investor day",
    re.IGNORECASE,
)
FILENAME_NEGATIVE = re.compile(
    r"newspaper|paperpublication|advertis|advt|meetingintimation|intimationofboardmeeting|noticeof|conference|earningscall|concall|investormeet|investorpresent|transcript|scheduled|tradingwindow|newspapercut|postearnings|results.*presentation|presentation.*results|analyst|subsidiar|votingresults",
    re.IGNORECASE,
)
STATEMENT = re.compile(
    r"financial\s+(?:results?|statements?)|(?:un[- ]?audited|audited|quarterly)\s+(?:consolidated\s+|standalone\s+)?(?:results?|accounts)|"
    r"(?:adopted|approved|adoption of|approval of)\s+(?:the\s+)?(?:audited\s+)?annual accounts|"
    r"(?:quarter|q[1-4]).{0,25}earnings release|earnings release.{0,25}(?:quarter|q[1-4])|"
    r"(?:net (?:profit|loss)|profit after tax|revenue).{0,45}(?:for the quarter|in q[1-4])",
    re.IGNORECASE,
)
BOARD_FILENAME = re.compile(
    r"(?:board(?:ofdirectors)?(?:meeting)?|bod(?:meeting)?|bm)(?:outcome|proceeding)|(?:outcome|proceeding)(?:of)?(?:the)?(?:board(?:ofdirectors)?(?:meeting)?|bod|bm)",
    re.IGNORECASE,
)
FIN_FILENAME = re.compile(
    r"(?:un)?audited(?:consolidated|standalone)?(?:financial)?results?|financialresults?|"
    r"quarterlyresults?|results?(?:q[1-4]|quarter|[0-3]\d[01]\d\d{2}(?:\d{2})?)|q[1-4](?:fy\d{2,4})?(?:financial|earnings|results)|"
    r"financialstatements?(?:quarter|q[1-4])|(?:board|bod|bm)outcome(?:financial)?results?|"
    r"(?:quarterly|q[1-4])earningsrelease",
    re.IGNORECASE,
)
REFERENTIAL = re.compile(
    r"limited review report|audit qualifications?|declaration for audit reports|utilisation of funds|related party transactions?|publish audited results|^auditor.s report$",
    re.IGNORECASE,
)
ENCLOSED = re.compile(
    r"(?:enclos(?:ed|ing)|submit(?:ted|s|ting)?|attach(?:ed|ing)|announc(?:ed|ing)|declar(?:ed|ing))(?:.{0,80}?)(?:audited|financial|quarterly) (?:results|statements)",
    re.IGNORECASE,
)
GENERIC = re.compile(
    r"^(?:scan(?:ned)?|letter|signed|submission|intimation|disclosure|nse|bse|se|doc(?:ument)?|attachment|outcome|pressrelease|press|release|result|results)?\d*$",
    re.IGNORECASE,
)
STANDARD = re.compile(
    r"(?:^|\b)(?:financial results? updates?|financial results?|outcome of (?:the )?board meeting|board meeting outcome)(?:$|\s*[-,(])",
    re.IGNORECASE,
)


def file_info(url, day):
    path = urlsplit(url)
    if path.hostname not in {
        "archives.nseindia.com",
        "nsearchives.nseindia.in",
        "nsearchives.nseindia.com",
        "archives.nseindia.in",
        "www.nseindia.com",
        "www.nseindia.in",
    }:
        return None
    raw = unquote(PurePosixPath(path.path).name).casefold()
    if not raw.endswith((".pdf", ".zip", ".xlsx", ".xls", ".xml")):
        return None
    # Strip only the NSE upload prefix with the same DDMMYYYY and valid HHMMSS.
    clock = day.strftime("%d%m%Y")
    m = re.fullmatch(r"[^/]+?_" + clock + r"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d_(.+)", raw)
    name = m[1] if m else raw
    stem = re.sub(r"[^a-z0-9]", "", name.rsplit(".", 1)[0])
    return name, stem


def bulletin_row(r, receipt, stamp, url):
    return {
        "symbol": r["symbol"],
        "reported_company": r.get("sm_name") or "",
        "isin": r.get("sm_isin"),
        "announced_at": stamp.isoformat() + "+05:30",
        "description": r.get("desc") or "",
        "text": r.get("attchmntText") or "",
        "source_url": url,
        "sequence": r.get("seq_id"),
        "catalogue_url": receipt["url"],
        "catalogue_sha256": receipt["sha256"],
        "accession_sha256": accession(r),
    }


def evidence(r, day_refs, info, formal):
    desc, body = r["description"], r["text"]
    # Original link markup includes filenames. Treat the issuer's written body
    # and the filename as separate signals with separately recorded provenance.
    prose = re.split(r"<a\b", body, maxsplit=1, flags=re.IGNORECASE)[0]
    if NEGATIVE.search(desc + " " + prose) or FILENAME_NEGATIVE.search(info[1]):
        return None
    reporter = re.search(
        r"""\btitled\s+["'“]([A-Za-z][A-Za-z &.\-]{1,65}?)\s+(?:reports|announces|publishes|declares)""",
        prose,
        re.IGNORECASE,
    )
    if reporter and r.get("reported_company"):
        named = {
            n
            for n in re.findall(r"[a-z0-9]+", reporter[1].lower())
            if n not in {"the", "limited", "ltd", "company", "group", "inc"}
        }
        issuer = set(
            re.findall(r"[a-z0-9]+", r["reported_company"].lower() + " " + r["symbol"].lower())
        )
        if named and not issuer.intersection(named):
            return None
    name, stem = info
    linked = []
    if r.get("isin"):
        for ref, other in day_refs:
            if r["isin"] != ref["isin"] or r["accession_sha256"] == ref["accession_sha256"]:
                continue
            exact = r["source_url"] == ref["source_url"]
            same = name == other[0] and (
                not GENERIC.fullmatch(stem)
                or len({rr["source_url"] for rr, token in day_refs if token[0] == name}) == 1
            )
            if exact or same:
                linked.append(
                    {
                        "description": ref["description"],
                        "announced_at": ref["announced_at"],
                        "source_url": ref["source_url"],
                        "sequence": ref["sequence"],
                        "accession_sha256": ref["accession_sha256"],
                        "relation": "identical_url"
                        if exact
                        else "same_issuer_day_and_document_basename",
                    }
                )
    reasons = {x["relation"] for x in linked}
    if (RELEASE.search(prose) or STATEMENT.search(prose) or ANNUAL_ACCOUNTS.search(prose)) and (
        not REFERENTIAL.search(desc) or ENCLOSED.search(prose)
    ):
        reasons.add("issuer_body_mentions_results_or_approval")
    if FIN_FILENAME.search(stem):
        reasons.add("results_document_named_in_filename")
    if BOARD_FILENAME.search(stem) and (formal or day_refs or reasons):
        reasons.add("board_outcome_filename_on_results_day_or_with_explicit_results")
    date_tag = date.fromisoformat(r["announced_at"][:10])
    dated_outcome = stem in {
        "outcome" + date_tag.strftime(fmt) for fmt in ("%d%m%Y", "%d%m%y", "%Y%m%d")
    }
    completed_meeting = re.search(
        r"\bboard of directors at (?:its |the |their )?meeting held\b", prose, re.IGNORECASE
    )
    if (
        stem == "outcome" and formal or dated_outcome and (formal or day_refs or completed_meeting)
    ) and desc.strip().lower() in {
        "general updates",
        "general update",
        "updates",
        "dividend",
        "record date",
    }:
        reasons.add(
            "dated_board_outcome_at_reported_meeting"
            if dated_outcome
            else "outcome_packet_on_same_issuer_structured_results_day"
        )
    if not reasons:
        return None
    return {
        "selection_reasons": sorted(reasons),
        "same_day_source_links": sorted(linked, key=lambda x: (x["announced_at"], x["source_url"])),
        "verified_earnings_reference_count_same_day": len(day_refs),
        "same_day_structured_filing_periods": sorted(formal),
    }


def project(rows, receipt, symbols, isins, formal):
    refs = defaultdict(list)
    possible = []
    scoped = Counter()
    for item in rows:
        symbol, isin = item.get("symbol"), item.get("sm_isin")
        if symbol not in symbols and isin not in isins:
            continue
        scoped["universe_announcements"] += 1
        url = archive_attachment(
            {"text": item.get("attchmntText") or "", "source_url": item.get("attchmntFile")}
        )
        if not url or not str(url).startswith("https://"):
            continue
        stamp = published_at(item.get("exchdisstime")) or published_at(item.get("an_dt"))
        if not stamp or stamp.date() > END or not (info := file_info(url, stamp.date())):
            continue
        scoped["dated_official_attachment"] += 1
        row = bulletin_row(item, receipt, stamp, url)
        key = (symbol, isin, stamp.date())
        desc, prose = (
            row["description"],
            re.split(r"<a\b", row["text"], maxsplit=1, flags=re.IGNORECASE)[0],
        )
        if STANDARD.search(desc):
            if (
                STATEMENT.search(desc + " " + prose) or RELEASE.search(prose)
            ) and not NEGATIVE.search(desc + " " + prose):
                refs[key].append((row, info))
            continue
        possible.append((row, key, info))
    out = []
    for row, key, info in possible:
        if proof := evidence(row, refs.get(key, []), info, formal.get(key, set())):
            out.append({**row, "release_language": "possible_original_financial_packet", **proof})
    return out, scoped


def main():
    market = ROOT / "notice-market-manifest.json"
    sources = ROOT / "notice-market-sources.json"
    m = json.loads(market.read_bytes())
    expected = {b["url"]: b["total"] for b in m["blocks"]}
    if len({b["start"][:7] for b in m["blocks"]}) != m["expected_months"] or len(expected) != len(
        m["blocks"]
    ):
        raise ValueError("Missing monthly all-market coverage")
    alias = ROOT / "dated-alias-attachment-plan.json"
    hist = members()
    isins = {r["isin"] for r in hist}
    symbols = {r["symbol"] for r in hist}
    symbols.update(r["symbol"] for r in json.loads(alias.read_bytes()) if r["isin"] in isins)
    plan = ROOT / "attachment-plan.json"
    formal = defaultdict(set)
    for source in (plan, alias):
        for r in json.loads(source.read_bytes()):
            if r["isin"] in isins and r.get("filed_at"):
                formal[(r["symbol"], r["isin"], date.fromisoformat(r["filed_at"][:10]))].add(
                    r["period_end"]
                )
    prior_files = [
        "notice-market-rows.json",
        "notice-language-rows.json",
        "notice-alias-rows.json",
        "notice-case-rows.json",
        "notice-language-identical-rows.json",
    ]
    prior = set()
    prior_sources = {}
    for name in prior_files:
        raw = (ROOT / name).read_bytes()
        prior_sources[name] = sha(raw)
        for r in json.loads(raw):
            prior.add((r["symbol"], r.get("isin"), r["announced_at"], archive_attachment(r)))
    chosen = []
    totals = Counter()
    seen = set()
    for src in json.loads(sources.read_bytes()):
        if src["kind"] != "period":
            continue
        if src["url"] in seen or src["url"] not in expected:
            raise ValueError("Duplicate/unrecognised monthly NSE source")
        seen.add(src["url"])
        _, raw = read_period(src)
        if len(raw) != expected[src["url"]]:
            raise ValueError("All-market source count differs from the census")
        rows, counts = project(raw, src, symbols, isins, formal)
        chosen.extend(rows)
        totals.update(counts)
        totals["raw_market_announcements"] += len(raw)
    if seen != set(expected):
        raise ValueError("Did not examine every official source month")
    chosen.sort(key=lambda r: (r["symbol"], r["announced_at"], r["accession_sha256"]))
    for r in chosen:
        r["already_indexed_by_original_or_supplemental_bulletin"] = (
            r["symbol"],
            r.get("isin"),
            r["announced_at"],
            r["source_url"],
        ) in prior
    rows_file = ROOT / "notice-extra-category-rows.json"
    write_json(rows_file, chosen)
    manifest = {
        "market_manifest_sha256": sha(market.read_bytes()),
        "market_sources_sha256": sha(sources.read_bytes()),
        "membership_sha256": sha((REPO / "data/universes/nifty200_membership.csv").read_bytes()),
        "alias_plan_sha256": sha(alias.read_bytes()),
        "structured_plan_sha256": sha(plan.read_bytes()),
        "previous_selection_sources": prior_sources,
        "new_relative_to_previous_selections": sum(
            not r["already_indexed_by_original_or_supplemental_bulletin"] for r in chosen
        ),
        "source_program_sha256": sha(
            (REPO / "src/thesistally/fundamental_notice_categories.py").read_bytes()
        ),
        "period_blocks": len(seen),
        "segments": m["segments"],
        "counts": totals,
        "selected": len(chosen),
        "unique_urls": len({r["source_url"] for r in chosen}),
        "rows_sha256": sha(rows_file.read_bytes()),
        "subjects": Counter(r["description"] for r in chosen),
        "reasons": Counter(reason for r in chosen for reason in r["selection_reasons"]),
        "limitations": [
            "A matching basename is candidate evidence only. Different uploaded bytes may contain different contents.",
            "A dated NSE attachment and an issuer's results language can identify a possible earlier packet, but facts, basis, period and clock require original filing review.",
            "A generic document name alone, a future meeting, analyst event, annual report or operational update cannot establish quarterly publication. The public source must establish the reporting issuer; an issuer can file a subsidiary’s results. An earnings headline explicitly naming another reporting company is excluded.",
            "Known announcement subjects for exchange results and board outcomes remain on their original acquisition lane; releases lacking a public attachment or recoverable official attachment URL are outside this supplement.",
        ],
    }
    write_json(ROOT / "notice-extra-category-manifest.json", manifest)


if __name__ == "__main__":
    main()
