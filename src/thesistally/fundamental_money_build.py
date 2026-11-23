"""Reconcile quarantined original monetary fields using two local, offline page renderings."""

import fcntl
import gzip
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .discovery_registry import digest, encoded, write_once
from .fundamental_money_witness import SIZES, archive_observation, deciding_witness, location
from .fundamental_render_cache import raster
from .fundamental_rendered import identity
from .fundamental_rendered_build import environment, original_pdf
from .fundamental_rendered_money import observe


def examine(root, program, row, review, tools):
    to_check = {}
    sha = review["notice_sha256"]
    for field, discrepancy in review["rejected"].items():
        row[field] = discrepancy["structured_value"]
        info = {
            **identity(row, {"source_url": review["notice_url"]}, {"sha256": sha}, review, field),
            "native_value": discrepancy["earlier_text_candidate"],
        }
        path = location(root, info)
        attempt = root / "rendered-money-attempts" / digest(encoded(tools)) / path.name
        if not path.exists() and not attempt.exists():
            to_check[field] = (info, attempt)
    if not to_check:
        return Counter()
    pdf = original_pdf(root, sha, review)
    views = []
    for size in SIZES:
        png, raw = raster(root, program, pdf, review["pdf_sha256"], review["page"], size, tools)
        views.append((size, png, raw, json.loads(raw)))
    counts = Counter()
    for field, (info, attempt) in to_check.items():
        samples = [(size, png, raw, observe(row, boxes, field)) for size, png, raw, boxes in views]
        observations = [obs for _, _, _, obs in samples]
        decision = deciding_witness(observations, info["native_value"], info["structured_value"])
        if decision:
            archive_observation(root, info, samples, decision, tools)
        write_once(
            attempt, encoded({"identity": info, "decision": decision, "observations": observations})
        )
        counts[decision or "unresolved"] += 1
    return counts


def monetary_tools(tools):
    tools = dict(tools)
    tools["monetary_extraction_sha256"] = digest(
        Path(__file__).with_name("fundamental_rendered_money.py").read_bytes()
    )
    tools["monetary_method_sha256"] = digest(
        Path(__file__).with_name("fundamental_money_witness.py").read_bytes()
    )
    return tools


def main():
    root, program, options = environment()
    tools = monetary_tools(options)
    receipt = json.loads((root / "filings-receipt.json").read_bytes())["field_reviews"]
    raw = gzip.decompress((root / receipt["file"]).read_bytes())
    if digest(raw) != receipt["sha256"]:
        raise ValueError("Source field review changed")
    plans = {}
    for filename in ("attachment-plan.json", "dated-alias-attachment-plan.json"):
        if (root / filename).is_file():
            plans.update({r["id"]: r for r in json.loads((root / filename).read_bytes())})
    # Some duplicate annual and quarterly accessions point to the same first PDF.
    # Keep each distinct first/revised numeric comparison and render it once.
    tasks = {}
    for review in json.loads(raw):
        r = plans.get(review["id"])
        if not r:
            continue
        key = (
            r["isin"],
            r["basis"],
            r["period_end"],
            review["notice_url"],
            review["page"],
            review["pdf_sha256"],
            encoded(review["rejected"]),
        )
        tasks.setdefault(key, (dict(r), review))

    def work(item):
        row, review = item
        locks = root / "rendered-money-attempts/locks"
        locks.mkdir(parents=True, exist_ok=True)
        # Separate accessions can reference exactly the same page and printed cells.
        page_key = f"{review['pdf_sha256']}-{review['page']}"
        with (locks / (page_key + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return examine(root, program, row, review, tools)

    counts = Counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(work, tasks.values()):
            counts.update(result)
    print(json.dumps(counts, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
