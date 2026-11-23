import copy

import numpy as np
import pytest

from thesistally import fundamental_legacy_raster as raster


def b(text, x, y, width=0.065, height=0.014):
    return {"text": text, "x": x, "y": y, "width": width, "height": height}


@pytest.fixture
def statement(monkeypatch):
    gray = np.full((1200, 1200), 255, np.uint8)
    # Boundaries of the issuer's three-column standalone block, next to another block.
    for x in (0.35, 0.80, 0.97):
        gray[80:1100, int(x * 1200)] = 0
    for x in (0.47, 0.57, 0.69):
        gray[200:1100, int(x * 1200)] = 0
    monkeypatch.setattr(raster, "_gray", lambda _: gray)
    boxes = [
        b("Example Limited", 0.42, 0.97),
        b("Statement of quarterly financial results", 0.06, 0.94, 0.56),
        b("(INR in crores)", 0.79, 0.925),
        b("standalone", 0.53, 0.88, 0.09),
        b("consolidated", 0.84, 0.88),
        b("3 months ended", 0.364, 0.82, 0.09),
        b("Year ended", 0.578, 0.82, 0.078),
        b("31.03.2015", 0.37, 0.77),
        b("31.03.2014", 0.49, 0.77),
        b("31.03.2015", 0.59, 0.77),
        b("31.03.2014", 0.70, 0.77),
        b("31.03.2015", 0.83, 0.77),
        b("Total revenue from operations", 0.07, 0.72, 0.25),
        b("100.91", 0.375, 0.72),
        b("Profit from operations before other income,", 0.07, 0.65, 0.25),
        b("finance costs and exceptional items", 0.07, 0.63, 0.25),
        b("10.49", 0.375, 0.64),
        b("Net profit for the period (9-10)", 0.07, 0.57, 0.25),
        b("(3.14)", 0.375, 0.57),
        b("Earnings per equity share (in INR)", 0.07, 0.50, 0.25),
        b("Basic", 0.07, 0.46),
        b("(0.25)", 0.375, 0.46),
        b("Diluted", 0.07, 0.43),
        b("(0.25)", 0.375, 0.43),
        b("100.91", 0.598, 0.72),
        b("(3.14)", 0.84, 0.57),
    ]
    return (
        {
            "metadata": {"companyName": "Example Limited"},
            "basis": "standalone",
            "period_end": "2015-03-31",
            "revenue": 1_009_100_000,
            "operating_profit": 104_900_000,
            "net_profit": -31_400_000,
            "eps": -0.25,
        },
        boxes,
        gray,
    )


def test_quarter_under_printed_merged_basis(statement):
    filing, boxes, _ = statement
    o = raster.observe(filing, boxes, image_bytes=b"test")
    assert o["values"] == {
        k: filing[k] for k in ("revenue", "operating_profit", "net_profit", "eps")
    }
    assert o["cells"]["operating_profit"]["cell"]["text"] == "10.49"
    assert abs(o["anchor"]["column_grid"][1] - 0.47) < 0.002


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_issuer",
        "wrong_period",
        "wrong_scope",
        "missing_grid",
        "not_quarter",
        "no_comparative",
        "no_image",
    ],
)
def test_ambiguous_group_withholds_all_fields(statement, mutation):
    filing, boxes, gray = statement
    image = b"test"
    if mutation == "wrong_issuer":
        filing["metadata"]["companyName"] = "Other Company Limited"
    if mutation == "wrong_period":
        filing["period_end"] = "2016-03-31"
    if mutation == "wrong_scope":
        filing["basis"] = "consolidated"
    if mutation == "missing_grid":
        gray[:] = 255
    if mutation == "not_quarter":
        next(b for b in boxes if b["text"] == "3 months ended")["text"] = "12 months ended"
    if mutation == "no_comparative":
        boxes[:] = [b for b in boxes if b["text"] != "31.03.2014"]
    if mutation == "no_image":
        image = None
    assert raster.observe(filing, boxes, image_bytes=image) is None


@pytest.mark.parametrize("legend", ["(in crores)", "(USD in crores)", "other_basis"])
def test_separate_amount_and_per_share_currency(statement, legend):
    filing, boxes, _ = statement
    unit = next(b for b in boxes if b["text"] == "(INR in crores)")
    if legend == "other_basis":
        unit.update(x=0.83, y=0.881)
    else:
        unit["text"] = legend
    o = raster.observe(filing, boxes, image_bytes=b"test")
    assert o["values"] == {
        "revenue": None,
        "operating_profit": None,
        "net_profit": None,
        "eps": -0.25,
    }
    cap = next(b for b in boxes if b["text"] == "Earnings per equity share (in INR)")
    cap["text"] = "Earnings per equity share (of INR 1 each)"
    assert raster.observe(filing, boxes, image_bytes=b"test") is None


def test_current_number_must_agree_without_searching_comparatives(statement):
    filing, boxes, _ = statement
    wrong = next(b for b in boxes if b["text"] == "100.91" and b["x"] < 0.4)
    wrong["text"] = "91.70"
    o = raster.observe(filing, boxes, image_bytes=b"test")
    assert o["values"]["revenue"] is None and o["values"]["net_profit"] == filing["net_profit"]
    boxes.append(copy.deepcopy(next(b for b in boxes if b["text"] == "(3.14)" and b["x"] < 0.4)))
    assert raster.observe(filing, boxes, image_bytes=b"test")["values"]["net_profit"] is None
