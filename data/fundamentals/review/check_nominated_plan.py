"""Audit the dated first-packet inventory and finite opportunity of a signed page plan."""

import csv
import gzip
import io
import json
from collections import defaultdict
from datetime import datetime

from check_post_download_queues import COVERAGE, NSE, F, sha, verified

PLAN = "9062be34a9411ff83cd431d5fb58dc2e8471b72b07cc11a97fe64d9ea07ba78d"
LEGACY = "306980ad1dc390e1f5783a82d733bceb2284161d8072bcdf1f550342d624f584"


def run():
    plan = json.loads(verified(NSE / "xbrl-optical-plans" / (PLAN + ".json.gz"), PLAN, True))
    legacy = json.loads(
        verified(NSE / "legacy-optical-plans" / (LEGACY + ".json.gz"), LEGACY, True)
    )
    cov = json.loads(verified(F / "coverage-history" / (COVERAGE + ".json"), COVERAGE))
    expected = cov["filing_sources"]["nse"]["sha256"]
    assert plan["source_edition"] == legacy["ledger"] == expected
    ledger = [
        json.loads(line)
        for line in verified(
            NSE / "vintages" / (expected + ".jsonl.gz"), expected, True
        ).splitlines()
    ]
    by_id = defaultdict(list)
    for r in ledger:
        by_id[r["id"]].append(r)
    prior = json.loads((F / "review/post-download-worklists-and-modern-sources.json").read_text())
    assert prior["coverage_sha256"] == COVERAGE
    legacy_pairs = {(f["isin"], f["period_end"]) for _, _, _, rows in legacy["tasks"] for f in rows}
    h = "ed95ddb10a6249f16d265d014d5e8f2fb3aa17401d560f1ccba634304f7aff18"
    field_pairs = {
        (r["isin"], r["period_end"])
        for r in json.loads(verified(NSE / "field-reviews" / (h + ".json.gz"), h, True))
    }
    early_sources = [
        F / "review/additional-earlier-clock-confirmations.json",
        F / "review/formal-original-clock-document-decisions.json",
    ]
    controls = {
        (r["isin"], r["period_end"])
        for p in early_sources
        for r in json.loads(p.read_bytes())["items"]
        if r["timing_status"] == "proven_original"
    }
    assert len(controls) == 9
    unknown_without_nomination = set()
    all_candidates, candidates_with_pages, accessions, nominated, physical = (
        {},
        {},
        set(),
        set(),
        set(),
    )
    nom_count, page_count = 0, 0
    page_index = {}
    for job in plan["jobs"]:
        path = NSE / job["package_path"]
        body = json.loads(gzip.decompress(verified(path, job["package_sha256"])))
        assert body["source_url"] == job["url"] and body["sha256"] == job["original_sha256"]
        members = {
            d["pdf_sha256"]: len(d["pages"]) - int(bool(d["pages"]) and not d["pages"][-1].strip())
            for d in body["pdfs"]
        }
        assert set(job["selected_pages"]) == set(members)
        any_nominated = any(job["selected_pages"].values())
        if not any_nominated:
            unknown_without_nomination.add(job["url"])
        for key, total in members.items():
            ids = job["selected_pages"][key]
            assert ids == sorted(set(ids)) and all(1 <= p <= total for p in ids)
            page_index[job["url"], key] = set(ids)
            nom_count += len(ids)
            page_count += total
            nominated.update((key, i) for i in ids)
            physical.update((key, i) for i in range(1, total + 1))
        for case in job["cases"]:
            f = case["filing"]
            ident = f["id"]
            assert ident not in accessions
            accessions.add(ident)
            matches = [
                r
                for r in by_id[ident]
                if r["filing_status"] in {"original", "uncertified"}
                and r.get("structured_sha256") == case["structured_sha256"]
                and r.get("structured_url") == f["source_url"]
                and (r.get("first_notice") or {}).get("source_url") == job["url"]
                and r["first_notice"]["announced_at"] == case["release_at"]
            ]
            assert len(matches) == 1, ident
            row = matches[0]
            assert (
                row["isin"] == f["isin"]
                and row["basis"] == f["basis"]
                and row["period_end"] == f["period_end"]
            )
            assert all(
                row["structured_values"][k] == f[k]
                for k in ("eps", "net_profit", "operating_profit", "revenue")
            )
            k = f["isin"], f["period_end"]
            instant = datetime.fromisoformat(case["release_at"])
            all_candidates[k] = min(instant, all_candidates.get(k, instant))
            if any_nominated:
                candidates_with_pages[k] = min(instant, candidates_with_pages.get(k, instant))
    saved = plan["counts"]
    assert (
        len(nominated),
        len(physical),
        nom_count,
        page_count,
        len(accessions),
        len(unknown_without_nomination),
    ) == (
        saved["distinct_nominated_pages"],
        saved["distinct_physical_pages"],
        saved["page_reads_nominated_across_jobs"],
        saved["physical_pages_across_jobs"],
        saved["eligible_accessions"],
        saved["zero_nomination_packets"],
    )
    controls_path = NSE / "modern-optical-plan-summaries" / (PLAN + "-known-pages.json")
    checks = json.loads(controls_path.read_bytes())
    assert checks["formal_clock_decisions_sha256"] == sha(early_sources[-1].read_bytes())
    pages = {}
    for c in checks["pages"]:
        ids = page_index[c["source_url"], c["source_pdf_sha256"]]
        assert (c["page"] in ids) == c["nominated"]
        pages[f"{c['issuer']} p{c['page']}"] = c["nominated"]
    quarter_hash = cov["products"]["quarter-coverage.csv"]
    all_pairs, known = defaultdict(set), defaultdict(set)
    for r in csv.DictReader(
        io.StringIO(
            verified(
                F / "coverage-history/objects" / (quarter_hash + ".gz"), quarter_hash, True
            ).decode()
        )
    ):
        k = r["isin"], r["period_end"]
        y = str(int(k[1][:4]) + int(k[1][5:7] == "12"))
        all_pairs[y].add(k)
        if r["covered_by_window_end"] == "True":
            known[y].add(k)
    result = {}
    for y, window in prior["windows"].items():
        cutoff = datetime.fromisoformat(window["window_cutoff_ist"])
        covered = known[y]
        eligible = all_pairs[y]
        modern = {k for k in eligible if k in all_candidates and all_candidates[k] <= cutoff}
        selected = {
            k for k in eligible if k in candidates_with_pages and candidates_with_pages[k] <= cutoff
        }
        optimistic = covered | eligible & (legacy_pairs | field_pairs | controls | selected)
        result[y] = {
            "eligible": len(eligible),
            "observed": len(covered),
            "needed": window["minimum_complete_pairs"],
            "uncovered_with_any_modern_facts_and_dated_original": len(modern - covered),
            "uncovered_with_any_nominated_modern_page_and_dated_original": len(selected - covered),
            "optimistic_total_across_named_queues_even_if_partial_modern_xbrl_can_supply_all_fields": len(
                optimistic
            ),
            "headroom_vs_required": len(optimistic) - window["minimum_complete_pairs"],
        }
    return {
        "coverage": COVERAGE,
        "numerical_ledger": expected,
        "modern_plan": PLAN,
        "legacy_plan": LEGACY,
        "modern_plan_counts": {
            "jobs": len(plan["jobs"]),
            "accessions": len(accessions),
            "unique_physical_pages": len(physical),
            "unique_nominated_pages": len(nominated),
            "source_page_references": page_count,
            "nominated_page_references": nom_count,
            "packets_with_zero_nominations": len(unknown_without_nomination),
        },
        "independently_located_statement_pages_nominated": pages,
        "assumptions": "Every uncovered eligible ISIN-quarter with any nominated modern page, any legacy or field-quarantine work, or any of the nine separately audited originals is generously allowed to become fully covered. A modern release counts if its original was disseminated by the window close. Give full credit even where later quarterly XBRL with only one available field, or after the window. The bound applies only to these dated worklists. It does not show coverage, freeze the study, establish that omitted pages contain no facts or limit other possible methods.",
        "windows": result,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
