"""Reconcile modern original-statement reads with their frozen filing and page selections."""

import json
from collections import Counter, defaultdict

from .discovery_registry import digest, encoded
from .fundamental_processing import packed
from .fundamental_xbrl_receipts import checked_body, original_scope, verify_cases


def job_fields(cohort, raw):
    if cohort == "modern-statement-pages":
        return raw
    if cohort != "independent-earlier-release":
        raise ValueError("Unknown original quarterly statement cohort")
    url, path, sha, cases = raw
    return {"url": url, "package_path": path, "package_sha256": sha, "cases": cases}


def expected_jobs(cohort, plan):
    wanted = {}
    omitted = {}
    for index, raw in enumerate(plan["jobs"]):
        job = job_fields(cohort, raw)
        if "selected_pages" in job and not any(job["selected_pages"].values()):
            omitted[digest(encoded(job))] = [c["filing"]["id"] for c in job["cases"]]
            continue
        for case in job["cases"]:
            key = (case["filing"]["id"], job["url"], case["release_at"])
            if key in wanted:
                raise ValueError("Quarterly optical plan contains a duplicate original disclosure")
            wanted[key] = index
    return wanted, omitted


def audit_cohort(root, name, plan_id, evaluator, reference, markers):
    plan = packed(root / "xbrl-optical-plans" / (plan_id + ".json.gz"), plan_id)
    if plan["source_edition"] != reference:
        raise ValueError("Modern page plan uses a different source inventory")
    wanted, omitted = expected_jobs(name, plan)
    candidates = []
    for file in markers:
        meta = json.loads(file.read_bytes())
        if meta["plan"] == plan_id and meta["evaluator"] == evaluator:
            candidates.append((file, meta))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one completed original-page review for {name} and its named plan"
        )
    file, meta = candidates[0]
    edition = meta["edition_sha256"]
    if file.stem != edition:
        raise ValueError("Original-page completion marker refers to another edition")
    study = packed(root / "xbrl-optical-editions" / (edition + ".json.gz"), edition)
    if (
        (study["cohort"], study["plan"], study["evaluator"]) != (name, plan_id, evaluator)
        or study.get("no_nominated_pages", {}) != omitted
        or len(wanted) != len(study["records"])
        or len(wanted) != meta["cases"]
        or (
            name == "modern-statement-pages"
            and (
                study["source_edition"] != reference
                or study["selection_version"] != plan["selection_version"]
                or not study.get("tools")
            )
        )
    ):
        raise ValueError("Original-page edition omits or adds planned filing observations")
    fields = Counter()
    conflicts = Counter()
    groups = defaultdict(list)
    for row in study["records"]:
        body = checked_body(root, row)
        info = body["identity"]
        key = (row["id"], info["original_url"], info["release_at"])
        index = wanted.pop(key, None)
        if index is None:
            raise ValueError("Optical edition includes an unplanned filing or release clock")
        groups[index].append(row)
        fields.update(k for k, v in row["values"].items() if v is not None)
        conflicts.update(row["conflicts"].keys())
    if wanted:
        raise ValueError("Original-page edition is missing planned filings")
    physical = Counter()
    for index, raw_job in enumerate(plan["jobs"]):
        job = job_fields(name, raw_job)
        rows = groups.get(index)
        if rows is None:
            _, _, selected, omitted_count, container_gap = original_scope(
                root, job, plan["selection_version"]
            )
            if selected or digest(encoded(job)) not in omitted:
                raise ValueError("A planned original page assessment disappeared")
            physical.update(
                packages_without_nominations=1,
                omitted_pages=omitted_count,
                packages_with_unreadable_members=int(container_gap),
            )
            continue
        physical.update(
            verify_cases(
                root,
                job,
                rows,
                plan.get("selection_version"),
                expected_evaluator=evaluator,
                expected_tools=study.get("tools"),
            )
        )
    if name == "modern-statement-pages":
        totals = {
            "packages_accounted_for": len(plan["jobs"]),
            "nominated_pages": physical["observed_pages"] + physical["failed_pages"],
            "filings_with_reads": len(study["records"]),
            "packages_without_nominated_pages": len(omitted),
            "fields_confirmed": sum(fields.values()),
        }
        if any(study.get("counts", {}).get(k, 0) != v for k, v in totals.items()):
            raise ValueError(
                "Modern edition counters differ from the verified physical source reads"
            )
    return {
        "plan": plan_id,
        "edition": edition,
        "marker_sha256": digest(file.read_bytes()),
        "verified_physical_reads": dict(physical),
        "original_packages": len(plan["jobs"]),
        "nominated_page_reads": (
            sum(len(pages) for job in plan["jobs"] for pages in job["selected_pages"].values())
            if name == "modern-statement-pages"
            else None
        ),
        "physical_pages_in_scope": plan.get("counts", {}).get("physical_pages_across_jobs"),
        "filings_with_reads": len(study["records"]),
        "filings_without_nominated_pages": sum(map(len, omitted.values())),
        "field_observations": dict(fields),
        "conflicting_fields": dict(conflicts),
    }


def checked_editions(root, frozen):
    from .fundamental_xbrl_optics import freeze

    _, paths = freeze(root)
    if sorted(paths) != sorted(frozen.get("reviewed_xbrl_optical_editions", [])):
        raise ValueError("Rebuild the filing ledger to include the completed modern page evidence")
    scope_file = root / "terminal-xbrl-scope.json"
    if not scope_file.is_file():
        if paths:
            raise ValueError("The modern sources have no signed publication scope")
        return {}
    raw = scope_file.read_bytes()
    scope = json.loads(raw)
    cohorts = scope["cohorts"]
    if set(cohorts) != {"modern-statement-pages", "independent-earlier-release"}:
        raise ValueError("Modern original statement scope differs from the named source cohorts")
    markers = [root / p for p in paths]
    return {
        "scope_sha256": digest(raw),
        "reference_filing_edition": scope["source_edition"],
        "cohorts": {
            name: audit_cohort(
                root,
                name,
                plan_id,
                scope["evaluator"],
                scope["source_edition"],
                [p for p in markers if p.parent.name == name],
            )
            for name, plan_id in cohorts.items()
        },
    }
