import gzip
from decimal import Decimal

import pytest

from thesistally.discovery_registry import digest, encoded, write_once
from thesistally.fundamental_rendered import (
    METHOD,
    identity,
    original_rendered_field,
    receipt_path,
    scalar,
    selector,
)


def box(text, x, y, width=0.04):
    return {"text": text, "x": x, "y": y, "width": width, "height": 0.009}


ROW = {
    "isin": "INE918I01026",
    "period_end": "2025-09-30",
    "basis": "standalone",
    "eps": 6.8,
    "metadata": {"companyName": "Bajaj Finserv Limited"},
}
# Normalized image coordinates reproduce the ordering in the 30 Sep 2025 original.
BOXES = [
    box("Bajaj Finserv Limited", 0.3, 0.9),
    box("Statement of standalone financial results for the quarter", 0.1, 0.8),
    box("Quarter ended", 0.52, 0.78),
    box("Half year ended", 0.77, 0.78),
    box("30.09.2025", 0.46, 0.76),
    box("30.06.2025", 0.55, 0.76),
    box("Earnings per share", 0.1, 0.37),
    box("Basic", 0.1, 0.358),
    box("Diluted", 0.1, 0.345),
    box("6.8", 0.49, 0.358),
    box("6.7", 0.49, 0.345),
    box("68", 0.59, 0.358),
]


def test_optical_basic_eps_requires_exact_scope_period_and_column():
    assert selector(ROW, BOXES)["cell"]["text"] == "6.8"
    assert selector({**ROW, "basis": "consolidated"}, BOXES) is None
    assert selector({**ROW, "period_end": "2025-06-30"}, BOXES) is None
    assert selector(ROW, [b for b in BOXES if b["text"] != "6.8"]) is None
    assert selector(ROW, [*BOXES, box("continuing operations", 0.15, 0.364)]) is None
    assert selector(ROW, [*BOXES, box("-", 0.47, 0.358)]) is None
    assert scalar("8,34") is None
    assert scalar("(8.34)") == Decimal("-8.34")


def test_pixel_receipt_is_pinned_to_source_inputs_and_both_archived_objects(tmp_path):
    fields = (
        {"source_url": "official"},
        {"sha256": "original"},
        {"pdf_sha256": "page source", "page": 3},
        "eps",
    )
    info = identity(ROW, *fields)
    key = digest(encoded(info))
    boxes = encoded(BOXES)
    png = b"original image bytes"
    for data, suffix, stored in ((boxes, ".json.gz", gzip.compress(boxes)), (png, ".png", png)):
        sha = digest(data)
        write_once(tmp_path / "rendered-page-objects" / sha[:2] / (sha + suffix), stored)
    write_once(
        receipt_path(tmp_path, key),
        encoded(
            {
                "identity": info,
                "method": METHOD,
                "boxes_sha256": digest(boxes),
                "rendered_page_sha256": digest(png),
                "observation": selector(ROW, BOXES),
            }
        ),
    )
    assert original_rendered_field(tmp_path, ROW, *fields, available=set()) is None
    assert original_rendered_field(tmp_path, ROW, *fields, available={key})["value"] == 6.8
    assert original_rendered_field(tmp_path, {**ROW, "eps": 68}, *fields) is None
    sha = digest(png)
    (tmp_path / "rendered-page-objects" / sha[:2] / (sha + ".png")).write_bytes(b"changed pixels")
    with pytest.raises(ValueError, match="changed"):
        original_rendered_field(tmp_path, ROW, *fields)
