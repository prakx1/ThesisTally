"""Handover a completed official-source collection independently of numerical evidence."""

import gzip
import json
from pathlib import Path

from .discovery_registry import write_once
from .fundamental_manifest import NOTICE_BATCHES
from .fundamental_media_recovery import candidate_keys, notice_packages
from .fundamental_notice_proof import plan_documents
from .fundamental_nse import END, ROOT, digest, load_plans, pack, write_json


def publish():
    index = json.loads((ROOT / "source-manifests/index.json").read_bytes())
    notices = index["notice_acquisition"]
    if not notices["complete"] or not index.get("structured_acquisition_complete"):
        raise ValueError("Public source requests or explicit mirror audits are still pending")
    required = (
        "transfer-retry-summary.json",
        "mirror-audit-summary.json",
        "notice-folio-pass.json",
        "identity-bhavcopies.json",
        "notice-case-document-gaps.json",
        "notice-extra-category-manifest.json",
        "notice-extra-category-rows.json",
        "notice-formal-review-manifest.json",
        "notice-formal-review-rows.json",
        "notice-categories-document-gaps.json",
        "notice-fiscal-document-gaps.json",
        "notice-formal-document-gaps.json",
        "notice-document-gaps.json",
        "dated-alias-attachment-gaps.json",
    )
    for name in required:
        inventory = index["inventories"].get(name)
        if not inventory or digest((ROOT / name).read_bytes()) != inventory["sha256"]:
            raise ValueError(f"Publish the final network census after {name}")
    plans = [Path(name).stem for name, _ in NOTICE_BATCHES.values()]
    unattempted = plan_documents(load_plans(ROOT), plans)
    if unattempted:
        raise ValueError(
            f"{len(unattempted)} currently relevant public notices lack a planned disposition"
        )
    folios = json.loads((ROOT / "notice-folio-pass.json").read_bytes())
    keys = candidate_keys(ROOT)
    if (
        folios["candidate_url_keys_sha256"] != digest(("\n".join(sorted(keys)) + "\n").encode())
        or folios["media_decoder_sha256"]
        != digest(Path(__file__).with_name("fundamental_media.py").read_bytes())
        or folios["readable_source_packets"] != len(notice_packages(ROOT))
        or folios["readable_source_packets"] != index["first_notice"]["records"]
        or index["structured"]["records"] != len(list((ROOT / "receipts").glob("*.json")))
    ):
        raise ValueError("Source receipts changed since the final folio pass or inventory")
    if any(
        not json.loads(p.read_bytes())["terminal"]
        for p in (ROOT / "historical-path-requests").glob("*.json")
    ):
        raise ValueError("A dated ticker alternate URL still has unprocessed transport errors")
    rows = {k: v for k, v in index.items() if k not in {"filings_sha256", "accepted_pages"}}
    for value in [*rows.values(), *index["inventories"].values()]:
        if isinstance(value, dict) and "file" in value:
            raw = gzip.decompress((ROOT / "source-manifests" / value["file"]).read_bytes())
            if digest(raw) != value["sha256"]:
                raise ValueError("A final network census object changed")
    census = (
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    sha = digest(census)
    dest = ROOT / "download-censuses" / (sha + ".json.gz")
    pack(dest, census)
    raw_notice_files = rows["raw_notice_receipts"]["file"]

    def sources(name):
        with gzip.open(ROOT / "source-manifests" / name, "rt") as f:
            return {json.loads(line)["url"] for line in f}

    raw_urls = sources(raw_notice_files) | sources(rows["first_notice"]["file"])
    marker = {
        "network_only": True,
        "meaning": "The NSE download and date-identity work is terminal. OCR, numerical source certification, coverage and returns have separate completion conditions.",
        "latest_available_session": str(END),
        "source_census_sha256": sha,
        "source_census_file": str(dest.relative_to(ROOT)),
        "notice_planned": notices["planned_urls"],
        "verified_additional_first_document_urls_unplanned": len(unattempted),
        "notice_outcomes": notices["by_status"],
        "unique_raw_notice_urls": len(raw_urls),
        "readable_notice_urls": rows["first_notice"]["records"],
        "structured_planned": rows["structured_requests"]["records"],
        "structured_outcomes": rows["structured_acquisition"],
        "unique_raw_structured_urls": rows["structured"]["records"],
        "dated_identity_sha256": rows["inventories"]["identity-bhavcopies.json"]["sha256"],
        "folio_check_sha256": rows["inventories"]["notice-folio-pass.json"]["sha256"],
    }
    # Publish the marker atomically after its entire immutable evidence graph is written.
    immutable = ROOT / "download-censuses" / (sha + ".ready.json")
    payload = (
        json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    write_once(immutable, payload)
    target = ROOT / "source-downloads-terminal.json"
    if target.is_file() and target.read_bytes() != payload:
        raise ValueError("Another source handover already exists for this workstream")
    write_json(target, marker)
