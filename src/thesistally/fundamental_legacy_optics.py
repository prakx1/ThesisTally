"""Validate a dated legacy results statement against its archived image and optical cells."""

import gzip
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from .discovery_registry import digest, encoded, write_once
from .fundamental_facts import FIELDS


def inputs(filing, package):
    return {
        "isin": filing["isin"],
        "period_end": filing["period_end"],
        "basis": filing["basis"],
        "structured_url": filing["source_url"],
        "structured_values": {k: filing.get(k) for k in FIELDS},
        "notice_url": package["source_url"],
        "package_sha256": package["sha256"],
        "source_members": sorted([d["pdf_sha256"], len(d["pages"])] for d in package["pdfs"]),
    }


@lru_cache(maxsize=1)
def extractor_version():
    return digest(Path(__file__).with_name("fundamental_legacy_raster.py").read_bytes())


def path_for(root, info):
    key = digest(encoded({"inputs": info, "extractor": extractor_version()}))
    return root / "legacy-original-receipts" / extractor_version() / key[:2] / (key + ".json")


def original_values(filing, source_proof):
    if any(source_proof.get(k) != filing[k] for k in ("basis", "period_end")):
        raise ValueError("Original optical field refers to another period or reporting basis")
    verified = {}
    for k in FIELDS:
        actual = source_proof["values"].get(k)
        independent = filing.get(k)
        if actual is not None:
            if independent is None or Decimal(str(actual)) != Decimal(str(independent)):
                raise ValueError("Original optical field differs from the independent table")
            verified[k] = actual
    return verified


def save_page(root, page, number, pdf_sha, png, raw_boxes, proof, filing):
    amounts = original_values(filing, proof)
    if not amounts:
        return None
    objects = root / "rendered-page-objects"
    psha, bsha = digest(png), digest(raw_boxes)
    write_once(objects / psha[:2] / (psha + ".png"), png)
    write_once(objects / bsha[:2] / (bsha + ".json.gz"), gzip.compress(raw_boxes, mtime=0))
    return {
        "pdf_sha256": pdf_sha,
        "page": page,
        "raster_size": number,
        "png_sha256": psha,
        "boxes_sha256": bsha,
        "original_observation": proof,
    }


def corroborate(root, filing, package, *, available=None):
    from .fundamental_legacy_raster import observe

    if not (filing.get("source_url") or "").lower().endswith(".html"):
        return None
    info = inputs(filing, package)
    positive = path_for(root, info)
    if (available is not None and positive.stem not in available) or not positive.is_file():
        return None
    raw = positive.read_bytes()
    witness = json.loads(raw)
    if witness["inputs"] != info or witness["extractor_sha256"] != extractor_version():
        raise ValueError("Legacy optical filing references the wrong original sources")
    objects = root / "rendered-page-objects"
    members = {d["pdf_sha256"]: len(d["pages"]) for d in package["pdfs"]}
    values = dict.fromkeys(FIELDS)
    evidence = {}
    for sample in witness["pages"]:
        sha = sample["png_sha256"]
        png = (objects / sha[:2] / (sha + ".png")).read_bytes()
        bsha = sample["boxes_sha256"]
        boxes = gzip.decompress((objects / bsha[:2] / (bsha + ".json.gz")).read_bytes())
        if (
            digest(png) != sha
            or digest(boxes) != bsha
            or not 1 <= sample["page"] <= members.get(sample["pdf_sha256"], 0)
        ):
            raise ValueError("Legacy statement optical evidence differs from its original page")
        observation = observe(filing, json.loads(boxes), image_bytes=png)
        if not observation or observation != sample["original_observation"]:
            raise ValueError("Cannot reproduce the original legacy quarter and data column")
        for k, v in original_values(filing, observation).items():
            values[k] = v
            evidence.setdefault(
                k,
                {
                    field: sample[field]
                    for field in ("pdf_sha256", "page", "png_sha256", "boxes_sha256", "raster_size")
                },
            )
    if not evidence:
        raise ValueError("Empty positive legacy financial statement certificate")
    primary = evidence.get("eps") or next(iter(evidence.values()))
    return {
        "method": "legacy_original_image_and_independent_same_quarter_table",
        "values": values,
        **primary,
        "field_page_evidence": evidence,
        "recognition_receipt_sha256": digest(raw),
        "receipt_key": positive.stem,
        "extractor_sha256": extractor_version(),
    }
