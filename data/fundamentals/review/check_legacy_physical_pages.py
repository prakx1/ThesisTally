"""Reconstruct the physical page census of the exchange's legacy first-result packets."""

import argparse
import gzip
import json
import re
import subprocess
from collections import Counter

from check_nominated_plan import LEGACY
from check_progress_bound import canon
from reconcile import METRICS, F, original_document, sha

N = F / "nse"


def run(plan_hash, require_complete, sample_count):
    plan_bytes = gzip.decompress(
        (N / "legacy-optical-plans" / (plan_hash + ".json.gz")).read_bytes()
    )
    assert sha(plan_bytes) == plan_hash
    plan = json.loads(plan_bytes)
    extractor = plan["extractor"]
    roots = N / "legacy-original-attempts" / extractor
    counts = Counter()
    sample = {}
    work = sorted(plan["tasks"], key=lambda task: sha(task[0].encode()))
    markers = {}
    for url, rel, expected, filings in work:
        packed = (N / rel).read_bytes()
        assert sha(packed) == expected
        source = json.loads(gzip.decompress(packed))
        assert url == source["source_url"]
        docs = source["pdfs"]
        members = sorted([d["pdf_sha256"], len(d["pages"])] for d in docs)
        pages = []
        for d in docs:
            texts = d["pages"]
            # `pdftotext -layout` normally ends each PDF with a form-feed and a final empty slot.
            pages.append((d["pdf_sha256"], len(texts) - int(bool(texts) and not texts[-1].strip())))
        physical_total = sum(n for _, n in pages)
        for f in filings:
            ident = {
                "isin": f["isin"],
                "period_end": f["period_end"],
                "basis": f["basis"],
                "structured_url": f["source_url"],
                "structured_values": {k: f.get(k) for k in METRICS},
                "notice_url": url,
                "package_sha256": source["sha256"],
                "source_members": members,
            }
            key = sha(canon({"inputs": ident, "extractor": extractor}))
            p = roots / (key + ".json")
            counts["planned_basis_records"] += 1
            if not p.exists():
                counts["pending"] += 1
                continue
            raw = p.read_bytes()
            marker = json.loads(raw)
            assert marker["inputs"] == ident and marker["counts"]["basis_quarters_checked"] == 1
            bad = marker["unreadable_pages"]
            assert all(row["source_url"] == url for row in bad)
            unresolved = [(e["pdf_sha256"], e.get("source_occurrence", 1), e["page"]) for e in bad]
            occurrences = Counter(digest for digest, _ in pages)
            extent = dict(pages)
            assert len(unresolved) == len(set(unresolved))
            assert all(
                type(pg) is int
                and 1 <= pg <= extent[h]
                and type(o) is int
                and 1 <= o <= occurrences[h]
                for h, o, pg in unresolved
            )
            observed = marker["physical_pages_checked"]
            assert type(observed) is int and observed >= 0
            assert observed + len(unresolved) == physical_total, (url, observed, unresolved, pages)
            counts["checked_basis"] += 1
            counts["verified_positive_basis"] += bool(marker["counts"]["with_verified_fields"])
            counts["observed_physical_page_basis_reads"] += observed
            counts["unreadable_page_basis_reads"] += len(unresolved)
            markers[key] = sha(raw)
            if len(sample) >= sample_count:
                continue
            for document, expected_pages in pages:
                if document in sample or len(sample) >= sample_count:
                    continue
                try:
                    original = original_document(source["sha256"], document)
                except AssertionError as failure:
                    # A non-PDF source may require image conversion. A changed source hash must still stop the audit.
                    if failure.args != ((source["sha256"], document),):
                        raise
                    counts["raw_pdf_sample_unavailable"] += 1
                    continue
                info = subprocess.run(
                    ["pdfinfo", "-"], input=original, capture_output=True, check=True, timeout=120
                ).stdout
                found = re.search(rb"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
                assert found and int(found[1]) == expected_pages
                sample[document] = {
                    "raw_original": source["sha256"],
                    "actual_pdf_pages": expected_pages,
                }
    if require_complete:
        assert not counts["pending"]
    return {
        "legacy_plan": plan_hash,
        "source_edition": plan["ledger"],
        "source_evaluator": extractor,
        "census": dict(counts),
        "unique_legacy_basis_receipts_sha256": sha(canon(markers)),
        "physical_page_counts_from_signed_original_sample": sample,
    }


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--plan", default=LEGACY)
    args.add_argument("--require-complete", action="store_true")
    args.add_argument("--raw-samples", type=int, default=16)
    a = args.parse_args()
    print(json.dumps(run(a.plan, a.require_complete, a.raw_samples), indent=2, sort_keys=True))
