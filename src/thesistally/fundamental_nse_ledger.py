"""Publish immutable NSE first-issue facts and preserve every later structured vintage."""

from __future__ import annotations

import gzip
import json
import subprocess
from collections import Counter
from xml.etree import ElementTree as ET

from .fundamental_facts import FIELDS, html_facts, xml_facts
from .fundamental_notice_proof import (
    REVISION,
    audited_through,
    candidates,
    firsts,
    index_notices,
    merged_manifest,
)
from .fundamental_nse import ROOT, digest, write_json


def parse_document(row, receipts=None):
    url = row["source_url"]
    if not url:
        raise ValueError("missing_original_attachment")
    if receipts is None:
        path = ROOT / "receipts" / (digest(url.encode()) + ".json")
        source = json.loads(path.read_bytes()) if path.is_file() else None
    else:
        source = receipts.get(url)
    if source is None:
        raise ValueError("structured_attachment_unavailable")
    if source["url"] != url:
        raise ValueError("Structured result references another archived attachment")
    sha = source["sha256"]
    raw = gzip.decompress((ROOT / "objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(raw) != sha:
        raise ValueError("Archived attachment has changed")
    if cert := source.get("source_security_proof"):
        if (
            not cert["isin"] == row["isin"]
            or cert["period_end"] != row["period_end"]
            or not cert["exchange_accession"] == row["metadata"].get("seqNumber")
        ):
            raise ValueError("Recovered exchange path attached to another security or accession")
        row = {**row, "source_security_proof": cert}
    parsed, tags = (xml_facts if url.endswith(".xml") else html_facts)(raw, row)
    return parsed, tags, sha


def verify_notice(package, proof):
    """Replay the referenced page directly from the archived original PDF or ZIP."""
    sha = package["sha256"]
    raw = gzip.decompress((ROOT / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(raw) != sha:
        raise ValueError("Original notice hash mismatch")
    pdf_sha = proof["pdf_sha256"]
    from .fundamental_media import archive_documents

    member = next(
        (
            (info, pdf)
            for info, pdf in archive_documents(raw, root=ROOT)
            if info["pdf_sha256"] == pdf_sha
        ),
        None,
    )
    if member is None:
        raise ValueError("Financial statement is absent from its original attachment")
    info, pdf = member
    used = sorted({int(proof["page"]), *(int(k) for k in proof.get("pages_used", []))})
    hashes = {}
    archived = next(d for d in package["pdfs"] if d["pdf_sha256"] == pdf_sha)["pages"]
    for page in used:
        if page < 1 or page > len(archived):
            raise ValueError("Results evidence page outside the original")
        if not proof.get("recognition_receipt_sha256"):
            text = (
                subprocess.run(
                    ["pdftotext", "-layout", "-f", str(page), "-l", str(page), "-", "-"],
                    input=pdf,
                    capture_output=True,
                    check=True,
                    timeout=45,
                )
                .stdout.decode(errors="replace")
                .split("\f")[0]
            )
            if text != archived[page - 1]:
                raise ValueError("Extracted original page no longer matches its PDF")
            hashes[str(page)] = digest(text.encode())
    return {
        **info,
        "source_sha256": sha,
        "page": proof["page"],
        "pages_used": used,
        "native_page_text_sha256": hashes,
    }


def attest_release(
    row,
    release,
    values,
    structured_sha,
    tags,
    identities,
    verified_pages,
    native_paths,
    ocr_available,
    rendered_available,
    money_available,
    legacy_available,
    field_reviews,
    reviewed_optics=None,
):
    from .fundamental_identity import identity_witness
    from .fundamental_ocr import corroborate
    from .fundamental_pdf_continuations import corroborate_continuations
    from .fundamental_pdf_grid import corroborate_grid
    from .fundamental_pdf_match import extract_original, match

    if release.get("formal_original"):
        return {
            "method": "exchange_designated_original",
            "values": values,
            "notice_url": row["source_url"],
            "notice_sha256": structured_sha,
        }, None
    if release.get("release_language") == "legacy_results_summary" and not release.get(
        "source_url"
    ):
        from .fundamental_notice_language import inline_fact_proof

        proof = inline_fact_proof(release, row)
        if not proof:
            return None, "summary_facts_or_identity_unreconciled"
        return {
            **proof,
            "notice_url": release["catalogue_url"],
            "notice_sha256": release["catalogue_sha256"],
        }, None
    if REVISION.search(release["description"] + " " + release["text"]):
        return None, "earliest_notice_marked_revision"
    url = release.get("source_url") or ""
    path = native_paths.get(digest(url.encode()))
    if not path:
        return None, "first_release_attachment_unavailable"
    package = json.loads(gzip.decompress(path.read_bytes()))
    if release.get("attachment_sha256") and release["attachment_sha256"] != package["sha256"]:
        return None, "original_packet_differs_from_signed_case"
    if package["source_url"] != url:
        raise ValueError("Original disclosure proof is linked to another URL")
    request = {**row, **values, "tags": tags}
    matched = match(request, package["pdfs"]) if structured_sha else None
    if not matched and structured_sha:
        matched = corroborate_continuations(request, package["pdfs"])
    if not matched and structured_sha:
        matched = corroborate_grid(request, package["pdfs"])
    identity = structured_sha or release.get("isin") == row["isin"]
    dated_identity = None
    if not identity:
        dated_identity = identity_witness(row, release, *identities)
        identity = bool(dated_identity)
    if not matched and structured_sha:
        matched = corroborate(ROOT, request, package, url, available=ocr_available)
    if not matched and identity:
        matched = extract_original(request, package["pdfs"])
    native_cells = None
    if structured_sha and (
        not matched or matched.get("method") == "issuer_heading_dated_first_pdf_column"
    ):
        from .fundamental_native_rows import corroborate as native_corroboration
        from .fundamental_targeted_currency import corroborate_targeted_currency

        native_cells = native_corroboration(
            request, package["pdfs"]
        ) or corroborate_targeted_currency(
            {**request, "structured_sha256": structured_sha}, package["pdfs"], release, package
        )
    legacy = None
    if legacy_available and structured_sha:
        from .fundamental_legacy_optics import corroborate as legacy_corroboration

        legacy = legacy_corroboration(ROOT, request, package, available=legacy_available)
    reviewed = None
    if reviewed_optics and structured_sha:
        from .fundamental_xbrl_optics import lookup

        reviewed = lookup(ROOT, request, structured_sha, release, package, reviewed_optics)
    reviewed_positive = reviewed and any(v is not None for v in reviewed["values"].values())
    if not matched and not legacy and not native_cells and not reviewed_positive:
        from .fundamental_scope import corroborated_other

        if corroborated_other(release, package["pdfs"], row["basis"]):
            from .fundamental_media import all_statement_members_readable

            source_sha = package["sha256"]
            original = gzip.decompress(
                (ROOT / "notice-objects" / source_sha[:2] / (source_sha + ".gz")).read_bytes()
            )
            if digest(original) != source_sha:
                raise ValueError("The opposite-basis proof refers to a changed original source")
            if not all_statement_members_readable(original):
                return None, "unreadable_source_member_cannot_exclude_reporting_basis"
            return {
                "other_basis": release["declared_other_basis"],
                "notice_url": url,
                "notice_sha256": package["sha256"],
            }, None
        return None, "first_release_facts_unreconciled"

    def verify(proof):
        key = (
            package["sha256"],
            proof["pdf_sha256"],
            proof["page"],
            tuple(proof.get("pages_used", (proof["page"],))),
            bool(proof.get("recognition_receipt_sha256")),
        )
        if key not in verified_pages:
            verified_pages[key] = verify_notice(package, proof)
        return {**proof, **verified_pages[key]}

    if native_cells:
        for field in native_cells["field_page_evidence"].values():
            checked = verify(field)
            if (
                checked["native_page_text_sha256"].get(str(field["page"]))
                != field["page_text_sha256"]
            ):
                raise ValueError(
                    "A corroborated native cell references a different original PDF page"
                )
        native_cells = verify(native_cells)
    if legacy:
        for source in legacy["field_page_evidence"].values():
            verify({**source, "recognition_receipt_sha256": legacy["recognition_receipt_sha256"]})
        legacy = verify(legacy)
    if reviewed_positive:
        for source in reviewed["field_page_evidence"].values():
            verify({**source, "recognition_receipt_sha256": source["receipt_sha256"]})
        reviewed = verify(reviewed)
    if matched:
        matched = verify(matched)
    elif native_cells:
        matched, native_cells = native_cells, None
    elif legacy:
        matched, legacy = legacy, None
    else:
        matched, reviewed = reviewed, None
    if matched.get("method") == "issuer_heading_dated_first_pdf_column":
        from .fundamental_field_review import verify_text_fields

        accepted, checks = verify_text_fields(matched["values"], values, structured_sha)
        rejected = {k: v for k, v in checks.items() if accepted[k] is None}
        for field in rejected:
            witness = None
            if field == "eps" and rendered_available:
                from .fundamental_rendered import original_rendered_field

                witness = original_rendered_field(
                    ROOT, request, release, package, matched, field, available=rendered_available
                )
            if not witness and money_available:
                from .fundamental_money_witness import original_rendered_money

                witness = original_rendered_money(
                    ROOT, request, release, package, matched, field, available=money_available
                )
            if witness:
                accepted[field] = witness["value"]
                matched.setdefault("rendered_field_evidence", {})[field] = witness
                checks[field] = {
                    **checks[field],
                    "status": "confirmed_by_original_rendering",
                    "independently_rendered_value": witness["value"],
                }
        native_restorations = {}
        for field in rejected:
            if (
                accepted[field] is None
                and native_cells
                and native_cells["values"].get(field) is not None
            ):
                value = native_cells["values"][field]
                page = native_cells["field_page_evidence"][field]
                accepted[field] = value
                native_restorations[field] = page
                matched.setdefault("field_page_evidence", {})[field] = page
                checks[field] = {
                    **checks[field],
                    "status": "confirmed_by_independent_original_cell",
                    "verified_original_cell_value": value,
                }
        if rejected:
            field_reviews.append(
                {
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "isin": row["isin"],
                    "period_end": row["period_end"],
                    "basis": row["basis"],
                    "release_at": release["announced_at"],
                    "notice_url": url,
                    "notice_sha256": package["sha256"],
                    "structured_url": row["source_url"],
                    "pdf_sha256": matched["pdf_sha256"],
                    "page": matched["page"],
                    "page_text_sha256": matched["native_page_text_sha256"],
                    "rejected": rejected,
                    "native_cell_evidence": native_restorations,
                    "rendered_field_evidence": matched.get("rendered_field_evidence", {}),
                }
            )
        matched = {**matched, "values": accepted, "field_verification": checks}
    from .fundamental_field_review import merge_original_fields

    matched = merge_original_fields(
        {**matched, "values": matched.get("values", values)}, native_cells, source="native"
    )
    matched = merge_original_fields(matched, legacy)
    matched = merge_original_fields(matched, reviewed, source="reviewed_xbrl")
    if not any(v is not None for v in matched["values"].values()):
        return None, "no_independently_corroborated_original_text_fields"
    if dated_identity:
        matched["dated_security_identity"] = dated_identity
    return {
        "method": "original_notice_matches_all_four_structured_facts",
        **matched,
        "values": matched.get("values", values),
        "notice_url": url,
        "notice_sha256": package["sha256"],
    }, None


def publish():
    from functools import partial

    from .fundamental_nse import load_plans
    from .fundamental_timeline import first_fact_timeline

    plans = load_plans(ROOT)
    manifest = merged_manifest()
    from .fundamental_identity import load as load_identities

    identities = load_identities()
    from .fundamental_media_recovery import notice_packages
    from .fundamental_nse import pack
    from .fundamental_ocr import VERSION

    native_paths = {p.name.split(".")[0]: p for p in notice_packages(ROOT)}
    ocr_paths = sorted((ROOT / "notice-ocr" / VERSION).glob("*/*.json.gz"))
    ocr_available = {p.name.split(".")[0] for p in ocr_paths}
    rendered_paths = sorted((ROOT / "notice-rendered-eps/v1").glob("*/*.json"))
    rendered_available = {p.stem for p in rendered_paths}
    money_paths = sorted((ROOT / "notice-rendered-money/v2").glob("*/*.json"))
    money_available = {p.stem for p in money_paths}
    from .fundamental_legacy_optics import extractor_version

    legacy_paths = sorted(
        (ROOT / "legacy-original-receipts" / extractor_version()).glob("*/*.json")
    )
    legacy_available = {p.stem for p in legacy_paths}
    from .fundamental_xbrl_optics import freeze

    reviewed_optics, reviewed_paths = freeze(ROOT)
    structured_receipts = {}
    for path in sorted((ROOT / "receipts").glob("*.json")):
        source = json.loads(path.read_bytes())
        if path.stem != digest(source["url"].encode()):
            raise ValueError("Structured accession receipt is filed under another URL")
        structured_receipts[source["url"]] = source
    inputs = {
        "structured": [structured_receipts[url] for url in sorted(structured_receipts)],
        "native": sorted(str(p.relative_to(ROOT)) for p in native_paths.values()),
        "ocr": sorted(str(p.relative_to(ROOT)) for p in ocr_paths),
        "rendered_eps": sorted(str(p.relative_to(ROOT)) for p in rendered_paths),
        "rendered_money": sorted(str(p.relative_to(ROOT)) for p in money_paths),
        "reviewed_xbrl_optical_editions": reviewed_paths,
        "legacy_original_optics": sorted(str(p.relative_to(ROOT)) for p in legacy_paths),
    }
    inputs_raw = (json.dumps(inputs, sort_keys=True, separators=(",", ":")) + "\n").encode()
    inputs_sha = digest(inputs_raw)
    pack(ROOT / "ledger-inputs" / (inputs_sha + ".json.gz"), inputs_raw)
    from .fundamental_notice_only import supplement

    notices = index_notices()
    plans = [*plans, *supplement(plans, notices)]
    rows = []
    verified_pages = {}
    field_reviews = []
    counts = Counter()
    unresolved_probes = Counter()
    probe_gaps = []
    for group in firsts(plans).values():
        earliest = group[0]
        # Duplicate annual/quarterly entries can reference one original instance.
        earliest_url = earliest.get("source_url")
        for row in group:
            reason = None
            values = dict.fromkeys(FIELDS)
            tags = {}
            sha = None
            try:
                values, tags, sha = parse_document(row, structured_receipts)
            except (ValueError, ET.ParseError, KeyError) as e:
                reason = str(e)[:180]
            m = row["metadata"]
            record = {
                k: row[k]
                for k in (
                    "id",
                    "isin",
                    "symbol",
                    "basis",
                    "period_end",
                    "filed_at",
                    "source_url",
                    "catalogue_url",
                )
            }
            record["identity"] = row.get("identity")
            record["catalogue_isin"] = m.get("isin")
            record["structured_url"] = row["source_url"]
            raw_source = structured_receipts.get(row["source_url"]) or {}
            record["structured_document_sha256"] = raw_source.get("sha256")
            record["structured_retrieved_url"] = raw_source.get("retrieved_url") or raw_source.get(
                "url"
            )
            record["structured_sha256"] = sha
            record["structured_values"] = values
            record["structured_tags"] = tags
            record["structured_parse_gap"] = reason
            release = None
            found = None
            timeline = None
            stamp = row["filed_at"]
            status = "uncertified"
            record["exchange_filing_type"] = m.get("type_Sub")
            record["exchange_revision_remark"] = m.get("revision_Remark") or m.get(
                "resultDescription"
            )
            if m.get("type_Sub") == "Revision" or REVISION.search(m.get("resultDescription") or ""):
                status = "revision"
                reason = reason or "exchange_labels_revision"
            elif row["filed_at"] != earliest["filed_at"] and (
                not earliest_url or row.get("source_url") != earliest_url
            ):
                status = "subsequent_filing"
                reason = reason or "subsequent_structured_vintage"
            else:
                history = manifest.get(row["symbol"], {})
                if (
                    history.get("error")
                    or not history.get("total")
                    or history.get("from_date", "2011-01-01") > row["period_end"]
                ):
                    reason = "unknown_issuer_announcement_archive"
                else:
                    prior = candidates(row, notices[row["symbol"]])
                    formal = None
                    if m.get("type_Sub") == "Original" and sha:
                        formal = {"formal_original": True, "announced_at": row["filed_at"]}
                    if prior or formal:
                        release = prior[0] if prior else None

                        attest = partial(
                            attest_release,
                            row,
                            values=values,
                            structured_sha=sha,
                            tags=tags,
                            identities=identities,
                            verified_pages=verified_pages,
                            native_paths=native_paths,
                            ocr_available=ocr_available,
                            rendered_available=rendered_available,
                            money_available=money_available,
                            legacy_available=legacy_available,
                            field_reviews=field_reviews,
                            reviewed_optics=reviewed_optics,
                        )
                        timeline, reason = first_fact_timeline(
                            prior, attest, formal, audited_through=audited_through(history, row)
                        )
                        if timeline:
                            release = timeline["editions"][0][0]
                            if release.get("formal_original"):
                                release = None
                            found = timeline["evidence"]
                            values = timeline["values"]
                            sha = found["notice_sha256"]
                            stamp = timeline["announced_at"]
                            record["source_url"] = found["notice_url"]
                            tags = {
                                k: "first_publication" for k, v in values.items() if v is not None
                            }
                            status = "original"
                            reason = None
                    else:
                        reason = reason or "original_release_unverified"
            if status == "original" and not sha:
                raise AssertionError("An original needs its source object")
            record.update(
                exchange="NSE",
                announced_at=stamp,
                first_reported=status == "original",
                filing_status=status,
                gap=reason,
                sha256=sha,
                fact_tags=tags,
                announcement_catalogue=manifest.get(row["symbol"], {}).get("url"),
                first_notice=release,
                notice_proof=found,
                **values,
            )
            if timeline:
                record.update(
                    {
                        k: timeline[k]
                        for k in (
                            "initial_disclosure_at",
                            "eps_absence_from",
                            "eps_absence_before",
                            "eps_absence_evidence",
                            "fact_announced_at",
                            "fact_sources",
                            "complete_available_at",
                            "remaining_fields_gap",
                            "other_basis_documents",
                        )
                    }
                )
                if len(timeline["editions"]) > 1 or any(
                    ev.get("method") == "exchange_announcement_body_explicit_current_quarter"
                    for _, ev in timeline["editions"]
                ):
                    for index, (event, proof) in enumerate(timeline["editions"], 1):
                        kind = (
                            "initial_summary"
                            if proof.get("method")
                            == "exchange_announcement_body_explicit_current_quarter"
                            else "original_statement_edition"
                        )
                        fields = {k: v for k, v in proof["values"].items() if v is not None}
                        edition = {
                            **record,
                            **dict.fromkeys(FIELDS),
                            **fields,
                            "id": record["id"] + f"/edition-{index}",
                            "original_record": record["id"],
                            "announced_at": event["announced_at"],
                            "filed_at": event["announced_at"],
                            "source_url": proof["notice_url"],
                            "sha256": proof["notice_sha256"],
                            "notice_proof": proof,
                            "filing_status": kind,
                            "first_reported": False,
                            "gap": None,
                            "first_notice": event,
                            "fact_tags": {k: "published_in_this_edition" for k in fields},
                            "original_fields": [
                                k
                                for k in fields
                                if timeline["fact_announced_at"].get(k) == event["announced_at"]
                            ],
                            "fact_announced_at": {k: event["announced_at"] for k in fields},
                            "fact_sources": {
                                k: {
                                    "source_url": proof["notice_url"],
                                    "sha256": proof["notice_sha256"],
                                }
                                for k in fields
                            },
                            "complete_available_at": event["announced_at"]
                            if len(fields) == len(FIELDS)
                            else None,
                        }
                        rows.append(edition)
                        counts[kind] += 1
            if row["id"].startswith("notice/") and status != "original":
                unresolved_probes[reason or status] += 1
                probe_gaps.append(
                    {
                        **{
                            k: record[k]
                            for k in (
                                "id",
                                "isin",
                                "symbol",
                                "period_end",
                                "basis",
                                "announced_at",
                                "structured_url",
                                "structured_parse_gap",
                                "gap",
                            )
                        },
                        "first_notice_url": (release or {}).get("source_url"),
                        "first_notice_at": (release or {}).get("announced_at"),
                    }
                )
            else:
                if reason:
                    counts[reason] += 1
                counts[status] += 1
                rows.append(record)
                later_values = record["structured_values"]
                changes = {
                    k: {"first": values[k], "structured": later_values[k]}
                    for k in FIELDS
                    if values[k] is not None
                    and later_values[k] is not None
                    and values[k] != later_values[k]
                }
                if status == "original" and found and changes:
                    # These are genuine separately timestamped observations. The reason
                    # (different precision, correction, restatement) is often unstated.
                    later = {
                        **record,
                        **later_values,
                        "id": record["id"] + "/structured-version",
                        "source_url": record["structured_url"],
                        "sha256": record["structured_sha256"],
                        "announced_at": record["filed_at"],
                        "first_reported": False,
                        "fact_tags": record.get("structured_tags", {}),
                        "filing_status": "subsequent_filing",
                        "gap": "values_differ_from_original_notice",
                        "original_record": record["id"],
                        "differences": changes,
                        "notice_proof": None,
                        "first_notice": None,
                        "fact_announced_at": {
                            k: record["filed_at"] for k, v in later_values.items() if v is not None
                        },
                        "complete_available_at": record["filed_at"]
                        if all(v is not None for v in later_values.values())
                        else None,
                        "fact_sources": {
                            k: {
                                "source_url": record["structured_url"],
                                "sha256": record["structured_sha256"],
                            }
                            for k, v in later_values.items()
                            if v is not None
                        },
                    }
                    rows.append(later)
                    counts["subsequent_filing"] += 1
                    counts["values_differ_from_original_notice"] += 1
    known_urls = {source["url"] for source in inputs["structured"]}
    if any(
        rec.get("structured_url") in known_urls
        and rec.get("structured_parse_gap") == "structured_attachment_unavailable"
        for rec in rows
    ):
        raise RuntimeError(
            "An archived structured attachment was reported missing in this ledger edition"
        )
    rows.sort(key=lambda r: (r["isin"], r["period_end"], r["basis"], r["filed_at"], r["id"]))

    def lines(records):
        return b"".join(
            (
                json.dumps(
                    r, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                + "\n"
            ).encode()
            for r in records
        )

    raw = lines(rows)
    sha = digest(raw)
    snapshot = ROOT / "vintages" / (sha + ".jsonl.gz")
    from .fundamental_nse import pack

    pack(snapshot, raw)
    views = {}
    for name, statuses in [
        ("first-reported", {"original"}),
        ("later-vintages", {"subsequent_filing", "revision"}),
        ("source-editions", {"initial_summary", "original_statement_edition"}),
    ]:
        subset = [r for r in rows if r["filing_status"] in statuses]
        content = lines(subset)
        view_hash = digest(content)
        dest = ROOT / "editions" / (name + "-" + view_hash + ".jsonl.gz")
        pack(dest, content)
        views[name] = {
            "sha256": view_hash,
            "records": len(subset),
            "file": str(dest.relative_to(ROOT)),
        }
    # This is an index of the immutable snapshot. No historical vintage is updated in place.
    tmp = ROOT / "filings.jsonl.part"
    tmp.write_bytes(raw)
    tmp.replace(ROOT / "filings.jsonl")
    write_json(ROOT / "notice-probe-gaps.json", probe_gaps)
    review_raw = (json.dumps(field_reviews, sort_keys=True, separators=(",", ":")) + "\n").encode()
    review_sha = digest(review_raw)
    pack(ROOT / "field-reviews" / (review_sha + ".json.gz"), review_raw)
    originals = [r for r in rows if r["first_reported"]]
    reporting_methods = Counter(
        (r.get("notice_proof") or {}).get("method")
        or (
            "original_notice_matches_all_four_structured_facts"
            if r.get("notice_proof")
            else "exchange_designated_original"
        )
        for r in originals
    )
    write_json(
        ROOT / "filings-receipt.json",
        {
            "sha256": sha,
            "records": len(rows),
            "direct_pdf_numeric_policy": "independent_field_agreement_or_validated_original_rendering_v1",
            "field_reviews": {
                "sha256": review_sha,
                "file": "field-reviews/" + review_sha + ".json.gz",
                "observations": len(field_reviews),
                "rendered_field_restorations": dict(
                    Counter(k for r in field_reviews for k in r["rendered_field_evidence"])
                ),
                "native_cell_restorations": dict(
                    Counter(k for r in field_reviews for k in r["native_cell_evidence"])
                ),
                "remaining_unknown_fields": dict(
                    Counter(
                        k
                        for r in field_reviews
                        for k in r["rejected"]
                        if k not in r["rendered_field_evidence"]
                        and k not in r["native_cell_evidence"]
                    )
                ),
                "reasons": dict(
                    Counter(f["status"] for v in field_reviews for f in v["rejected"].values())
                ),
            },
            "editions": views,
            "original_source_methods": dict(reporting_methods),
            "certified_original_values": {
                k: sum(r[k] is not None for r in originals) for k in FIELDS
            },
            "source_definition": {
                "currency": "INR absolute, per-share INR for EPS. Old exchange HTML monetary tables are converted from lakhs.",
                "revenue": "Operating revenue for industrial and NBFC filings. Total income in banking statements; net premium income when the insurance statement reports it.",
                "operating_profit": "Explicit operating profit if supplied, including statutory pre-provision operating profit for banks. For non-banks and NBFCs use operating revenue less expenses plus finance costs if each figure is present (EBIT). An insurer's policyholder-account surplus is not classed as operating profit.",
                "net_profit": "Total profit after tax for the quarter, including discontinued operations where reported, before allocating the total to parent and noncontrolling holders.",
                "eps": "Basic EPS after extraordinary items or inclusive of continuing and discontinued operations. Explicit combined basic and diluted EPS is accepted where the issuer reports that single figure. Never use diluted alone or year-to-date values.",
            },
            "counts": dict(counts),
            "unresolved_notice_only_probes": dict(unresolved_probes),
            "notice_probe_gaps_sha256": digest((ROOT / "notice-probe-gaps.json").read_bytes()),
            "dated_alias_corpus_sha256": {
                p.name: digest(p.read_bytes()) for p in ROOT.glob("dated-alias-*.json")
            },
            "attachments_plan_sha256": digest((ROOT / "attachment-plan.json").read_bytes()),
            "frozen_ledger_inputs_sha256": inputs_sha,
            "notices_sha256": digest((ROOT / "notice-census.json").read_bytes()),
            "notice_manifest_sha256": digest((ROOT / "notice-manifest.json").read_bytes()),
            "supplemental_sha256": {
                p.name: digest(p.read_bytes())
                for p in (
                    ROOT / filename
                    for filename in [
                        "notice-fallback-manifest.json",
                        "notice-fallback-rows.json",
                        "notice-fallback-sources.json",
                        "notice-market-manifest.json",
                        "notice-market-rows.json",
                        "notice-market-sources.json",
                        "notice-market-extra-plan.json",
                        "notice-language-manifest.json",
                        "notice-language-rows.json",
                        "notice-language-identical-manifest.json",
                        "notice-language-identical-rows.json",
                        "notice-alias-manifest.json",
                        "notice-alias-rows.json",
                        "notice-case-manifest.json",
                        "notice-case-rows.json",
                        "notice-formal-review-rows.json",
                        "notice-formal-review-manifest.json",
                        "notice-extra-category-rows.json",
                        "notice-extra-category-manifest.json",
                        "identity-bhavcopies.json",
                    ]
                )
                if p.exists()
            },
        },
    )
    print(len(rows), counts)


if __name__ == "__main__":
    from .fundamental_sources import run

    run("ledger")
