"""Recheck every signed, confirmed earlier earnings packet against its quarterly XBRLs."""

import argparse
import gzip
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from .discovery_registry import digest, encoded, write_once
from .fundamental_facts import FIELDS
from .fundamental_media_recovery import package_path
from .fundamental_nse import ROOT, load_plans, pack
from .fundamental_review_optics import assess, evaluator, worker_tools


def select(root):
    review = root.parent / "review"
    a = root / "notice-formal-review-manifest.json"
    b = review / "additional-earlier-clock-confirmations.json"
    formal = json.loads(a.read_bytes())["reviewed"]
    extra = json.loads(b.read_bytes())["items"]
    marked = [
        {**x, "period_end": x["declared_period_end"], "sha256": x["attachment_sha256"]}
        for x in formal
        if x["reviewed_status"] == "proven_original"
    ]
    marked.extend(x for x in extra if x["timing_status"] == "proven_original")
    edition = json.loads((root / "filings-receipt.json").read_bytes())["sha256"]
    raw = gzip.decompress((root / "vintages" / (edition + ".jsonl.gz")).read_bytes())
    if digest(raw) != edition:
        raise ValueError("Reference source edition changed")
    filings = {f["id"]: f for f in load_plans(root)}
    keys = {(m["symbol"], m["period_end"]) for m in marked}
    by_key = defaultdict(list)
    for line in raw.splitlines():
        r = json.loads(line)
        if (
            (r["symbol"], r["period_end"]) in keys
            and r["filing_status"] in {"original", "uncertified"}
            and r["id"] in filings
            and (r.get("structured_url") or "").lower().endswith(".xml")
            and r.get("structured_sha256")
            and all(r["structured_values"].get(k) is not None for k in FIELDS)
        ):
            by_key[r["symbol"], r["period_end"]].append(r)
    groups = defaultdict(dict)
    audit = []
    for notice in marked:
        url, stamp = notice["source_url"], notice["announced_at"]
        subject = (notice["symbol"], notice["period_end"])
        p = package_path(root, url)
        matches = []
        for r in by_key[subject]:
            first = r.get("first_notice") or {}
            if (first.get("source_url"), first.get("announced_at")) != (url, stamp):
                continue
            if not p.exists():
                raise ValueError("Signed original packet is no longer present")
            pack_record = json.loads(gzip.decompress(p.read_bytes()))
            if pack_record["sha256"] != notice["sha256"] or pack_record["source_url"] != url:
                raise ValueError("Signed earlier-release source identity differs")
            f = filings[r["id"]]
            if f["source_url"] != r["structured_url"]:
                raise ValueError("Reference filing URL changed")
            case = {
                "filing": {**f, **r["structured_values"], "tags": r["structured_tags"]},
                "structured_sha256": r["structured_sha256"],
                "release_at": stamp,
            }
            groups[url][r["id"]] = case
            matches.append(r["id"])
        audit.append(
            {
                "symbol": subject[0],
                "period_end": subject[1],
                "url": url,
                "sha256": notice["sha256"],
                "at": stamp,
                "sequence": notice["sequence"],
                "matched_first_xbrls": matches,
                "status": "bound_to_first_disclosure"
                if matches
                else "different_first_clock_or_no_eligible_quarterly_xbrl",
            }
        )
    jobs = []
    for url, cases in sorted(groups.items()):
        f = package_path(root, url)
        jobs.append([url, str(f.relative_to(root)), digest(f.read_bytes()), list(cases.values())])
    return {
        "source_edition": edition,
        "evaluator": evaluator(),
        "selection": "every confirmed original in the two independent all-subject reviews, exact first notice timestamp and URL, all four archived quarterly XBRL facts required",
        "input_files": {str(f.relative_to(root.parent)): digest(f.read_bytes()) for f in [a, b]},
        "candidate_bulletins": audit,
        "jobs": jobs,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=1)
    args = p.parse_args()
    if args.workers < 1:
        raise ValueError("Provide a positive worker count")
    tools = worker_tools()
    plan = select(ROOT)
    blob = encoded(plan)
    pid = digest(blob)
    pack(ROOT / "xbrl-optical-plans" / (pid + ".json.gz"), blob)
    print(
        json.dumps(
            {
                "plan": pid,
                "evaluator": evaluator(),
                "jobs": len(plan["jobs"]),
                "qualified_xbrl_inputs": sum(len(j[-1]) for j in plan["jobs"]),
            }
        ),
        flush=True,
    )
    out = []
    with ThreadPoolExecutor(args.workers) as pool:
        for job, rows in zip(
            plan["jobs"],
            pool.map(lambda item: assess(ROOT, *item, workers_tools=tools), plan["jobs"]),
            strict=True,
        ):
            out.extend(rows)
            print(
                json.dumps(
                    {
                        "source": job[0],
                        "cases": len(rows),
                        "pages": rows[0]["physical_pages"] if rows else 0,
                        "positive_fields": sum(
                            sum(v is not None for v in r["values"].values()) for r in rows
                        ),
                    }
                ),
                flush=True,
            )
    cohort = {
        "evaluator": evaluator(),
        "plan": pid,
        "cohort": "independent-earlier-release",
        "records": out,
    }
    data = encoded(cohort)
    edition = digest(data)
    pack(ROOT / "xbrl-optical-editions" / (edition + ".json.gz"), data)
    write_once(
        ROOT / "xbrl-optical-completions" / "independent-earlier-release" / (edition + ".json"),
        encoded(
            {"edition_sha256": edition, "evaluator": evaluator(), "plan": pid, "cases": len(out)}
        ),
    )
    print(
        json.dumps(
            {
                "edition": edition,
                "field_counts": dict(
                    Counter(k for r in out for k, v in r["values"].items() if v is not None)
                ),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
