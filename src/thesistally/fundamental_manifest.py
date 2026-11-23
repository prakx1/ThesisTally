"""Publish the compressed source inventory without duplicating multi-GB exchange attachments."""

from __future__ import annotations

import gzip
import json
from collections import Counter

from .fundamental_media_recovery import notice_packages
from .fundamental_nse import ROOT, digest, pack, write_json


def all_requests():
    urls = set()
    for filename in [
        "catalogue-manifest.json",
        "notice-manifest.json",
        "notice-fallback-manifest.json",
    ]:
        path = ROOT / filename
        if path.exists():
            for row in json.loads(path.read_bytes()):
                for field in ["url", "crosscheck_url"]:
                    if row.get(field):
                        urls.add(row[field])
    for p in (ROOT / "catalogues").glob("*.receipt.json"):
        urls.add(json.loads(p.read_bytes())["url"])
    return sorted(urls)


NOTICE_BATCHES = {
    "formal_audit": ("notice-formal-extra-plan.json", "notice-formal-document-gaps.json"),
    "all_categories": ("notice-categories-extra-plan.json", "notice-categories-document-gaps.json"),
    "fiscal_year": ("notice-fiscal-extra-plan.json", "notice-fiscal-document-gaps.json"),
    "initial": ("notice-request-plan.json", "notice-document-gaps.json"),
    "market": ("notice-market-extra-plan.json", "notice-extra-document-gaps.json"),
    "calendar": ("notice-clock-extra-plan.json", "notice-clock-document-gaps.json"),
    "case": ("notice-case-extra-plan.json", "notice-case-document-gaps.json"),
    "timing": ("notice-timing-extra-plan.json", "notice-timing-document-gaps.json"),
    "alias": ("notice-alias-extra-plan.json", "notice-alias-document-gaps.json"),
    "reconcile": ("notice-reconcile-extra-plan.json", "notice-reconcile-document-gaps.json"),
    "language": ("notice-language-extra-plan.json", "notice-language-document-gaps.json"),
}


def failure_state(root, kind, url, error):
    from .fundamental_mirror_audit import audit_history, needs_check
    from .fundamental_retry import MAX_ATTEMPTS, history, transient

    row = {"status": "unavailable", "error": error}
    if transient(error):
        receipts = history(root, kind, url)
        row["recovery_attempts"] = receipts
        latest = receipts[-1] if receipts else None
        if latest and latest["status"] == "unavailable":
            row["terminal_official_error"] = latest["outcome_error"]
        elif len(receipts) >= MAX_ATTEMPTS:
            row["status"] = "unresolved_after_healthy_network_retries"
        else:
            row["status"] = "awaiting_healthy_network_retry"
    mirror = audit_history(root, kind, url)
    if mirror:
        row["mirror_audit"] = mirror
        latest = mirror[-1]
        if latest["status"] == "unavailable":
            row.update(status="unavailable", terminal_official_error=latest["response"])
        elif latest["status"] == "transient":
            row["status"] = (
                "unresolved_after_healthy_network_retries"
                if len(mirror) >= MAX_ATTEMPTS
                else "awaiting_healthy_network_retry"
            )
    elif row["status"] in {
        "unavailable",
        "unresolved_after_healthy_network_retries",
    } and needs_check(root, kind, url, error):
        row["status"] = "awaiting_official_mirror_audit"
    return row


PENDING_STATES = ("pending", "awaiting_healthy_network_retry", "awaiting_official_mirror_audit")


def acquisition_terminal(counts):
    return not any(counts.get(state, 0) for state in PENDING_STATES)


def structured_run_state(root, receipts):
    captured = {row["url"]: row for row in receipts}
    urls = set()
    errors = {}
    for plan, gaps in (
        ("attachment-plan.json", "attachment-gaps.json"),
        ("dated-alias-attachment-plan.json", "dated-alias-attachment-gaps.json"),
    ):
        path = root / plan
        if path.exists():
            urls.update(
                row["source_url"] for row in json.loads(path.read_bytes()) if row.get("source_url")
            )
        p = root / gaps
        if p.exists():
            errors.update(dict(json.loads(p.read_bytes())))
    rows = []
    for url in sorted(urls):
        out = {"url": url}
        receipt = captured.get(url)
        if receipt:
            out.update(
                status="archived", retrieved_url=receipt["retrieved_url"], sha256=receipt["sha256"]
            )
            if url in errors:
                out["earlier_failure"] = errors[url]
        elif url in errors:
            out.update(failure_state(root, "structured", url, errors[url]))
        else:
            out["status"] = "pending"
        rows.append(out)
    return rows, dict(Counter(row["status"] for row in rows))


def notice_run_state(root, packages):
    """Every planned public URL has a terminal receipt or an explicit pending state."""
    captured = {p["url"]: p for p in packages}
    planned = {}
    finished = {}
    for batch, (plan, gaps) in NOTICE_BATCHES.items():
        plan_path = root / plan
        finished[batch] = plan_path.exists() and (root / gaps).exists()
        if plan_path.exists():
            for url in json.loads(plan_path.read_bytes()):
                planned.setdefault(url, []).append(batch)
    rows = []
    for url, batches in sorted(planned.items()):
        key = digest(url.encode())
        failure = root / "notice-request-failures" / (key + ".json")
        media_check = root / "notice-media-checks" / (key + ".json")
        registered = captured.get(url)
        row = {"url": url, "batches": batches}
        if registered:
            row.update(
                status="archived",
                sha256=registered["sha256"],
                retrieved_url=registered["retrieved_url"],
            )
            if failure.exists():
                row["earlier_failure"] = json.loads(failure.read_bytes())[1]
        elif failure.exists():
            failed_url, error = json.loads(failure.read_bytes())
            if failed_url != url:
                raise ValueError("Notice failure belongs to a different URL")
            row.update(failure_state(root, "notice", url, error))
            source = root / "notice-raw-receipts" / (key + ".json")
            if source.is_file():
                raw = json.loads(source.read_bytes())
                if raw["url"] != url:
                    raise ValueError("Original raw packet belongs to a different archive URL")
                row["raw_attachment"] = raw
            if media_check.exists():
                row["media_recovery"] = json.loads(media_check.read_bytes())
        else:
            row["status"] = "pending"
        rows.append(row)
    status_counts = Counter(r["status"] for r in rows)
    return rows, {
        "planned_urls": len(planned),
        "completed_batches": finished,
        "by_status": dict(status_counts),
        "complete": all(finished.values()) and acquisition_terminal(status_counts),
        "all_planned_documents_archived": len(planned) == status_counts["archived"]
        and all(finished.values()),
        "bounded_transport_exhaustions": status_counts["unresolved_after_healthy_network_retries"],
        "status_definition": "Acquisition is complete after every public request is terminal. Unavailable files and exhausted healthy-network attempts remain missing source data.",
    }


def write_manifest():
    manifest = {"structured": [], "first_notice": [], "catalogues": []}
    for p in sorted((ROOT / "receipts").glob("*.json")):
        receipt = json.loads(p.read_bytes())
        receipt.setdefault("retrieved_url", receipt["url"])
        manifest["structured"].append(receipt)
    manifest["structured_requests"], structured_state = structured_run_state(
        ROOT, manifest["structured"]
    )
    for p in notice_packages(ROOT):
        receipt = json.loads(gzip.decompress(p.read_bytes()))
        manifest["first_notice"].append(
            {
                "url": receipt["source_url"],
                "retrieved_url": receipt.get("retrieved_url", receipt["source_url"]),
                "sha256": receipt["sha256"],
                "text_sha256": digest(gzip.decompress(p.read_bytes())),
                "pdfs": [
                    {
                        **{
                            field: d[field]
                            for field in (
                                "pdf_sha256",
                                "source_member_sha256",
                                "source_member",
                                "source_media",
                                "rendering",
                            )
                            if field in d
                        },
                        "pages": len(d["pages"]),
                    }
                    for d in receipt["pdfs"]
                ],
            }
        )
    manifest["notice_requests"], acquisition = notice_run_state(ROOT, manifest["first_notice"])
    ledger = ROOT / "filings.jsonl"
    manifest["accepted_pages"] = []
    edition_sha = digest(ledger.read_bytes()) if ledger.exists() else None
    if ledger.exists():
        with ledger.open() as rows:
            for line in rows:
                row = json.loads(line)
                proof = row.get("notice_proof")
                if not row["first_reported"] or not proof:
                    continue
                manifest["accepted_pages"].append(
                    {
                        "id": row["id"],
                        "isin": row["isin"],
                        "period_end": row["period_end"],
                        **{
                            k: proof[k]
                            for k in (
                                "notice_url",
                                "notice_sha256",
                                "pdf_sha256",
                                "source_member",
                                "source_member_sha256",
                                "source_media",
                                "rendering",
                                "method",
                                "pages_used",
                                "native_page_text_sha256",
                                "recognition_receipt_sha256",
                                "rendered_field_evidence",
                                "field_page_evidence",
                                "additional_original_optics",
                                "additional_native_cells",
                                "additional_reviewed_xbrl_optics",
                                "cohort_receipt",
                                "original_field_conflicts",
                            )
                            if k in proof
                        },
                    }
                )
    manifest["raw_notice_receipts"] = [
        json.loads(p.read_bytes())
        for directory, pattern in (
            ("notice-raw-receipts", "*.json"),
            ("notice-raw-vintages", "*/*.json"),
        )
        for p in sorted((ROOT / directory).glob(pattern))
    ]
    manifest["historical_path_requests"] = [
        json.loads(p.read_bytes())
        for p in sorted((ROOT / "historical-path-requests").glob("*.json"))
    ]
    manifest["mirror_attempts"] = [
        json.loads(p.read_bytes()) for p in sorted((ROOT / "mirror-audits").glob("*/*/*.json"))
    ]
    manifest["transfer_recovery_attempts"] = [
        json.loads(p.read_bytes()) for p in sorted((ROOT / "transfer-retries").glob("*/*/*.json"))
    ]
    for url in all_requests():
        key = digest(url.encode())
        cached = ROOT / "catalogues" / (key + ".json.gz")
        if not cached.exists():
            continue
        raw = gzip.decompress(cached.read_bytes())
        data = json.loads(raw)
        receipt = ROOT / "catalogues" / (key + ".receipt.json")
        retrieved = json.loads(receipt.read_bytes())["retrieved_url"] if receipt.exists() else url
        manifest["catalogues"].append(
            {
                "url": url,
                "retrieved_url": retrieved,
                "sha256": digest(raw),
                "rows": len(data["data"])
                if isinstance(data, dict) and "data" in data
                else len(data),
            }
        )
    output = ROOT / "source-manifests"
    hashes = {}
    photos = {}
    for name in (
        "attachment-plan",
        "catalogue-gaps",
        "attachment-gaps",
        "catalogue-manifest",
        "notice-manifest",
        "notice-fallback-manifest",
        "notice-market-manifest",
        "notice-census",
        "notice-fallback-rows",
        "notice-market-rows",
        "notice-request-plan",
        "notice-market-extra-plan",
        "notice-document-gaps",
        "notice-extra-document-gaps",
        "notice-clock-extra-plan",
        "notice-clock-document-gaps",
        "notice-tail-assistance",
        "notice-folio-pass",
        "identity-bhavcopies",
        "notice-language-rows",
        "notice-language-identical-rows",
        "notice-language-identical-manifest",
        "notice-language-manifest",
        "notice-language-extra-plan",
        "notice-language-document-gaps",
        "notice-reconcile-extra-plan",
        "notice-reconcile-document-gaps",
        "dated-alias-attachment-plan",
        "dated-alias-catalogue-gaps",
        "dated-alias-attachment-gaps",
        "notice-alias-extra-plan",
        "notice-alias-document-gaps",
        "notice-alias-manifest",
        "notice-case-manifest",
        "notice-case-rows",
        "notice-previous-copies",
        "notice-case-extra-plan",
        "notice-case-document-gaps",
        "notice-timing-extra-plan",
        "notice-timing-document-gaps",
        "notice-formal-review-rows",
        "notice-formal-review-manifest",
        "notice-extra-category-rows",
        "notice-extra-category-manifest",
        "notice-formal-extra-plan",
        "notice-formal-document-gaps",
        "notice-categories-extra-plan",
        "notice-categories-document-gaps",
        "notice-fiscal-extra-plan",
        "notice-fiscal-document-gaps",
        "notice-alias-rows",
        "transfer-retry-summary",
        "mirror-audit-summary",
        "structured-mirror-audit-summary",
    ):
        source = ROOT / (name + ".json")
        if source.is_file():
            raw = source.read_bytes()
            sha = digest(raw)
            pack(output / (sha + ".json.gz"), raw)
            photos[source.name] = {"sha256": sha, "file": sha + ".json.gz"}
    for key, rows in manifest.items():
        raw = b"".join(
            (
                json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            for r in rows
        )
        sha = digest(raw)
        pack(output / (sha + ".jsonl.gz"), raw)
        hashes[key] = {"sha256": sha, "records": len(rows), "file": sha + ".jsonl.gz"}
    write_json(
        output / "index.json",
        {
            **hashes,
            "filings_sha256": edition_sha,
            "notice_acquisition": acquisition,
            "structured_acquisition": structured_state,
            "structured_acquisition_complete": all(
                (ROOT / f).is_file()
                for f in ("attachment-gaps.json", "dated-alias-attachment-gaps.json")
            )
            and acquisition_terminal(structured_state),
            "all_planned_structured_documents_archived": sum(structured_state.values())
            == structured_state.get("archived", 0),
            "inventories": photos,
        },
    )


if __name__ == "__main__":
    from .fundamental_sources import run

    run("manifest")
