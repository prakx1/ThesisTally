"""Check original historical results pages in resumable exchange-attachment order."""

import argparse
import gzip
import json
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import fundamental_legacy_batch as batch
from .discovery_registry import digest, encoded, write_once
from .fundamental_facts import FIELDS
from .fundamental_legacy_optics import extractor_version, inputs, path_for, save_page
from .fundamental_media import archive_documents
from .fundamental_media_recovery import package_path
from .fundamental_render_cache import raster
from .fundamental_rendered_build import environment


def requests(root, per_year):
    receipt = json.loads((root / "filings-receipt.json").read_bytes())
    sha = receipt["sha256"]
    raw = gzip.decompress((root / "vintages" / (sha + ".jsonl.gz")).read_bytes())
    if digest(raw) != sha:
        raise ValueError("Changed numeric edition during legacy source materialization")
    plans = {}
    for filename in ("attachment-plan.json", "dated-alias-attachment-plan.json"):
        if (root / filename).is_file():
            plans.update({r["id"]: r for r in json.loads((root / filename).read_bytes())})
    groups = defaultdict(dict)
    by_year = defaultdict(set)
    for line in raw.splitlines():
        r = json.loads(line)
        if (
            r["id"] not in plans
            or not (r.get("structured_url") or "").lower().endswith(".html")
            or not r.get("structured_sha256")
            or r["filing_status"] not in {"original", "uncertified"}
            or not any(v is not None for v in r["structured_values"].values())
            or r["first_reported"]
            and all(r[k] is not None for k in FIELDS)
        ):
            continue
        first = r.get("first_notice") or {}
        url = first.get("source_url")
        if not url or not package_path(root, url).is_file():
            continue
        row = {
            **plans[r["id"]],
            **r["structured_values"],
            "source_url": r["structured_url"],
            "tags": r["structured_tags"],
        }
        identity = (row["isin"], row["period_end"], row["basis"], row["source_url"])
        groups[url][identity] = row
        by_year[row["period_end"][:4]].add(url)
    if per_year is not None:
        # The source sample is stratified by year and independently ordered by URL hash.
        selected = {
            url
            for urls in by_year.values()
            for url in sorted(urls, key=lambda u: digest(u.encode()))[:per_year]
        }
        groups = {u: rows for u, rows in groups.items() if u in selected}
    return sha, [(u, list(rows.values())) for u, rows in sorted(groups.items())]


def freeze_jobs(root, sha, jobs, per_year):
    tasks = []
    for url, filings in jobs:
        file = package_path(root, url)
        tasks.append([url, str(file.relative_to(root)), digest(file.read_bytes()), filings])
    plan = {
        "ledger": sha,
        "extractor": extractor_version(),
        "render_profile": batch.profile(),
        "sample_per_year": per_year,
        "tasks": tasks,
    }
    raw = encoded(plan)
    key = digest(raw)
    write_once(root / "legacy-optical-plans" / (key + ".json.gz"), gzip.compress(raw, mtime=0))
    return key, plan


def load_plan(root, key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Invalid legacy optical source plan")
    raw = gzip.decompress((root / "legacy-optical-plans" / (key + ".json.gz")).read_bytes())
    if digest(raw) != key:
        raise ValueError("Legacy optical source plan changed")
    plan = json.loads(raw)
    if plan["extractor"] != extractor_version():
        raise ValueError("Use the source extractor version pinned to the plan")
    if "render_profile" in plan and plan["render_profile"] != batch.profile():
        raise ValueError("Use the original raster and recognizer version pinned to the plan")
    return plan


def physical_members(package):
    # pdftotext adds a trailing separator. Separate ZIP members can contain identical PDFs.
    return [
        (
            doc["pdf_sha256"],
            len(doc["pages"]) - int(bool(doc["pages"]) and not doc["pages"][-1].strip()),
        )
        for doc in package["pdfs"]
    ]


def confirmed_attempt(attempt, info, positive, members):
    saved = json.loads(attempt.read_bytes())
    if saved["inputs"] != info or saved["counts"].get("basis_quarters_checked") != 1:
        raise ValueError("Legacy optical attempt belongs to different inputs")
    counts = saved["counts"]
    if bool(counts.get("with_verified_fields")) != positive.is_file():
        raise ValueError("Legacy optical attempt and original field evidence disagree")
    gaps = saved["unreadable_pages"]
    if bool(counts.get("basis_quarters_with_unreadable_pages")) != bool(gaps):
        raise ValueError("Legacy unreadable-page count disagrees with its source record")
    physical = dict(members)
    occurrences = Counter(sha for sha, _ in members)
    keys = [(g["pdf_sha256"], g.get("source_occurrence", 1), g["page"]) for g in gaps]
    checked = saved["physical_pages_checked"]
    if (
        type(checked) is not int
        or checked < 0
        or len(keys) != len(set(keys))
        or any(
            gap["source_url"] != info["notice_url"]
            or type(page) is not int
            or type(occurrence) is not int
            or not 1 <= page <= physical.get(sha, 0)
            or not 1 <= occurrence <= occurrences[sha]
            for gap, (sha, occurrence, page) in zip(gaps, keys, strict=True)
        )
        or checked + len(keys) != sum(n for _, n in members)
    ):
        raise ValueError("Legacy optical page counts or source identity disagree")
    return counts


def original_pages(root, program, pdf, sha, count, tools, batch_program):
    first = 1
    if batch_program:
        try:
            for row in batch.read_pages(root, program, batch_program, pdf, sha, count, tools):
                first = row[0] + 1
                yield row
            return
        except ValueError as exc:
            if str(exc) != "Poppler original page dimensions or raster length are invalid":
                raise
            # Poppler can round a square PDF to 2401 by 2400. Render the original
            # bytes directly to PNG, preserving even the extra pixel.
    for page in range(first, count + 1):
        try:
            png, boxes = raster(root, program, pdf, sha, page, 2400, tools, retain_png=False)
            yield page, png, boxes, {"method": "original-poppler-png"}, None
        except subprocess.CalledProcessError as exc:
            yield page, None, None, None, exc.returncode


def check(root, program, tools, job, *, batch_program=None):
    from .fundamental_legacy_raster import observe

    url, relative, expected, filings = job
    file = (root / relative).resolve(strict=True)
    if not file.is_relative_to(root.resolve()) or file.parent.name not in (
        "notice-text",
        "notice-media",
    ):
        raise ValueError("Invalid archived original package path")
    packed = file.read_bytes()
    if digest(packed) != expected:
        raise ValueError("The original optical source package changed")
    package = json.loads(gzip.decompress(packed))
    if url != package["source_url"]:
        raise ValueError("Original notice belongs to another issuer release")
    members = physical_members(package)
    active = {}
    stats = Counter()
    for filing in filings:
        info = inputs(filing, package)
        positive = path_for(root, info)
        attempt = root / "legacy-original-attempts" / extractor_version() / positive.name
        if attempt.is_file():
            stats.update(confirmed_attempt(attempt, info, positive, members))
            continue
        active[positive.stem] = (filing, info, positive, attempt, [])
    if not active:
        return stats
    sha = package["sha256"]
    original = gzip.decompress((root / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(original) != sha:
        raise ValueError("Original legacy disclosure archive changed")
    docs = {p["pdf_sha256"]: p for p in package["pdfs"]}
    pages = 0
    gaps = []
    visits = Counter()
    for meta, pdf in archive_documents(original, root=root):
        pdf_sha = meta["pdf_sha256"]
        if pdf_sha not in docs:
            continue
        visits[pdf_sha] += 1
        native = docs[pdf_sha]["pages"]
        count = len(native) - int(bool(native) and not native[-1].strip())
        result = original_pages(root, program, pdf, pdf_sha, count, tools, batch_program)
        for page, png, raw_boxes, renderer, error in result:
            if error is not None:
                gaps.append(
                    {
                        "source_url": url,
                        "pdf_sha256": pdf_sha,
                        "page": page,
                        "returncode": error,
                        **({"source_occurrence": visits[pdf_sha]} if visits[pdf_sha] > 1 else {}),
                        "reason": "original_page_raster_or_optical_read_failed",
                    }
                )
                continue
            boxes = json.loads(raw_boxes)
            pages += 1
            for filing, _, _, _, found in active.values():
                proof = observe(filing, boxes, image_bytes=png)
                if proof and (
                    sample := save_page(root, page, 2400, pdf_sha, png, raw_boxes, proof, filing)
                ):
                    found.append({**sample, "raster_source": renderer} if batch_program else sample)
    for _, info, positive, attempt, found in active.values():
        counts = {"basis_quarters_checked": 1, "with_verified_fields": int(bool(found))}
        if gaps:
            counts["basis_quarters_with_unreadable_pages"] = 1
        if found:
            write_once(
                positive,
                encoded(
                    {
                        "inputs": info,
                        "extractor_sha256": extractor_version(),
                        "pages": found,
                        "tools": tools,
                    }
                ),
            )
        write_once(
            attempt,
            encoded(
                {
                    "inputs": info,
                    "physical_pages_checked": pages,
                    **({"render_profile": batch.profile()} if batch_program else {}),
                    "unreadable_pages": gaps,
                    "counts": counts,
                }
            ),
        )
        stats.update(confirmed_attempt(attempt, info, positive, members))
    return stats


def main():
    from . import fundamental_legacy_raster  # import the exact extractor before pinning its source

    parser = argparse.ArgumentParser(description=__doc__)
    selecting = parser.add_mutually_exclusive_group()
    selecting.add_argument("--sample-per-year", type=int)
    selecting.add_argument("--plan")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers < 1 or (args.sample_per_year is not None and args.sample_per_year < 1):
        parser.error("Provide positive worker and sample sizes")
    root, program, tools = environment()
    if args.plan:
        key, plan = args.plan, load_plan(root, args.plan)
    else:
        sha, jobs = requests(root, args.sample_per_year)
        key, plan = freeze_jobs(root, sha, jobs, args.sample_per_year)
    jobs = plan["tasks"]
    tools["source_extractor"] = extractor_version()
    batch_program = batch.program(root) if "render_profile" in plan else None
    if batch_program:
        tools["render_profile"] = plan["render_profile"]
    if not callable(fundamental_legacy_raster.observe):
        raise TypeError("Missing legacy results extractor")
    print(
        json.dumps(
            {
                "ledger": plan["ledger"],
                "plan": key,
                "source_extractor": extractor_version(),
                "attachments": len(jobs),
                "sample_per_year": plan["sample_per_year"],
            }
        ),
        flush=True,
    )
    counts = Counter()
    with ThreadPoolExecutor(args.workers) as pool:
        for n, c in enumerate(
            pool.map(
                lambda item: check(root, program, tools, item, batch_program=batch_program), jobs
            ),
            1,
        ):
            counts.update(c)
            if n % 25 == 0:
                print(json.dumps({"attachments_checked": n, **counts}), flush=True)
    print(json.dumps({"attachments_checked": len(jobs), **counts}), flush=True)
    write_once(
        root / "legacy-optical-completions" / (key + ".json"),
        encoded(
            {"plan": key, "ledger": plan["ledger"], "attachments_checked": len(jobs), **counts}
        ),
    )


if __name__ == "__main__":
    main()
