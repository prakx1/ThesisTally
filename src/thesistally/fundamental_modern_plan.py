"""Freeze page nominations from a dated original release and independently archived quarterly XML."""

import argparse
import gzip
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .discovery_registry import digest, encoded, write_once
from .fundamental_bse_recheck import checked_gzip, signed_rows
from .fundamental_facts import FIELDS
from .fundamental_media import MAX_ENTRIES, MAX_MEMBER, MAX_TOTAL, media
from .fundamental_notice_proof import REVISION
from .fundamental_nse import ROOT, load_plans, pack
from .fundamental_ocr import NATIVE, VERSION

AUDIT = "fd10f4d2b9985fc82b3413d67b5e1f78c33fc4803436c653320edfefeb6e5873"
SELECTOR = "modern_income_statement_text_v1"
NUMBER = re.compile(r"(?<![\w])[-(]?\d+(?:[,.]\d+)*[)]?(?![\w])")
PERIOD = re.compile(
    r"quarter|three.?months|ninemonths|sixmonths|yearended|periodended|monthsended|standalone|consolidated|(?:march|june|september|december)20[12]\d|(?:31|30)(?:03|06|09|12)20[12]\d"
)
HEADING = re.compile(
    r"financia[l1i]resu[l1i]ts?|statementofprofitandloss|statementofincome|incomestatement|profitandlossaccount"
)
ROWS = [
    re.compile(pattern)
    for pattern in (
        r"revenue|incomefromoperations|interestearned|operationsincome",
        r"totalincome|otherincome|interestincome",
        r"expenses|expenditure|financ[eia](?:cost|charge)|interestexpended|employeebenefit",
        r"profit|lossbeforetax|lossaftertax|netloss|preprovision",
        r"taxexpense|currenttax|deferredtax|incometax",
        r"earningsper|pershare|basiceps|dilutedeps|basicearning",
    )
]


def cues(text):
    # OCR and PDF text may separate every caption letter with spaces. Use that
    # repair only to nominate, never to interpret a number or an accounting basis.
    text = unicodedata.normalize("NFKC", text).casefold()
    compact = re.sub(r"[^a-z0-9]", "", text)
    return (
        sum(1 for _ in NUMBER.finditer(text)),
        bool(PERIOD.search(compact)),
        bool(HEADING.search(compact)),
        sum(bool(r.search(compact)) for r in ROWS),
    )


def nomination(native, optical, previous_context=False):
    views = [cues(v) for v in (native, optical) if v and v.strip()]
    own_context = any(period or heading for _, period, heading, _ in views)
    for n, period, heading, rows in views:
        if n >= 6 and rows >= 2 and (period or heading):
            return True, own_context
        if n >= 12 and rows >= 3 and previous_context:
            return True, own_context
    return False, own_context


def clock(row):
    notice = row.get("first_notice") or {}
    try:
        stamp = datetime.fromisoformat(notice["announced_at"])
        filed = datetime.fromisoformat(row["filed_at"])
    except (ValueError, TypeError, KeyError):
        return None
    if (
        stamp.utcoffset() != timedelta(hours=5, minutes=30)
        or filed.tzinfo is None
        or stamp >= filed
        or stamp.date().isoformat() <= row["period_end"]
    ):
        return None
    return notice["announced_at"]


def case(row, plans):
    if row.get("filing_status") not in {"original", "uncertified"}:
        return None, "later_or_other_edition"
    if row["id"] not in plans:
        return None, "no_registered_formal_accession"
    if row["filing_status"] == "original" and all(row.get(k) is not None for k in FIELDS):
        return None, "original_four_fields_already_complete"
    if not (row.get("structured_url") or "").lower().endswith(".xml"):
        return None, "non_xml_structured_source"
    if not row.get("structured_sha256") or all(
        row["structured_values"].get(f) is None for f in FIELDS
    ):
        return None, "no_verified_structured_figures"
    notice = row.get("first_notice") or {}
    if not notice.get("source_url"):
        return None, "no_first_notice_packet_url"
    when = clock(row)
    if not when:
        return None, "no_strictly_earlier_dated_public_clock"
    if row.get("exchange_filing_type") == "Revision" or REVISION.search(
        " ".join([notice.get("description") or "", notice.get("text") or ""])
    ):
        return None, "revision"
    f = plans[row["id"]]
    if (
        any(f[k] != row[k] for k in ("isin", "period_end", "basis", "filed_at", "symbol"))
        or f["source_url"] != row["structured_url"]
    ):
        raise ValueError("Structured catalogue identity differs from numerical edition")
    return {
        "filing": {**f, **row["structured_values"], "tags": row["structured_tags"]},
        "structured_sha256": row["structured_sha256"],
        "release_at": when,
    }, None


def edition(root, ledger):
    if (
        not re.fullmatch(r"[0-9a-f]{64}", ledger)
        or (root / "withdrawals" / (ledger + ".json")).exists()
    ):
        raise ValueError("Expected a current, valid immutable NSE source edition")
    candidates = []
    for f in sorted((root / "checkpoints" / ledger).glob("*/index.json")):
        ix = json.loads(f.read_bytes())
        if ix["filings_sha256"] == ledger and ix["source_acquisition"]["complete"]:
            candidates.append((f, ix))
    if len(candidates) != 1:
        raise ValueError("A unique completed public-acquisition checkpoint is required")
    path, ix = candidates[0]
    checksum = ix["source_census_sha256"]
    if path.parent.name != checksum:
        raise ValueError("Source census identity disagrees with directory")
    source = json.loads(
        checked_gzip(path.parent / "filings-receipt.json.gz", ix["filings_receipt_sha256"])
    )
    census = json.loads(checked_gzip(path.parent / "source-index.json.gz", checksum))
    if (
        source["sha256"] != ledger
        or census["filings_sha256"] != ledger
        or not census["structured_acquisition_complete"]
    ):
        raise ValueError("Source checkpoint has mixed or incomplete input editions")
    return source, census, checksum


def source_members(blob, members):
    """Inventory actual archive leaves. No conversion, rendering, or extraction to disk."""
    by_hash = {m["source_member_sha256"] for m in members}
    leaves = []
    gaps = []
    budget = [0, 0]

    def walk(raw, name, depth):
        kind = media(raw)
        if kind != "zip":
            sha = digest(raw)
            record = {
                "source_member": name,
                "source_member_sha256": sha,
                "source_media": kind or "unrecognized",
                "decoded": sha in by_hash,
            }
            leaves.append(record)
            if sha not in by_hash and not (
                name.startswith("__MACOSX/")
                or Path(name).name.lower() in {"thumbs.db", ".ds_store", "desktop.ini"}
            ):
                gaps.append({**record, "cause": "original_member_without_decoded_page_census"})
            return
        if depth >= 2:
            gaps.append(
                {
                    "source_member": name,
                    "cause": "nested_zip_outside_decoder",
                    "sha256": digest(raw),
                }
            )
            return
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    label = name + "!" + entry.filename if name else entry.filename
                    budget[0] += 1
                    budget[1] += entry.file_size
                    if (
                        budget[0] > MAX_ENTRIES
                        or budget[1] > MAX_TOTAL
                        or entry.file_size > MAX_MEMBER
                        or entry.flag_bits & 1
                    ):
                        gaps.append(
                            {
                                "source_member": label,
                                "cause": "opaque_or_oversized_archive_member",
                                "bytes": entry.file_size,
                            }
                        )
                        continue
                    try:
                        read = archive.read(entry)
                    except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as error:
                        gaps.append({"source_member": label, "cause": type(error).__name__})
                        continue
                    walk(read, label, depth + 1)
        except zipfile.BadZipFile:
            gaps.append(
                {"source_member": name, "cause": "undecodable_original_zip", "sha256": digest(raw)}
            )

    walk(blob, "", 0)
    missing = by_hash - {l["source_member_sha256"] for l in leaves}
    if missing:
        raise ValueError("A decoded original member is missing from the archived container")
    return leaves, gaps


def pages_for(root, pkg, known, engine_hash):
    selected = {}
    members = []
    gaps = []
    ocr_receipts = {}
    for d in pkg["pdfs"]:
        leaf = d["pdf_sha256"]
        native = d["pages"]
        n = len(native) - int(bool(native) and not native[-1].strip())
        texts = {}
        if leaf in known:
            p = root / known[leaf]
            raw = p.read_bytes()
            r = json.loads(gzip.decompress(raw))
            if (
                r["pdf_sha256"] != leaf
                or r["method"] != VERSION
                or r["engine_sha256"] != engine_hash
            ):
                raise ValueError("Generic OCR belongs to another original or engine")
            texts = {int(i): t for i, t in r["pages"].items()}
            if any(i < 1 or i > n or len(native[i - 1].strip()) >= 50 for i in texts):
                raise ValueError("Generic page receipt has no matching sparse original page")
            ocr_receipts[leaf] = digest(raw)
        picks = []
        blank = []
        prior = False
        for i in range(1, n + 1):
            own = native[i - 1]
            opt = texts.get(i, "")
            if len(own.strip()) < 50 and len(opt.strip()) < 50:
                blank.append(i)
            picked, prior = nomination(own, opt, prior)
            if picked:
                picks.append(i)
        if leaf in selected and selected[leaf] != picks:
            raise ValueError("Page lists disagree for duplicated source member")
        selected[leaf] = picks
        member = {
            "pdf_sha256": leaf,
            "source_member": d.get("source_member"),
            "source_member_sha256": d.get("source_member_sha256", leaf),
            "source_media": d.get("source_media", "pdf"),
            "physical_pages": n,
            "selected_pages": len(picks),
            "generic_pages": len(texts),
            "sparse_pages_without_readable_text": blank,
        }
        members.append(member)
        if not n:
            gaps.append({"pdf_sha256": leaf, "cause": "zero_decoded_pages"})
        if blank:
            gaps.append(
                {"pdf_sha256": leaf, "cause": "sparse_native_and_optical_text", "pages": blank}
            )
    if not members:
        gaps.append({"cause": "zero_decoded_members"})
    if not any(selected.values()):
        gaps.append({"cause": "no_income_statement_page_nominated"})
    return selected, members, gaps, ocr_receipts


def control_results(root, jobs, source_sha):
    report = json.loads(checked_gzip(root / "xbrl-optical-editions" / (AUDIT + ".json.gz"), AUDIT))
    original_plan = json.loads(
        checked_gzip(root / "xbrl-optical-plans" / (report["plan"] + ".json.gz"), report["plan"])
    )
    if original_plan["source_edition"] != source_sha:
        return {
            "source_edition": original_plan["source_edition"],
            "status": "independent control belongs to another edition",
        }
    index = {j["url"]: j for j in jobs}
    reference = set()
    expected_pages = set()
    found = set()
    field_hits = []
    misses = []
    for row in report["records"]:
        raw = json.loads(checked_gzip(root / row["file"], row["receipt_sha256"]))
        url = raw["identity"]["original_url"]
        job = index.get(url)
        if (
            raw["identity"]["id"] != row["id"]
            or raw["identity"]["evaluator"] != report["evaluator"]
        ):
            raise ValueError("Optical control has the wrong filing")
        for p in raw["pages"]:
            reference.add((url, p["pdf_sha256"], p["page"]))
        for field, value in raw["values"].items():
            if value is None:
                continue
            proofs = [
                p
                for p in raw["observations"]
                if p["field"] == field and p.get("admissible", True) and float(p["value"]) == value
            ]
            if not proofs:
                raise ValueError("Control field lacks a positive original page")
            passed = False
            for p in proofs:
                sig = (url, p["pdf_sha256"], p["page"])
                expected_pages.add(sig)
                if job and p["page"] in job["selected_pages"].get(p["pdf_sha256"], []):
                    found.add(sig)
                    passed = True
                else:
                    misses.append({"id": row["id"], "field": field, "source_page": sig})
            field_hits.append(passed)
    n_ref_kept = sum(
        page in index[url]["selected_pages"].get(leaf, [])
        for url, leaf, page in reference
        if url in index
    )
    return {
        "status": "checked",
        "edition_sha256": AUDIT,
        "control_plan_sha256": report["plan"],
        "all_page_control_cases": len(report["records"]),
        "all_reference_pages": len(reference),
        "reference_pages_nominated": n_ref_kept,
        "non_null_control_fields": len(field_hits),
        "field_controls_nominated": sum(field_hits),
        "unique_positive_source_pages": len(expected_pages),
        "positive_source_pages_nominated": len(found),
        "misses": misses,
        "interpretation": "Recall is measured only against physically corroborated fields in the separate all-page control. Omitted pages elsewhere can contain unobserved fields or conflicts.",
    }


def build(root, ledger):
    selector_code = digest(Path(__file__).read_bytes())
    source, census, census_sha = edition(root, ledger)
    sign = source["frozen_ledger_inputs_sha256"]
    frozen = json.loads(checked_gzip(root / "ledger-inputs" / (sign + ".json.gz"), sign))
    for name, expected in {
        "attachment-plan.json": source["attachments_plan_sha256"],
        "dated-alias-attachment-plan.json": source["dated_alias_corpus_sha256"][
            "dated-alias-attachment-plan.json"
        ],
    }.items():
        if digest((root / name).read_bytes()) != expected:
            raise ValueError("Dated structured acquisition plan changed")
    plans = {r["id"]: r for r in load_plans(root)}
    native = {Path(p).name.split(".")[0]: p for p in frozen["native"]}
    optical = {Path(p).name.split(".")[0]: p for p in frozen["ocr"]}
    archive = {r["url"]: r for r in signed_rows(root, census["first_notice"])}
    struct = {r["url"]: r for r in frozen["structured"]}
    if len(archive) != len(native):
        raise ValueError("The original package inventory differs from the closed source census")
    reasons = Counter()
    groups = defaultdict(list)
    exclusions = []
    ids = set()
    for line in checked_gzip(root / "vintages" / (ledger + ".jsonl.gz"), ledger).splitlines():
        r = json.loads(line)
        c, reason = case(r, plans)
        if reason:
            reasons[reason] += 1
            continue
        u = r["first_notice"]["source_url"]
        if u not in archive:
            reasons["first_packet_unavailable"] += 1
            exclusions.append({"id": r["id"], "url": u, "cause": "first_packet_unavailable"})
            continue
        if struct.get(r["structured_url"], {}).get("sha256") != c["structured_sha256"]:
            raise ValueError("Structured first observation is not in the frozen source archive")
        if r["id"] in ids:
            raise ValueError("Duplicate first filing accession")
        if r["first_notice"].get("attachment_sha256") not in {None, archive[u]["sha256"]}:
            raise ValueError("Audited issuer bulletin refers to a different original")
        ids.add(r["id"])
        groups[u].append(c)
    jobs = []
    totals = Counter()
    optical_hashes = {}
    engine = digest(NATIVE.read_bytes())
    all_members = set()
    all_selected = set()
    all_phys = set()
    for u, cases in sorted(groups.items()):
        path = native[digest(u.encode())]
        packed = (root / path).read_bytes()
        body = gzip.decompress(packed)
        pkg = json.loads(body)
        if (
            pkg["source_url"] != u
            or pkg["sha256"] != archive[u]["sha256"]
            or digest(body) != archive[u]["text_sha256"]
        ):
            raise ValueError("Physical package differs from the closed official archive")
        selected, members, gaps, reads = pages_for(root, pkg, optical, engine)
        optical_hashes.update(reads)
        source_sha = pkg["sha256"]
        original = checked_gzip(
            root / "notice-objects" / source_sha[:2] / (source_sha + ".gz"), source_sha
        )
        leaves, original_gaps = source_members(original, members)
        gaps.extend(original_gaps)
        totals["original_container_leaves"] += len(leaves)
        totals["unaccounted_container_entries"] += len(original_gaps)
        cases.sort(key=lambda c: (c["release_at"], c["filing"]["id"]))
        totals["physical_pages_across_jobs"] += sum(m["physical_pages"] for m in members)
        totals["page_reads_nominated_across_jobs"] += sum(len(v) for v in selected.values())
        totals["members_across_jobs"] += len(members)
        totals["zero_nomination_packets"] += int(not any(selected.values()))
        totals["sparse_pages_without_readable_text"] += sum(
            len(m["sparse_pages_without_readable_text"]) for m in members
        )
        totals["zero_decoded_member_packets"] += int(not members)
        for m in members:
            h = m["pdf_sha256"]
            all_members.add(h)
            all_phys.update((h, i) for i in range(1, m["physical_pages"] + 1))
            all_selected.update((h, i) for i in selected[h])
        jobs.append(
            {
                "url": u,
                "package_path": path,
                "package_sha256": digest(packed),
                "original_sha256": pkg["sha256"],
                "cases": cases,
                "selected_pages": selected,
                "members": members,
                "original_container_members": leaves,
                "gaps": gaps,
            }
        )
    totals.update(
        {
            "eligible_accessions": len(ids),
            "eligible_issuer_quarter_basis": len(
                {
                    (c["filing"]["isin"], c["filing"]["period_end"], c["filing"]["basis"])
                    for j in jobs
                    for c in j["cases"]
                }
            ),
            "original_url_jobs": len(jobs),
            "distinct_physical_members": len(all_members),
            "distinct_physical_pages": len(all_phys),
            "distinct_nominated_pages": len(all_selected),
        }
    )
    manifest = {
        "selection_version": SELECTOR,
        "source_edition": ledger,
        "source_census_sha256": census_sha,
        "selector_sha256": selector_code,
        "frozen_ledger_inputs_sha256": sign,
        "structured_plans": {
            "primary": source["attachments_plan_sha256"],
            "dated_aliases": source["dated_alias_corpus_sha256"][
                "dated-alias-attachment-plan.json"
            ],
        },
        "complete_native_package_inventory": len(native),
        "complete_generic_ocr_member_inventory": len(optical),
        "native_inventory_sha256": census["first_notice"]["sha256"],
        "used_ocr_receipts_sha256": digest(encoded(optical_hashes)),
        "counts": dict(totals),
        "excluded_ledger_rows": dict(reasons),
        "controls": control_results(root, jobs, ledger),
        "limits": [
            "Page nomination consumes only the archived native text and original generic OCR. Empty, garbled and omitted pages can hide facts or contradictory values.",
            "A selected page is a proposed physical read. Issuer identity, source chronology, legal scope, explicitly printed units, value agreement and cross-page conflicts still require verification.",
        ],
    }
    plan = {**manifest, "jobs": jobs, "source_gaps": exclusions, "ocr_receipts": optical_hashes}
    if digest(Path(__file__).read_bytes()) != selector_code:
        raise ValueError("Nomination source program changed during construction")
    raw = encoded(plan)
    key = digest(raw)
    pack(root / "xbrl-optical-plans" / (key + ".json.gz"), raw)
    summary = {**manifest, "plan_sha256": key}
    write_once(root / "modern-optical-plan-summaries" / (key + ".json"), encoded(summary))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", required=True)
    print(json.dumps(build(ROOT, p.parse_args().ledger), indent=2))


if __name__ == "__main__":
    main()
