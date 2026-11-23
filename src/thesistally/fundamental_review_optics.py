"""Verify original exchange result fields against a pinned independent quarterly XBRL."""

from __future__ import annotations

import gzip
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from . import fundamental_legacy_batch as batch
from . import fundamental_xbrl_subset as subsets
from .discovery_registry import digest, encoded, write_once
from .fundamental_facts import FIELDS
from .fundamental_legacy_raster import observe as grouped
from .fundamental_media import all_statement_members_readable, archive_documents
from .fundamental_native_rows import _page
from .fundamental_nse import pack
from .fundamental_ocr import layout
from .fundamental_rendered_build import environment
from .fundamental_rendered_money import observe as printed
from .fundamental_xbrl_math import operating_profit


@lru_cache(maxsize=1)
def evaluator():
    names = (
        "review_optics",
        "xbrl_math",
        "xbrl_subset",
        "legacy_raster",
        "native_rows",
        "ocr",
        "rendered_money",
        "rendered",
        "pdf_match",
        "currency",
    )
    code = {
        name: digest(Path(__file__).with_name("fundamental_" + name + ".py").read_bytes())
        for name in names
    }
    return digest(encoded({"code": code, "renderer": batch.profile()}))


def worker_tools():
    root, original, tools = environment()
    return original, batch.program(root), tools


def identity(filing, structured_sha256, original_url, original_sha256, release_at):
    return {
        "id": filing["id"],
        "isin": filing["isin"],
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "structured_url": filing["source_url"],
        "structured_sha256": structured_sha256,
        "structured_values": {k: filing[k] for k in FIELDS},
        "tags": filing["tags"],
        "original_url": original_url,
        "original_sha256": original_sha256,
        "release_at": release_at,
        "evaluator": evaluator(),
    }


def page_observations(filing, png, boxes):
    """Only scoped statutory cells; retain unequal values to expose contradictions."""
    found = []
    text = layout(boxes)
    arithmetic = operating_profit(filing, text)
    if arithmetic:
        found.append({**arithmetic, "layout_sha256": digest(text.encode())})
    native = _page(filing, text)
    if native:
        amounts, witnesses = native
        for field, value in amounts.items():
            found.append(
                {
                    "field": field,
                    "value": str(value),
                    "method": "optical_quarter_columns",
                    "evidence": witnesses[field],
                    "layout_sha256": digest(text.encode()),
                }
            )
    for field in FIELDS:
        observation = printed(filing, boxes, field)
        if not observation:
            continue
        found.append(
            {
                "field": field,
                "value": observation["value_inr"],
                "method": "optical_currency_and_cells",
                "admissible": observation["currency_explicit"],
                "evidence": observation,
            }
        )
    proof = grouped(filing, boxes, image_bytes=png)
    if proof:
        for field, value in proof["values"].items():
            if value is not None:
                found.append(
                    {
                        "field": field,
                        "value": str(value),
                        "method": "optical_grouped_statutory_columns",
                        "evidence": proof,
                    }
                )
    return found


def resolve(expected, observations, *, complete=True):
    values = dict.fromkeys(FIELDS)
    evidence, conflicts, misses = {}, {}, {}
    for field in FIELDS:
        observed = [x for x in observations if x["field"] == field and x.get("admissible", True)]
        if not observed:
            continue
        numbers = {Decimal(x["value"]) for x in observed}
        if len(numbers) != 1:
            conflicts[field] = sorted(map(str, numbers))
            continue
        amount = numbers.pop()
        if expected[field] is None or amount != Decimal(str(expected[field])):
            misses[field] = str(amount)
            continue
        if complete:
            values[field] = float(amount)
            evidence[field] = [x for x in observed if Decimal(x["value"]) == amount]
    return values, evidence, conflicts, misses


def assess(
    root,
    original_url,
    package_path,
    expected_package_sha256,
    cases,
    *,
    workers_tools,
    selected_pages=None,
    selection_version=None,
):
    from .fundamental_nse_ledger import parse_document

    relative = Path(package_path)
    path = (root / relative).resolve(strict=True)
    if path.parent.name not in {"notice-text", "notice-media"} or not path.is_relative_to(
        root.resolve()
    ):
        raise ValueError("Reviewed original is outside the signed exchange archive")
    raw = path.read_bytes()
    if digest(raw) != expected_package_sha256:
        raise ValueError("Reviewed original package fingerprint changed")
    package = json.loads(gzip.decompress(raw))
    if package["source_url"] != original_url:
        raise ValueError("Reviewed original URL differs from its marked announcement")
    sha = package["sha256"]
    orig = gzip.decompress((root / "notice-objects" / sha[:2] / (sha + ".gz")).read_bytes())
    if digest(orig) != sha:
        raise ValueError("Original exchange disclosure bytes changed")
    members = {
        d["pdf_sha256"]: len(d["pages"]) - int(bool(d["pages"]) and not d["pages"][-1].strip())
        for d in package["pdfs"]
    }
    scope = subsets.selection(members, selected_pages, selection_version)
    active = {}
    for case in cases:
        filing = case["filing"]
        if not filing["source_url"].lower().endswith(".xml"):
            raise ValueError("Reviewed result needs independently archived quarterly XBRL")
        raw_values, tags, structured_sha = parse_document(dict(filing))
        if (
            raw_values != {k: filing[k] for k in FIELDS}
            or tags != filing["tags"]
            or structured_sha != case["structured_sha256"]
        ):
            raise ValueError("Independent XBRL values or source identity differ from registration")
        info = identity(filing, structured_sha, original_url, sha, case["release_at"])
        active[digest(encoded({"identity": info, "selection": scope}))] = (filing, info, [])
    originals = {d["pdf_sha256"]: d for d in package["pdfs"]}
    old_program, vision, tools = workers_tools
    pages = []
    seen = set()
    unreadable = []
    if not all_statement_members_readable(orig):
        unreadable.append({"reason": "some_original_archive_members_are_unreadable"})
    objects = root / "rendered-page-objects"
    for meta, pdf in archive_documents(orig, root=root):
        member = meta["pdf_sha256"]
        if member not in originals or member in seen:
            continue
        seen.add(member)
        native = originals[member]["pages"]
        count = len(native) - int(bool(native) and not native[-1].strip())
        reads = (
            batch.read_pages(root, old_program, vision, pdf, member, count, tools)
            if selected_pages is None
            else subsets.read(root, old_program, vision, pdf, member, scope["pages"][member], tools)
        )
        for page, png, words, rendition, err in reads:
            if err is not None:
                unreadable.append({"pdf_sha256": member, "page": page, "returncode": err})
                continue
            psha, bsha = digest(png), digest(words)
            boxes = json.loads(words)
            sample = {
                "pdf_sha256": member,
                "page": page,
                "png_sha256": psha,
                "boxes_sha256": bsha,
                "raster_source": rendition,
            }
            pages.append(sample)
            for filing, _, observed in active.values():
                rows = page_observations(filing, png, boxes)
                if rows:
                    write_once(objects / psha[:2] / (psha + ".png"), png)
                    # Legacy and new render-cache namespaces both keep these canonical words already.
                    if (
                        digest(
                            gzip.decompress((objects / bsha[:2] / (bsha + ".json.gz")).read_bytes())
                        )
                        != bsha
                    ):
                        raise ValueError("Missing or changed original optical word receipt")
                    observed.extend({**row, **sample} for row in rows)
    if seen != set(originals):
        raise ValueError("A reviewed original PDF member was skipped")
    results = []
    for key, (_, info, observed) in sorted(active.items()):
        values, witness, conflicts, misses = resolve(
            info["structured_values"], observed, complete=not unreadable
        )
        body = {
            "identity": info,
            "selection": scope,
            "omitted_pages": sum(members.values())
            - len(pages)
            - sum("page" in e for e in unreadable),
            "tools": tools,
            "package_file": str(relative),
            "package_sha256": expected_package_sha256,
            "pages": pages,
            "unreadable_pages": unreadable,
            "observations": observed,
            "values": values,
            "conflicts": conflicts,
            "nonmatching_fields": misses,
        }
        data = encoded(body)
        fingerprint = digest(data)
        target = (
            root
            / "original-xbrl-optics"
            / evaluator()
            / key[:2]
            / (key + "." + fingerprint + ".json.gz")
        )
        pack(target, data)
        results.append(
            {
                "key": key,
                "id": info["id"],
                "base_key": digest(encoded(info)),
                "selection": scope,
                "omitted_pages": body["omitted_pages"],
                "receipt_sha256": fingerprint,
                "file": str(target.relative_to(root)),
                "values": values,
                "conflicts": conflicts,
                "nonmatching_fields": misses,
                "physical_pages": len(pages),
                "unreadable_pages": unreadable,
                "supporting_page_counts": {
                    k: len({(x["pdf_sha256"], x["page"]) for x in found})
                    for k, found in witness.items()
                },
            }
        )
    return results


def freeze(root):
    """Admit only finished, content-addressed cohorts to a numerical ledger."""
    records = {}
    paths = []
    for p in sorted((root / "xbrl-optical-completions").glob("*/*.json")):
        meta = json.loads(p.read_bytes())
        edition = p.stem
        if meta["edition_sha256"] != edition:
            raise ValueError("Wrong completed optical source edition")
        file = root / "xbrl-optical-editions" / (edition + ".json.gz")
        data = gzip.decompress(file.read_bytes())
        if digest(data) != edition:
            raise ValueError("Completed XBRL optical source edition changed")
        cohort = json.loads(data)
        if (cohort["plan"], cohort["evaluator"], cohort["cohort"], len(cohort["records"])) != (
            meta["plan"],
            meta["evaluator"],
            p.parent.name,
            meta["cases"],
        ):
            raise ValueError("Original-page completion disagrees with its source selection")
        if cohort["evaluator"] != evaluator():
            if edition == "fd10f4d2b9985fc82b3413d67b5e1f78c33fc4803436c653320edfefeb6e5873":
                # Preserved pilot run, before read-scope identities and indexed expenses.
                continue
            raise ValueError("Unsupported completed optical source evaluator")
        for r in cohort["records"]:
            base = r["base_key"]
            if r["key"] not in records.setdefault(base, {}):
                records[base][r["key"]] = r
            elif records[base][r["key"]] != r:
                raise ValueError("Conflicting reviewed optical source receipts")
        paths.append(str(p.relative_to(root)))
    return records, paths


def lookup(root, filing, sha, notice, package, records):
    if not records or not sha:
        return None
    info = identity(filing, sha, notice["source_url"], package["sha256"], notice["announced_at"])
    key = digest(encoded(info))
    refs = list(records.get(key, {}).values())
    if not refs:
        return None
    # A complete physical review takes precedence over its strict subsets.
    refs.sort(key=lambda x: (x["selection"]["mode"] != "all_pages", x["key"]))
    primary = None
    from .fundamental_field_review import merge_original_fields

    withheld = {
        field: {
            "reason": "incompatible_scoped_original_cells",
            "receipt": ref["receipt_sha256"],
            "amounts": amounts,
        }
        for ref in refs
        for field, amounts in ref["conflicts"].items()
    }
    blocked = set(withheld) | {k for r in refs for k in r["nonmatching_fields"]}
    for ref in refs:
        proof = _one(root, filing, info, ref, package)
        if proof:
            primary = merge_original_fields(primary, proof, source="reviewed_xbrl")
        if ref["selection"]["mode"] == "all_pages":
            break
    if not primary:
        return (
            {
                "values": dict.fromkeys(FIELDS),
                "field_page_evidence": {},
                "original_field_conflicts": withheld,
            }
            if withheld
            else None
        )
    for k in blocked:
        primary["values"][k] = None
        primary["field_page_evidence"].pop(k, None)
    primary["original_field_conflicts"] = withheld
    return primary if withheld or any(v is not None for v in primary["values"].values()) else None


def _one(root, filing, info, ref, package):
    if not any(v is not None for v in ref["values"].values()):
        return None
    file = (root / ref["file"]).resolve(strict=True)
    if not file.is_relative_to((root / "original-xbrl-optics").resolve()):
        raise ValueError("Review receipt left the signed original archive")
    data = gzip.decompress(file.read_bytes())
    body = json.loads(data)
    if (
        digest(data) != ref["receipt_sha256"]
        or body["identity"] != info
        or body["selection"] != ref["selection"]
        or digest(encoded({"identity": info, "selection": body["selection"]})) != ref["key"]
    ):
        raise ValueError("Reviewed XBRL source receipt changed")
    values, fields, conflicts, misses = resolve(
        info["structured_values"], body["observations"], complete=not body["unreadable_pages"]
    )
    if (
        values != ref["values"]
        or conflicts != ref["conflicts"]
        or misses != ref["nonmatching_fields"]
    ):
        raise ValueError("Reviewed optical result no longer reconciles")
    members = {
        d["pdf_sha256"]: len(d["pages"]) - int(bool(d["pages"]) and not d["pages"][-1].strip())
        for d in package["pdfs"]
    }
    indexed = {
        (p["pdf_sha256"], p["page"], p["png_sha256"], p["boxes_sha256"]) for p in body["pages"]
    }
    verified = {}
    for field, rows in fields.items():
        for witness in rows:
            psha, bsha = witness["png_sha256"], witness["boxes_sha256"]
            tag = psha, bsha
            if (
                not 1 <= witness["page"] <= members.get(witness["pdf_sha256"], 0)
                or (witness["pdf_sha256"], witness["page"], *tag) not in indexed
            ):
                raise ValueError("Reviewed optical evidence belongs to a different original page")
            if tag not in verified:
                base = root / "rendered-page-objects"
                png = (base / psha[:2] / (psha + ".png")).read_bytes()
                words = gzip.decompress((base / bsha[:2] / (bsha + ".json.gz")).read_bytes())
                if digest(png) != psha or digest(words) != bsha:
                    raise ValueError("Reviewed original pixels or words changed")
                verified[tag] = page_observations(filing, png, json.loads(words))
            if not any(
                all(witness[k] == v for k, v in o.items()) and o["field"] == field
                for o in verified[tag]
            ):
                raise ValueError("Original image field can no longer be reproduced")
    evidence = {
        k: {**rows[0], "receipt_sha256": ref["receipt_sha256"]} for k, rows in fields.items()
    }
    primary = evidence.get("eps") or next(iter(evidence.values()))
    return {
        "method": "original_optical_quarter_cells_corroborated_by_xbrl",
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "pdf_sha256": primary["pdf_sha256"],
        "page": primary["page"],
        "values": values,
        "field_page_evidence": evidence,
        "recognition_receipt_sha256": ref["receipt_sha256"],
        "cohort_receipt": ref["file"],
    }
