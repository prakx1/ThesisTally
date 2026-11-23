"""Reconcile a frozen Strategy D source edition without using implementation-side selectors."""

import bisect
import csv
import gzip
import hashlib
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

F = Path(__file__).resolve().parents[1]
NSE = F / "nse"
ROOT = F.parents[1]
COVERAGE = "4e3a1e357a22fa22de3bd097c0a4900b27a178b63825fd79f9eedef5e3e4918c"
CONDITIONAL = "c09a0fc6f2ff049b4ea9eabe7a0c865a2d5de201d4030d41f51b2b2d8979ff16"
FIELDS = ("eps", "net_profit", "operating_profit", "revenue")
INDIA = ZoneInfo("Asia/Kolkata")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verified(path, expected, packed=False):
    raw = path.read_bytes()
    if packed:
        raw = gzip.decompress(raw)
    assert sha(raw) == expected, path
    return raw


def document(row):
    notice = row["first_notice"]
    digest = notice["attachment_sha256"]
    return verified(NSE / "notice-objects" / digest[:2] / (digest + ".gz"), digest, True)


def modern_example(row):
    pdf = document(row)
    page = (
        subprocess.run(
            ["pdftotext", "-q", "-layout", "-f", "16", "-l", "16", "-", "-"],
            input=pdf,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\f")[0]
    )
    cleaned = re.sub(r"\s+", "", page.lower())
    for phrase in (
        "muthootfinancelimited",
        "unauditedconsolidatedfinancialresults",
        "june30,2026",
        "(rs.inmillions)",
    ):
        assert phrase in cleaned
    amount = re.compile(r"\d[\d,]*[.]\d\d")
    wanted = {
        "RevenueFromOperations": "totalrevenue fro m operations",
        "Expenses": "totalexpenses(iv)",
        "FinanceCosts": "financecosts",
        "ProfitLossForPeriod": "profitfortheperiod(v•vl)",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "basic(rs.)",
    }
    field = {}
    for tag, caption in wanted.items():
        lines = [
            line
            for line in page.splitlines()
            if caption.replace(" ", "") in re.sub(r"\s+", "", line.lower())
        ]
        assert len(lines) == 1, (caption, lines)
        first = amount.search(lines[0])
        assert first and len(amount.findall(lines[0])) >= 2
        field[tag] = Decimal(first.group().replace(",", ""))
    source = row["structured_sha256"]
    structured = ET.fromstring(
        verified(NSE / "objects" / source[:2] / (source + ".gz"), source, True)
    )
    period = next(e for e in structured if e.tag.endswith("context") and e.get("id") == "OneD")
    tags = {e.tag.split("}")[-1]: e.text for e in period.iter()}
    assert (tags["startDate"], tags["endDate"]) == (
        "2026-04-01",
        "2026-06-30",
    ) and "explicitMember" not in tags
    for tag, value in field.items():
        nodes = [
            e for e in structured if e.tag.split("}")[-1] == tag and e.get("contextRef") == "OneD"
        ]
        unit = "INRPerShare" if tag.startswith("Basic") else "INR"
        assert len(nodes) == 1 and nodes[0].get("unitRef") == unit
        assert Decimal(nodes[0].text) == value * (1 if unit == "INRPerShare" else 10**6)
    results = {
        "eps": field["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations"],
        "net_profit": field["ProfitLossForPeriod"] * 10**6,
        "operating_profit": (
            field["RevenueFromOperations"] - field["Expenses"] + field["FinanceCosts"]
        )
        * 10**6,
        "revenue": field["RevenueFromOperations"] * 10**6,
    }
    assert all(Decimal(str(row["structured_values"][k])) == v for k, v in results.items())
    return {
        "issuer_quarter_basis": row["id"],
        "original_notice_sha256": sha(pdf),
        "original_page": 16,
        "page_native_text_sha256": sha(page.encode()),
        "first_notice_at": row["first_notice"]["announced_at"],
        "first_source_url": row["first_notice"]["source_url"],
        "later_structured_url": row["structured_url"],
        "later_structured_sha256": source,
        "filed_at": row["filed_at"],
        "digitised_original_and_later_independent_agreement_inr": {
            k: str(v) for k, v in results.items()
        },
        "immutable_snapshot_disposition": row["gap"],
    }


def run():
    cov = json.loads(verified(F / "coverage-history" / (COVERAGE + ".json"), COVERAGE))
    bound_bytes = verified(
        F / "coverage-history/conditional-ceilings" / (CONDITIONAL + ".json"), CONDITIONAL
    )
    prior = json.loads(bound_bytes)
    m = cov["filing_sources"]["nse"]
    checkpoint = NSE / "checkpoints" / m["sha256"] / m["source_census_sha256"]
    ix = json.loads(verified(checkpoint / "index.json", m["checkpoint_index_sha256"]))
    src = json.loads(
        verified(checkpoint / "filings-receipt.json.gz", ix["filings_receipt_sha256"], True)
    )
    assert src["sha256"] == m["sha256"]
    ledger = verified(NSE / "vintages" / (src["sha256"] + ".jsonl.gz"), src["sha256"], True)
    data = [json.loads(line) for line in ledger.splitlines()]
    qh = cov["products"]["quarter-coverage.csv"]
    cells = csv.DictReader(
        io.StringIO(verified(F / "coverage-history/objects" / (qh + ".gz"), qh, True).decode())
    )
    arc = ROOT.parent / "ThesisTally" / "data/baseline/preflight.json"
    calendar = json.loads(verified(arc, cov["preflight_sha256"]))["sessions"]
    periods, covered, cutoff = defaultdict(set), defaultdict(set), {}
    for year in cov["windows"]:
        last = calendar[bisect.bisect_right(calendar, year + "-12-31") - 1]
        cutoff[year] = datetime.combine(
            datetime.fromisoformat(last).date(), time(15, 30), tzinfo=INDIA
        )
    for r in cells:
        k = r["isin"], r["period_end"]
        year = str(int(k[1][:4]) + int(k[1][5:7] == "12"))
        periods[year].add(k)
        if r["covered_by_window_end"] == "True":
            covered[year].add(k)
    plans = set()
    for name, expected in [
        ("attachment-plan.json", src["attachments_plan_sha256"]),
        (
            "dated-alias-attachment-plan.json",
            src["dated_alias_corpus_sha256"]["dated-alias-attachment-plan.json"],
        ),
    ]:
        plans.update(r["id"] for r in json.loads(verified(NSE / name, expected)))
    packages = {
        p.name.removesuffix(".json.gz")
        for folder in ("notice-text", "notice-media")
        for p in (NSE / folder).glob("*.json.gz")
    }
    html, html_urls, modern, modern_ids = set(), set(), set(), set()
    q = src["field_reviews"]
    quarantined = {
        (r["isin"], r["period_end"])
        for r in json.loads(verified(NSE / q["file"], q["sha256"], True))
    }
    for r in data:
        first = r.get("first_notice") or {}
        url = first.get("source_url")
        scores = r.get("structured_values") or {}
        key = r["isin"], r["period_end"]
        if (
            r["id"] not in plans
            or not url
            or sha(url.encode()) not in packages
            or not r.get("structured_sha256")
        ):
            continue
        if r["filing_status"] not in {"original", "uncertified"} or not any(
            v is not None for v in scores.values()
        ):
            continue
        if (r.get("structured_url") or "").lower().endswith(".html"):
            if not (r["first_reported"] and all(r[k] is not None for k in FIELDS)):
                html.add(key)
                html_urls.add(url)
            continue
        year = str(int(key[1][:4]) + int(key[1][5:7] == "12"))
        if (
            year not in cutoff
            or key not in periods[year]
            or r.get("gap") != "first_release_facts_unreconciled"
            or not all(scores.get(k) is not None for k in FIELDS)
        ):
            continue
        original_at, later_at = (
            datetime.fromisoformat(first["announced_at"]),
            datetime.fromisoformat(r["filed_at"]),
        )
        if url != r.get("structured_url") and original_at < later_at <= cutoff[year]:
            modern.add(key)
            modern_ids.add(r["id"])
    assert len(html_urls) == prior["legacy_source_packets"]
    years = {}
    for y, claim in cov["windows"].items():
        all_keys, known = periods[y], covered[y]
        assert (len(all_keys), len(known)) == (claim["eligible"], claim["covered"])
        missing = all_keys - known
        h, m = missing & (html | quarantined), missing & modern
        assert len(known) + len(h) == prior["windows"][y]["fixed_nse_work_ceiling"]
        years[y] = {
            "window_cutoff_ist": cutoff[y].isoformat(),
            "eligible": len(all_keys),
            "verified": len(known),
            "remaining_current_queues_unique_pairs": len(h),
            "optimistic_known_plus_current_queues": len(known) + len(h),
            "modern_pairs_with_earlier_original_and_later_four_field_table_by_cutoff": len(m),
            "optimistic_known_plus_modern_pairs": len(known) + len(m),
            "overlap_of_uncovered_sets": len(h & m),
            "optimistic_known_plus_both_sets": len(known) + len(h | m),
            "minimum_complete_pairs": (len(all_keys) * 4 + 4) // 5,
            "sorted_modern_pair_list_sha256": sha(
                json.dumps(sorted(m), separators=(",", ":")).encode()
            ),
        }
    example = next(
        r
        for r in data
        if r["id"] == "integrated/MUTHOOTFIN/INE414G01012/2026-06-30/consolidated/181533"
    )
    return {
        "coverage_sha256": COVERAGE,
        "nse_ledger_sha256": src["sha256"],
        "legacy_bound_sha256": CONDITIONAL,
        "legacy_worklist_urls_including_warmup": len(html_urls),
        "modern_candidate_accessions_before_excluding_already_covered_pairs": len(modern_ids),
        "modern_candidate_definition": "Each unique, still-uncovered member ISIN and eligible quarter has at least one archived earlier original notice and a distinct accessible non-HTML structured filing whose four fields parsed. Both timestamps precede the last trading close of the window. The original table must still prove every original field and its basis. The signed source census separately verifies the underlying physical files.",
        "limitation": "None of these alternatives is observed coverage. Their sum must remove overlapping issuer-quarters. They are upper bounds only on the explicitly named finite sets of candidates. Other archived originals may provide additional information. The BSE catalogue was not enumerable.",
        "windows": years,
        "one_modern_original_witness": modern_example(example),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
