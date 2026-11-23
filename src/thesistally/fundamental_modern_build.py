"""Read nominated pages of modern NSE originals from an immutable, all-issuer filing plan."""

import argparse
import gzip
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .discovery_guard import output_root
from .discovery_registry import digest, encoded, write_once
from .fundamental_nse import pack
from .fundamental_xbrl_receipts import verify_cases

COHORT = "modern-statement-pages"


def read_plan(root, key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Invalid quarterly statement plan fingerprint")
    raw = gzip.decompress((root / "xbrl-optical-plans" / (key + ".json.gz")).read_bytes())
    if digest(raw) != key:
        raise ValueError("Modern source plan fingerprint changed")
    plan = json.loads(raw)
    if not plan.get("selection_version") or not plan.get("source_edition"):
        raise ValueError("Missing selection or reference filing edition")
    return plan


def check_job(root, run, job, version, tools):
    from .fundamental_xbrl_optics import assess

    marked = sum(len(nums) for nums in job["selected_pages"].values())
    job_key = digest(encoded(job))
    store = run / job_key[:2] / (job_key + ".json")
    if store.is_file():
        checkpoint = json.loads(store.read_bytes())
        if checkpoint["job"] != job_key or checkpoint["nominated_pages"] != marked:
            raise ValueError("Attempt does not belong to this original statement plan")
        rows = checkpoint["records"]
    else:
        rows = []
        if marked:
            rows = assess(
                root,
                job["url"],
                job["package_path"],
                job["package_sha256"],
                job["cases"],
                workers_tools=tools,
                selected_pages=job["selected_pages"],
                selection_version=version,
            )
    if marked:
        verify_cases(root, job, rows, version, expected_tools=tools[2])
    elif rows:
        raise ValueError("A statement with no selected pages yielded a value")
    summary = {"job": job_key, "nominated_pages": marked, "records": rows}
    write_once(store, encoded(summary))
    return summary


def main():
    from .fundamental_xbrl_optics import evaluator, worker_tools

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("Provide at least one worker")
    root = output_root(workstream="fundamentals") / "nse"
    plan = read_plan(root, args.plan)
    tools = worker_tools()
    provenance = {"plan": args.plan, "evaluator": evaluator(), "tools": tools[2]}
    run = root / "modern-optical-attempts" / digest(encoded(provenance))
    write_once(run / "run.json", encoded(provenance))
    jobs = plan["jobs"]
    counts = Counter()
    records = []
    no_pages = {}
    print(json.dumps({**provenance, "original_packages": len(jobs)}), flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        done = workers.map(
            lambda j: check_job(root, run, j, plan["selection_version"], tools), jobs
        )
        for job, result in zip(jobs, done, strict=True):
            rows = result["records"]
            records.extend(rows)
            if not result["nominated_pages"]:
                no_pages[result["job"]] = [case["filing"]["id"] for case in job["cases"]]
            counts["packages_accounted_for"] += 1
            counts["nominated_pages"] += result["nominated_pages"]
            counts["filings_with_reads"] += len(rows)
            counts["packages_without_nominated_pages"] += int(not result["nominated_pages"])
            counts["fields_confirmed"] += sum(
                sum(value is not None for value in row["values"].values()) for row in rows
            )
            if counts["packages_accounted_for"] % 25 == 0:
                print(json.dumps(counts), flush=True)
    cohort = {
        **provenance,
        "cohort": COHORT,
        "source_edition": plan["source_edition"],
        "selection_version": plan["selection_version"],
        "counts": dict(counts),
        "no_nominated_pages": no_pages,
        "records": records,
    }
    body = encoded(cohort)
    key = digest(body)
    pack(root / "xbrl-optical-editions" / (key + ".json.gz"), body)
    marker = {
        "edition_sha256": key,
        "plan": args.plan,
        "evaluator": provenance["evaluator"],
        "cases": len(records),
    }
    write_once(root / "xbrl-optical-completions" / COHORT / (key + ".json"), encoded(marker))
    print(json.dumps({"edition": key, **dict(counts)}), flush=True)


if __name__ == "__main__":
    main()
