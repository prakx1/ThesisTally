"""Reproduce optimistic Strategy D ceilings from an immutable snapshot of completed source jobs."""

import argparse
import csv
import gzip
import io
import json
from collections import defaultdict
from datetime import datetime

from check_nominated_plan import LEGACY, PLAN
from check_post_download_queues import COVERAGE, NSE, F, sha, verified


def canon(data):
    return (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def progress_receipt(run, snapshot):
    checkpoint = F / "review/progress-snapshots"
    if snapshot:
        return json.loads(verified(checkpoint / (snapshot + ".json.gz"), snapshot, True)), snapshot
    files = sorted(run.glob("*/*.json"))
    selected = {f.stem: sha(f.read_bytes()) for f in files}
    assert len(files) == len(selected)
    body = canon({"run_sha256": run.name, "markers_sha256": selected})
    h = sha(body)
    checkpoint.mkdir(exist_ok=True)
    p = checkpoint / (h + ".json.gz")
    if p.exists():
        verified(p, h, True)
    else:
        p.write_bytes(gzip.compress(body, mtime=0))
    return json.loads(body), h


def run(snapshot=None):
    scope = json.loads((NSE / "terminal-xbrl-scope.json").read_bytes())
    plan = json.loads(verified(NSE / "xbrl-optical-plans" / (PLAN + ".json.gz"), PLAN, True))
    assert (
        scope["cohorts"]["modern-statement-pages"] == PLAN
        and scope["source_edition"] == plan["source_edition"]
    )
    candidates = [
        p
        for p in (NSE / "modern-optical-attempts").glob("*/run.json")
        if json.loads(p.read_bytes())["plan"] == PLAN
        and json.loads(p.read_bytes())["evaluator"] == scope["evaluator"]
    ]
    assert len(candidates) == 1
    runfile = candidates[0]
    run = runfile.parent
    assert sha(runfile.read_bytes()) == run.name
    steps, snap = progress_receipt(run, snapshot)
    links = steps["markers_sha256"]
    assert steps["run_sha256"] == run.name
    cov = json.loads(verified(F / "coverage-history" / (COVERAGE + ".json"), COVERAGE))
    previously = json.loads(
        verified(
            F / "review/post-download-worklists-and-modern-sources.json",
            "e84a213d48261390a42d39898cd2b8f2aba700de789276b73e8b2fbe8ef58e7a",
        )
    )
    baseline = json.loads(
        verified(
            F / "review/nominated-plan-9062be34.json",
            "64fb6f5801b577b388b085dfd02fd8c0723d67102f3a9356202453268e7a2e3b",
        )
    )
    legacy = json.loads(
        verified(NSE / "legacy-optical-plans" / (LEGACY + ".json.gz"), LEGACY, True)
    )
    absorb = {(r["isin"], r["period_end"]) for _, _, _, rows in legacy["tasks"] for r in rows}
    h = "ed95ddb10a6249f16d265d014d5e8f2fb3aa17401d560f1ccba634304f7aff18"
    absorb.update(
        (r["isin"], r["period_end"])
        for r in json.loads(verified(NSE / "field-reviews" / (h + ".json.gz"), h, True))
    )
    for path in (
        "formal-original-clock-document-decisions.json",
        "additional-earlier-clock-confirmations.json",
    ):
        absorb.update(
            (r["isin"], r["period_end"])
            for r in json.loads((F / "review" / path).read_bytes())["items"]
            if r["timing_status"] == "proven_original"
        )
    pairs, known = defaultdict(set), defaultdict(set)
    quarter_hash = cov["products"]["quarter-coverage.csv"]
    for r in csv.DictReader(
        io.StringIO(
            verified(
                F / "coverage-history/objects" / (quarter_hash + ".gz"), quarter_hash, True
            ).decode()
        )
    ):
        k = r["isin"], r["period_end"]
        y = str(int(k[1][:4]) + int(k[1][5:7] == "12"))
        pairs[y].add(k)
        if r["covered_by_window_end"] == "True":
            known[y].add(k)
    scheduled, surviving, referenced, positives = {}, {}, set(), set()
    closed_fieldless, trusted_rows, unknown_page_cases = 0, 0, 0
    empty_by_issuer_quarter = defaultdict(list)
    for j in plan["jobs"]:
        key = sha(canon(j))
        n = sum(len(l) for l in j["selected_pages"].values())
        out = {}
        if key in links:
            cfile = run / key[:2] / (key + ".json")
            finished = json.loads(verified(cfile, links[key]))
            referenced.add(key)
            assert finished["job"] == key and finished["nominated_pages"] == n
            expected = {c["filing"]["id"]: c for c in j["cases"]}
            assert {r["id"] for r in finished["records"]} == (
                set(expected) if n else set()
            ) and len(finished["records"]) == (len(expected) if n else 0)
            for r in finished["records"]:
                info = expected[r["id"]]
                f = info["filing"]
                h = r["receipt_sha256"]
                path = (NSE / r["file"]).resolve(strict=True)
                assert (
                    path.is_relative_to((NSE / "original-xbrl-optics").resolve()) and h in path.name
                )
                raw = json.loads(verified(path, h, True))
                ident = raw["identity"]
                sc = raw["selection"]
                assert (
                    sc["pages"] == j["selected_pages"]
                    and sc["mode"] == "nominated"
                    and sc["selection_version"] == plan["selection_version"]
                )
                assert (
                    sc["nomination_sha256"] == sha(canon(j["selected_pages"]))
                    and raw["package_sha256"] == j["package_sha256"]
                )
                assert (
                    ident["original_url"],
                    ident["original_sha256"],
                    ident["structured_sha256"],
                    ident["release_at"],
                    ident["evaluator"],
                ) == (
                    j["url"],
                    j["original_sha256"],
                    info["structured_sha256"],
                    info["release_at"],
                    scope["evaluator"],
                )
                assert (ident["id"], ident["isin"], ident["period_end"], ident["basis"]) == (
                    f["id"],
                    f["isin"],
                    f["period_end"],
                    f["basis"],
                )
                assert (
                    raw["values"] == r["values"]
                    and raw["unreadable_pages"] == r["unreadable_pages"]
                )
                assert (
                    raw["conflicts"] == r["conflicts"]
                    and raw["nonmatching_fields"] == r["nonmatching_fields"]
                )
                physical = {(p["pdf_sha256"], p["page"]) for p in raw["pages"]}
                scheduled_pages = {(s, pg) for s, ids in j["selected_pages"].items() for pg in ids}
                assert physical <= scheduled_pages and len(physical) == len(raw["pages"])
                other = {
                    (e["pdf_sha256"], e["page"]) for e in raw["unreadable_pages"] if "page" in e
                }
                assert physical | other == scheduled_pages
                trusted_rows += 1
                successful = any(v is not None for v in raw["values"].values())
                if successful:
                    positives.add(h)
                unknown_page_cases += bool(raw["unreadable_pages"])
                # Retain even one positive field, any unreadable page or any observed eligible unmatched source value.
                # The latter might be independently supported by another pending receipt or printed exception.
                conflict = bool(raw["conflicts"] or raw["nonmatching_fields"])
                out[f["id"]] = successful or bool(raw["unreadable_pages"]) or conflict
                if not out[f["id"]]:
                    closed_fieldless += 1
                    empty_by_issuer_quarter[f["isin"], f["period_end"]].append(
                        {
                            "id": f["id"],
                            "symbol": f["symbol"],
                            "original_url": j["url"],
                            "release_at": info["release_at"],
                            "job": key,
                            "receipt_sha256": h,
                        }
                    )
        if n:
            for case in j["cases"]:
                f = case["filing"]
                pair = f["isin"], f["period_end"]
                stamp = datetime.fromisoformat(case["release_at"])
                scheduled[pair] = min(stamp, scheduled.get(pair, stamp))
                if key not in links or out[f["id"]]:
                    surviving[pair] = min(stamp, surviving.get(pair, stamp))
    assert set(links) == referenced
    results = {}
    for y, w in baseline["windows"].items():
        eligible = pairs[y]
        before = known[y]
        dt = datetime.fromisoformat(previously["windows"][y]["window_cutoff_ist"])
        initially_possible = {k for k in eligible if k in scheduled and scheduled[k] <= dt}
        still_possible = {k for k in eligible if k in surviving and surviving[k] <= dt}
        fixed = before | (eligible & absorb)
        upper = fixed | still_possible
        original = fixed | initially_possible
        assert (
            len(original)
            == w[
                "optimistic_total_across_named_queues_even_if_partial_modern_xbrl_can_supply_all_fields"
            ]
        )
        demonstrate = len(original) >= w["needed"] > len(upper)
        minimal = sorted(original - upper)[: len(original) - w["needed"] + 1] if demonstrate else []
        results[y] = {
            "eligible": len(eligible),
            "covered_in_reference_edition": len(before),
            "optimistic_before_processing": len(original),
            "issuer_quarters_no_longer_recoverable_in_these_queues": len(original - upper),
            "optimistic_now": len(upper),
            "minimal_case_receipts_proving_newly_impossible_window": {
                "/".join(k): sorted(empty_by_issuer_quarter[k], key=lambda r: r["id"])
                for k in minimal
            },
            "required": w["needed"],
            "would_be_possible_if_every_remaining_case_recovered_all_fields": len(upper)
            >= w["needed"],
        }
    return {
        "source_edition": plan["source_edition"],
        "plan": PLAN,
        "evaluator": scope["evaluator"],
        "run": run.name,
        "completed_job_snapshot_sha256": snap,
        "completed_source_jobs": len(links),
        "total_jobs": len(plan["jobs"]),
        "matched_nominated_accessions": trusted_rows,
        "completed_nominated_accessions_with_no_fields_contradictions_or_unreadable_pages": closed_fieldless,
        "accessions_with_an_unreadable_source_retained": unknown_page_cases,
        "positive_source_receipts": len(positives),
        "limitation": "Fixed source plans and recorded evaluator only. Every pending accession or other basis, any positive field, any internally inconsistent evidence, any failed physical read, all initially covered issuer-quarters, and every eligible legacy/quarantined or independently traced earlier original remains eligible in this deliberately optimistic count. A pair is removed only after all of its applicable nominated accessions have physically complete, fully matched receipts with no observed fields or conflicts. Additional collectors, archives, improved recognition or changed source rules are outside this bound.",
        "windows": results,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot")
    args = p.parse_args()
    print(json.dumps(run(args.snapshot), indent=2))
