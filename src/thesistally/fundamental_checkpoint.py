"""Pin a numerical vintage and the exact source-acquisition census available for its report."""

from __future__ import annotations

import gzip
import json

from .discovery_registry import git, write_once
from .fundamental_nse import REPO, ROOT, digest, pack


def publish():
    source_index = (ROOT / "source-manifests/index.json").read_bytes()
    source_sha = digest(source_index)
    sources = json.loads(source_index)
    receipt = (ROOT / "filings-receipt.json").read_bytes()
    related = json.loads(receipt)
    edition = related["sha256"]
    if (ROOT / "withdrawals" / (edition + ".json")).is_file():
        raise ValueError("A withdrawn source edition cannot be published as research evidence")
    if sources.get("filings_sha256") != edition:
        raise ValueError("Publish the matching source census before pinning the ledger")
    if not related.get("field_reviews"):
        raise ValueError("Numeric PDF text-layer confirmation audit is missing")
    checks = related["field_reviews"]
    if digest(gzip.decompress((ROOT / checks["file"]).read_bytes())) != checks["sha256"]:
        raise ValueError("Numeric source review fingerprint differs")
    raw_ledger = gzip.decompress((ROOT / "vintages" / (edition + ".jsonl.gz")).read_bytes())
    if digest(raw_ledger) != edition:
        raise ValueError("Numeric vintage contents changed")
    gaps = (ROOT / "notice-probe-gaps.json").read_bytes()
    if digest(gaps) != related["notice_probe_gaps_sha256"]:
        raise ValueError("Gap detail belongs to a different ledger")
    destination = ROOT / "checkpoints" / edition / source_sha
    pack(destination / "filings-receipt.json.gz", receipt)
    pack(destination / "source-index.json.gz", source_index)
    pack(destination / "notice-probe-gaps.json.gz", gaps)
    for view in related["editions"].values():
        if digest(gzip.decompress((ROOT / view["file"]).read_bytes())) != view["sha256"]:
            raise ValueError("Original or changed edition differs from its fingerprint")
    record = {
        "filings_sha256": edition,
        "rows": related["records"],
        "filings_receipt_sha256": digest(receipt),
        "source_census_sha256": source_sha,
        "notice_probe_gaps_sha256": digest(gaps),
        "source_acquisition": sources["notice_acquisition"],
        "field_reviews": checks,
        "source_vintages": related["editions"],
        "git_head_at_checkpoint": git(REPO, "rev-parse", "HEAD").decode().strip(),
    }
    out = destination / "index.json"
    if out.is_file():
        previous = json.loads(out.read_bytes())
        if any(previous[k] != record[k] for k in record if k != "git_head_at_checkpoint"):
            raise ValueError("Checkpoint disagrees with an existing immutable publication")
    else:
        write_once(out, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
    print(str(out.relative_to(REPO)))
