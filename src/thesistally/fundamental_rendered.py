"""Verify a printed basic EPS directly from the pixels of its original exchange page."""

import gzip
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from .discovery_registry import digest, encoded
from .fundamental_pdf_grid import _printed_date
from .fundamental_pdf_match import _legal_header

VERSION = "v1"
METHOD = "original_raster_basic_eps_and_exchange_structured_value"
_BASIC = re.compile(r"(?:\(?a\)?[.)|: -]*)?basic(?:\s+(?:eps|in rs\.?))?", re.IGNORECASE)
_GROUP = re.compile(
    r"(?:quarter|three months|3 months|six months|nine months|half year|year)(?: ended)?",
    re.IGNORECASE,
)


def image_date(text):
    if re.fullmatch(r"\d{1,2}[- ][A-Za-z]{3}[- ]\d{2}", text):
        try:
            return datetime.strptime(text.replace(" ", "-"), "%d-%b-%y").date()
        except ValueError:
            return None
    return _printed_date([text])


def statement_column(filing, boxes):
    """Locate a single first-quarter column using issuer, basis and the printed comparative date."""
    target = date.fromisoformat(filing["period_end"])
    titles = [b for b in boxes if "financial results" in b["text"].lower()]
    if not titles or not _legal_header(
        filing, [b["text"] for b in boxes if b["y"] > titles[0]["y"]]
    ):
        return None
    scopes = {
        t for b in titles for t in re.findall(r"\b(consolidated|standalone)\b", b["text"].lower())
    }
    if scopes != {filing["basis"]}:
        return None
    dates = [(b, image_date(b["text"])) for b in boxes if len(b["text"]) <= 24]
    dates = [(b, d) for b, d in dates if d and b["y"] < max(t["y"] for t in titles)]
    anchors = []
    for b, d in dates:
        if d != target:
            continue
        row = sorted(
            ((s, v) for s, v in dates if abs(s["y"] - b["y"]) < b["height"] * 0.65),
            key=lambda a: a[0]["x"],
        )
        if len(row) < 2 or row[0][0] != b or not target - timedelta(days=370) <= row[1][1] < target:
            continue
        groups = sorted(
            (g for g in boxes if 0 < g["y"] - b["y"] < 0.045 and _GROUP.fullmatch(g["text"])),
            key=lambda g: g["x"],
        )
        if not groups or groups[0]["text"].lower() not in {
            "quarter",
            "quarter ended",
            "3 months ended",
            "three months ended",
        }:
            continue
        x = b["x"] + b["width"] / 2
        other = row[1][0]
        radius = (other["x"] + other["width"] / 2 - x) / 2
        if radius > 0.015:
            anchors.append((b, x - radius, x + radius, groups[0]["text"]))
    return anchors[0] if len(anchors) == 1 else None


def cell_at(label, boxes, left, right):
    cells = [
        b
        for b in boxes
        if abs(b["y"] - label["y"]) < 0.55 * max(label["height"], b["height"])
        and left <= b["x"] + b["width"] / 2 < right
    ]
    return cells[0] if len(cells) == 1 else None


def selector(filing, boxes):
    """Require the issuer, period, accounting basis, column and basic-EPS row on the image."""
    anchor = statement_column(filing, boxes)
    if not anchor:
        return None
    header, left, right, duration = anchor
    sections = [
        b
        for b in boxes
        if b["x"] < left
        and b["y"] < header["y"]
        and re.search(r"earnings? per (?:equity )?share", b["text"], re.IGNORECASE)
    ]
    if len(sections) != 1:
        return None
    section = sections[0]
    labels = [
        b
        for b in boxes
        if b["x"] < left
        and 0 < section["y"] - b["y"] < 0.06
        and _BASIC.fullmatch(b["text"].strip())
    ]
    if len(labels) != 1:
        return None
    label = labels[0]
    nearby = " ".join(
        b["text"]
        for b in boxes
        if b["x"] < left and label["y"] - 0.001 <= b["y"] <= section["y"] + 0.001
    )
    if re.search(r"continuing|discontinued|attributable|USD|dollars?|ADR", nearby, re.IGNORECASE):
        return None
    cell = cell_at(label, boxes, left, right)
    if not cell:
        return None
    return {
        "period_label": header["text"],
        "duration_label": duration,
        "section": section["text"],
        "basic_label": label["text"],
        "cell": cell,
    }


def scalar(text):
    value = text.strip().replace("−", "-")
    if re.fullmatch(r"\(\d+(?:\.\d+)?\)", value):
        return -Decimal(value[1:-1])
    return Decimal(value) if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else None


def identity(row, release, package, text_proof, field):
    return {
        "isin": row["isin"],
        "basis": row["basis"],
        "period_end": row["period_end"],
        "source_url": release["source_url"],
        "source_sha256": package["sha256"],
        "pdf_sha256": text_proof["pdf_sha256"],
        "page": int(text_proof["page"]),
        "field": field,
        "structured_value": row.get(field),
    }


def receipt_path(root, key):
    return root / "notice-rendered-eps" / VERSION / key[:2] / (key + ".json")


def original_rendered_field(root, row, release, package, text_proof, field, *, available=None):
    if field != "eps" or row.get(field) is None:
        return None
    key_fields = identity(row, release, package, text_proof, field)
    key = digest(encoded(key_fields))
    if available is not None and key not in available:
        return None
    path = receipt_path(root, key)
    if not path.is_file():
        return None
    payload = path.read_bytes()
    proof = json.loads(payload)
    if proof["identity"] != key_fields or proof["method"] != METHOD:
        raise ValueError("Rendered fiscal observation belongs to another quarterly statement")
    objects = root / "rendered-page-objects"
    for name, suffix in (("rendered_page_sha256", ".png"), ("boxes_sha256", ".json.gz")):
        sha = proof[name]
        raw = (objects / sha[:2] / (sha + suffix)).read_bytes()
        if name == "boxes_sha256":
            raw = gzip.decompress(raw)
            boxes = json.loads(raw)
        if digest(raw) != sha:
            raise ValueError("Rendered fiscal evidence changed")
    cell = selector(row, boxes)
    if (
        not cell
        or cell != proof["observation"]
        or scalar(cell["cell"]["text"]) != Decimal(str(row[field]))
    ):
        raise ValueError(
            "Original basic EPS pixel evidence disagrees with the verified observation"
        )
    return {
        **proof,
        "pdf_sha256": key_fields["pdf_sha256"],
        "page": key_fields["page"],
        "value": float(scalar(cell["cell"]["text"])),
        "evidence_sha256": digest(payload),
    }
