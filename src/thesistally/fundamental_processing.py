"""Seal the local reads of a completed exchange acquisition before publishing results."""

import gzip
import json
from collections import Counter
from pathlib import Path

from .discovery_registry import digest, encoded, write_once
from .fundamental_legacy_optics import extractor_version, inputs, path_for
from .fundamental_legacy_optics_build import (
    confirmed_attempt,
    freeze_jobs,
    physical_members,
    requests,
)
from .fundamental_money_build import monetary_tools
from .fundamental_money_witness import location
from .fundamental_nse import load_plans
from .fundamental_ocr import VERSION, capsule
from .fundamental_rendered import identity, receipt_path
from .fundamental_rendered_build import environment


def packed(path, sha):
    raw = gzip.decompress(path.read_bytes())
    if digest(raw) != sha:
        raise ValueError(f"Changed source evidence at {path.name}")
    return json.loads(raw)


def full_legacy_check(root, ledger):
    source, jobs = requests(root, None)
    if source != ledger:
        raise ValueError("Legacy source worklist differs from the numeric edition")
    key, plan = freeze_jobs(root, source, jobs, None)
    finish = root / "legacy-optical-completions" / (key + ".json")
    if not finish.exists():
        raise ValueError(f"Full original optical review is pending for plan {key}")
    audit = json.loads(finish.read_bytes())
    counts, reads = Counter(), {}
    for url, name, expected, filings in plan["tasks"]:
        raw = (root / name).read_bytes()
        package = json.loads(gzip.decompress(raw))
        if digest(raw) != expected or package["source_url"] != url:
            raise ValueError("Legacy source package changed during publication")
        members = physical_members(package)
        for filing in filings:
            info = inputs(filing, package)
            result = path_for(root, info)
            attempt = root / "legacy-original-attempts" / plan["extractor"] / result.name
            counts.update(confirmed_attempt(attempt, info, result, members))
            reads[result.stem] = digest(attempt.read_bytes())
    expected = dict(plan=key, ledger=ledger, attachments_checked=len(plan["tasks"]), **counts)
    if audit != expected:
        raise ValueError("Completed legacy source census differs from its original page reads")
    return {**audit, "attempts_sha256": digest(encoded(reads))}


def original_fields(root, source, tools):
    review = source["field_reviews"]
    filings = {f["id"]: f for f in load_plans(root)}
    reads = {}
    money_key = digest(encoded(monetary_tools(tools)))
    eps_key = digest(encoded(tools))
    missing_plans = []
    for row in packed(root / review["file"], review["sha256"]):
        filing = filings.get(row["id"])
        if not filing:
            missing_plans.append(row["id"])
            continue
        for field, discrepancy in row["rejected"].items():
            later = discrepancy["structured_value"]
            info = identity(
                {**filing, field: later},
                {"source_url": row["notice_url"]},
                {"sha256": row["notice_sha256"]},
                row,
                field,
            )
            jobs = []
            if field == "eps" and later is not None:
                pos = receipt_path(root, digest(encoded(info)))
                jobs.append((info, pos, root / "rendered-page-attempts" / eps_key / pos.name))
            money = {**info, "native_value": discrepancy["earlier_text_candidate"]}
            pos = location(root, money)
            jobs.append((money, pos, root / "rendered-money-attempts" / money_key / pos.name))
            for expected, positive, attempt in jobs:
                file = positive if positive.exists() else attempt
                if not file.is_file():
                    raise ValueError(f"Original field review is pending for {row['id']} {field}")
                raw = file.read_bytes()
                if json.loads(raw)["identity"] != expected:
                    raise ValueError("Original field review refers to another printed cell")
                reads[str(file.relative_to(root))] = digest(raw)
    return {
        "field_review_sha256": review["sha256"],
        "unique_field_reads": len(reads),
        "attempts_sha256": digest(encoded(reads)),
        "unmapped_source_accessions": sorted(set(missing_plans)),
    }


def scanned_pages(root, frozen):
    from .fundamental_media_recovery import notice_packages

    packages = sorted(str(p.relative_to(root)) for p in notice_packages(root))
    if packages != frozen["native"]:
        raise ValueError("Native attachments changed after the numeric edition was created")
    ocr_paths = set(frozen["ocr"])
    errors = {}
    for f in sorted((root / "notice-ocr/issues").glob("*.json")):
        for gap in json.loads(f.read_bytes())["issues"]:
            errors[gap["input"]] = gap["reason"]
    reads, gaps = {}, []
    for file in packages:
        package = json.loads(gzip.decompress((root / file).read_bytes()))
        for doc in package["pdfs"]:
            sha = doc["pdf_sha256"]
            pages = doc["pages"]
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            expected = {str(i) for i, page in enumerate(pages, 1) if len(page.strip()) < 50}
            if not expected or sha in reads:
                continue
            receipt = capsule(root, sha)
            if not receipt.exists():
                name = Path(file).name
                if name not in errors or sha not in errors[name]:
                    raise ValueError(f"Original page OCR is pending for {name} member {sha}")
                gaps.append(
                    {
                        "source": file,
                        "pdf_sha256": sha,
                        "pages": sorted(map(int, expected)),
                        "reason": errors[name],
                    }
                )
                continue
            raw = receipt.read_bytes()
            data = json.loads(gzip.decompress(raw))
            if (
                str(receipt.relative_to(root)) not in ocr_paths
                or data["pdf_sha256"] != sha
                or data["method"] != VERSION
                or not expected.issubset(data["pages"])
            ):
                raise ValueError("Frozen optical input omits an original scanned page or receipt")
            reads[sha] = digest(raw)
    return {
        "packages": len(packages),
        "scanned_documents": len(reads),
        "receipts_sha256": digest(encoded(reads)),
        "unreadable": gaps,
    }


def seal(data):
    coverage = (data / "coverage.json").read_bytes()
    coverage_id = digest(coverage)
    from .fundamental_bse_recheck import recheck
    from .results_registry import require_completed_acquisition

    require_completed_acquisition(data, coverage_id)
    root, _, tools = environment()
    nse = json.loads(coverage)["filing_sources"]["nse"]
    source = json.loads((root / "filings-receipt.json").read_bytes())
    if root != data / "nse" or source["sha256"] != nse["sha256"]:
        raise ValueError("Numeric source edition differs from signed coverage")
    key = source["frozen_ledger_inputs_sha256"]
    frozen = packed(root / "ledger-inputs" / (key + ".json.gz"), key)
    for category, dirs in (
        ("rendered_eps", "notice-rendered-eps/v1"),
        ("rendered_money", "notice-rendered-money/v2"),
        ("legacy_original_optics", "legacy-original-receipts/" + extractor_version()),
    ):
        paths = sorted(str(p.relative_to(root)) for p in (root / dirs).glob("*/*.json"))
        if frozen[category] != paths:
            raise ValueError(f"Rebuild the numeric ledger to include all {category} evidence")
    result = {
        "coverage_sha256": coverage_id,
        "filings_sha256": nse["sha256"],
        "source_census_sha256": nse["source_census_sha256"],
        "frozen_ledger_inputs_sha256": key,
        "legacy": full_legacy_check(root, nse["sha256"]),
        "original_fields": original_fields(root, source, tools),
        "scanned_pages": scanned_pages(root, frozen),
        "bse_fallback": recheck(data, frozen, nse),
    }
    if "reviewed_xbrl_optical_editions" in frozen or (root / "terminal-xbrl-scope.json").exists():
        from .fundamental_xbrl_processing import checked_editions

        result["modern_originals"] = checked_editions(root, frozen)
    path = data / "processing-history" / (coverage_id + ".json")
    write_once(path, encoded(result))
    return path


if __name__ == "__main__":
    from .discovery_guard import output_root

    print(seal(output_root(workstream="fundamentals")))
