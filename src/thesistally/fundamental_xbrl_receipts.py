"""Verify the physical reads and original evidence behind quarterly optical receipts."""

import gzip
import json
import re
from collections import Counter, defaultdict

from . import fundamental_legacy_batch as batch
from .discovery_registry import digest, encoded
from .fundamental_legacy_optics_build import physical_members
from .fundamental_media import all_statement_members_readable
from .fundamental_review_optics import evaluator, identity, page_observations, resolve
from .fundamental_xbrl_subset import selection


def checked_body(root, row):
    path = (root / row["file"]).resolve(strict=True)
    if not path.is_relative_to((root / "original-xbrl-optics").resolve()):
        raise ValueError("Completed optical receipt is outside the research archive")
    raw = gzip.decompress(path.read_bytes())
    if digest(raw) != row["receipt_sha256"]:
        raise ValueError("Completed optical receipt changed")
    return json.loads(raw)


def original_scope(root, job, version):
    p = (root / job["package_path"]).resolve(strict=True)
    if not p.is_relative_to(root.resolve()) or p.parent.name not in {"notice-text", "notice-media"}:
        raise ValueError("Unexpected original package path")
    raw = p.read_bytes()
    if digest(raw) != job["package_sha256"]:
        raise ValueError("Original package differs from the signed plan")
    package = json.loads(gzip.decompress(raw))
    original = package["sha256"]
    if package["source_url"] != job["url"] or job.get("original_sha256", original) != original:
        raise ValueError("Original disclosure differs from the signed plan")
    physical = dict(physical_members(package))  # evaluator reads a duplicate PDF member only once
    if any(not re.fullmatch("[0-9a-f]{64}", sha) for sha in (*physical, original)):
        raise ValueError("Invalid original PDF member hash")
    current = selection(physical, job.get("selected_pages"), version)
    selected = current.get("pages", {k: range(1, n + 1) for k, n in physical.items()})
    scope = {(sha, n) for sha, nums in selected.items() for n in nums}
    container_gap = False
    if set(physical) != {original}:
        source = gzip.decompress(
            (root / "notice-objects" / original[:2] / (original + ".gz")).read_bytes()
        )
        if digest(source) != original:
            raise ValueError("Original container hash differs from its census")
        container_gap = not all_statement_members_readable(source)
    return original, current, scope, sum(physical.values()) - len(scope), container_gap


def page_key(item):
    if type(item.get("page")) is not int:
        raise ValueError("Invalid submitted physical page number")
    return item["pdf_sha256"], item["page"]


def page_objects(root, page, tools):
    member, number = page_key(page)
    old, new, inputs = batch._paths(root, {**tools, "pdf_sha256": member, "page": number})
    rendered = page["raster_source"]
    if rendered["method"] in {"original-poppler-png", "original-poppler-png-batch-fallback"}:
        path, expected = old, inputs
        if rendered != {"method": rendered["method"]}:
            raise ValueError("Unexpected original Poppler raster receipt")
    else:
        path, expected = new, {**inputs, "render_profile": batch.profile()}
    record = json.loads(path.read_bytes())
    if path == new and rendered != {**batch.profile(), "rgb_sha256": record["rgb_sha256"]}:
        raise ValueError("Signed original pixel-rendering profile differs")
    psha, bsha = page["png_sha256"], page["boxes_sha256"]
    if (record["inputs"], record["png"], record["word_boxes"]) != (expected, psha, bsha):
        raise ValueError("Page pixels or words are bound to a different original source page")
    if not all(isinstance(x, str) and re.fullmatch("[0-9a-f]{64}", x) for x in [psha, bsha]):
        raise ValueError("Invalid optical object identity")
    objects = root / "rendered-page-objects"
    words = gzip.decompress((objects / bsha[:2] / (bsha + ".json.gz")).read_bytes())
    if digest(words) != bsha:
        raise ValueError("Original optical words changed")
    return objects / psha[:2] / (psha + ".png"), words


def read_pages(body, expected, container_gap):
    pages = {}
    for item in body["pages"]:
        k = page_key(item)
        if k in pages:
            raise ValueError("Duplicate original page read")
        pages[k] = item
    failed = set()
    package_reasons = []
    for gap in body["unreadable_pages"]:
        if "page" not in gap:
            package_reasons.append(gap)
            continue
        k = page_key(gap)
        if (
            k in failed
            or set(gap) != {"pdf_sha256", "page", "returncode"}
            or type(gap["returncode"]) is not int
            or gap["returncode"] == 0
        ):
            raise ValueError("Invalid or duplicate physical page failure")
        failed.add(k)
    reasons = [{"reason": "some_original_archive_members_are_unreadable"}] if container_gap else []
    if package_reasons != reasons or pages.keys() & failed or pages.keys() | failed != expected:
        raise ValueError("Observed and failed physical pages do not partition the original plan")
    return pages, failed


def verify_cases(root, job, rows, version=None, *, expected_evaluator=None, expected_tools=None):
    expected_evaluator = expected_evaluator or evaluator()
    if expected_evaluator != evaluator():
        raise ValueError("Quarterly receipts require the exact registered field evaluator")
    cases = {c["filing"]["id"]: c for c in job["cases"]}
    if len(cases) != len(job["cases"]) or sorted(cases) != sorted(r["id"] for r in rows):
        raise ValueError("Completed optical job has missing, extra or duplicate filings")
    original, selected, wanted, omitted, container_gap = original_scope(root, job, version)
    inventory = None
    cached_pages = {}
    stats = Counter()
    for row in rows:
        body = checked_body(root, row)
        if expected_tools is not None and body["tools"] != expected_tools:
            raise ValueError("Receipt uses different optical tools from the signed run")
        case = cases[row["id"]]
        filing = case["filing"]
        info = identity(filing, case["structured_sha256"], job["url"], original, case["release_at"])
        key = digest(encoded({"identity": info, "selection": selected}))
        if (
            body["identity"] != info
            or row["base_key"] != digest(encoded(info))
            or row["key"] != key
            or body["package_file"] != job["package_path"]
            or body["package_sha256"] != job["package_sha256"]
            or body["selection"] != selected
            or row["selection"] != selected
        ):
            raise ValueError(
                "Completed optical receipt describes another quarterly release or selection"
            )
        pages, failed = read_pages(body, wanted, container_gap)
        if (
            body["omitted_pages"] != omitted
            or row["omitted_pages"] != omitted
            or row["unreadable_pages"] != body["unreadable_pages"]
            or type(row["physical_pages"]) is not int
            or row["physical_pages"] != len(pages)
        ):
            raise ValueError("Quarterly receipt counts disagree with its physical reads")
        by_page = defaultdict(list)
        for o in body["observations"]:
            k = page_key(o)
            if k not in pages or any(o.get(f) != v for f, v in pages[k].items()):
                raise ValueError(
                    "Statement observation refers to an absent or failed original page"
                )
            by_page[k].append(o)
        page_signature = encoded([body["tools"], body["pages"], body["unreadable_pages"]])
        if inventory is None:
            inventory = page_signature
            stats.update(
                original_packages=1,
                observed_pages=len(pages),
                failed_pages=len(failed),
                omitted_pages=omitted,
                packages_with_unreadable_members=int(container_gap),
            )
        elif inventory != page_signature:
            raise ValueError("Basis filings from the same original report different physical reads")
        for k, p in pages.items():
            if k not in cached_pages:
                cached_pages[k] = page_objects(root, p, body["tools"])
            image, words = cached_pages[k]
            # Every observed cell preserves its image. Re-read retained images even
            # when a completed receipt claims that the page yielded no fields.
            if k not in by_page and not image.is_file():
                continue
            png = image.read_bytes()
            if digest(png) != p["png_sha256"]:
                raise ValueError("Signed original page image changed")
            actual = [{**o, **p} for o in page_observations(filing, png, json.loads(words))]
            if actual != by_page.get(k, []):
                raise ValueError(
                    "Declared financial observations differ from the original PNG and words"
                )
        values, hits, conflicts, misses = resolve(
            info["structured_values"], body["observations"], complete=not body["unreadable_pages"]
        )
        counts = {f: len({page_key(o) for o in observations}) for f, observations in hits.items()}
        if (
            any(
                body[f] != v or row[f] != v
                for f, v in (
                    ("values", values),
                    ("conflicts", conflicts),
                    ("nonmatching_fields", misses),
                )
            )
            or row["supporting_page_counts"] != counts
        ):
            raise ValueError(
                "Declared quarterly figures or support counts lack matching source observations"
            )
    return dict(stats)
