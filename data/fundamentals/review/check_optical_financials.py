"""Independently reproduce modern original-page evidence using archived source bytes."""

import argparse
import gzip
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from functools import lru_cache

from reconcile import METRICS, F, original_document, read_amount, sha

N = F / "nse"
OBJECTS = N / "rendered-page-objects"


def exact(path, expected, compressed=False):
    raw = path.read_bytes()
    if compressed:
        raw = gzip.decompress(raw)
    assert sha(raw) == expected, path
    return raw


@lru_cache(maxsize=128)
def original_rgb_digest(source, document, number, size):
    original = original_document(source, document)
    original_ppm = subprocess.run(
        [
            "pdftoppm",
            "-q",
            "-f",
            str(number),
            "-l",
            str(number),
            "-singlefile",
            "-scale-to",
            str(size),
            "-",
        ],
        input=original,
        check=True,
        capture_output=True,
        timeout=420,
    ).stdout
    magic = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", original_ppm)
    assert magic and len(original_ppm[magic.end() :]) == int(magic[1]) * int(magic[2]) * 3
    return sha(original_ppm[magic.end() :])


@lru_cache(maxsize=128)
def verify_page(source, document, number, png_sha, boxes_sha, rgb_sha, size):
    original = original_rgb_digest(source, document, number, size)
    png = exact(OBJECTS / png_sha[:2] / (png_sha + ".png"), png_sha)
    captured = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        input=png,
        check=True,
        capture_output=True,
        timeout=90,
    ).stdout
    assert original == sha(captured)
    if rgb_sha:
        assert rgb_sha == original
    return json.loads(exact(OBJECTS / boxes_sha[:2] / (boxes_sha + ".json.gz"), boxes_sha, True))


def layout(boxes):
    groups = []
    for b in sorted(boxes, key=lambda b: (-b["y"], b["x"])):
        if not groups or abs(groups[-1][0]["y"] - b["y"]) > 0.55 * min(
            b["height"], groups[-1][0]["height"]
        ):
            groups.append([])
        groups[-1].append(b)
    result = []
    for group in groups:
        line = ""
        for b in sorted(group, key=lambda b: b["x"]):
            line += " " * max(2 * bool(line), round(b["x"] * 185) - len(line)) + b["text"]
        result.append(line)
    return "\n".join(result)


def calendar_cell(field, proof, lines, basis, period, arithmetic):
    assert proof["scope"] == basis and proof["quarter"]["dates"][0] == period
    expanded = " ".join(" ".join(lines).split())
    assert (
        proof["statement_heading"] in "\n".join(lines)
        and " ".join(proof["issuer_heading"].split()) in expanded
    )
    assert proof["unit"] in "\n".join(lines)

    def read_cell(c):
        l = lines[c["line"] - 1]
        left, right = c["columns"]
        assert l[left:right] == c["raw_cell"] and l[right:].lstrip().split()[0] == c["comparison"]
        if arithmetic and "printed_line" in proof and l == proof["printed_line"]:
            marker = proof["repeated_statement_index"]
            # The normalized line retains every numeric right edge. Only the repeated row label is removed.
            label = l[:left].rstrip()
            assert label.endswith(marker)
            remaining = label[: -len(marker)].strip()
        else:
            remaining = l[:left].strip()
        assert remaining == c["label"], (remaining, c["label"])
        return read_amount({"text": c["raw_cell"]})

    c = proof["cell"]
    if "inputs" in c:
        a, b, d = map(read_cell, c["inputs"])
        value = a - b + d
    else:
        value = read_cell(c)
    if field != "eps":
        unit = proof["unit"].lower()
        match = re.search(r"(?:\b|(?<=rs))(crores?|lakhs?|lacs?|millions?|thousands?)\b", unit)
        assert match, unit
        value *= {"crore": 10**7, "lakh": 10**5, "lac": 10**5, "million": 10**6, "thousand": 10**3}[
            match[1].rstrip("s")
        ]
    return value


def cell_value(w, info, boxes):
    field, evidence = w["field"], w["evidence"]
    if w["method"] in {"optical_quarter_columns", "original_indexed_expenses_operating_profit"}:
        text = layout(boxes)
        assert sha(text.encode()) == w["layout_sha256"]
        return calendar_cell(
            field,
            evidence,
            text.splitlines(),
            info["basis"],
            info["period_end"],
            w["method"].startswith("original_indexed_"),
        )
    if w["method"] == "optical_currency_and_cells":
        assert evidence["currency_explicit"] and w.get("admissible", True)
        if "basic_eps_observation" in evidence:
            cell = evidence["basic_eps_observation"]["cell"]
            assert cell in boxes and field == "eps"
            return read_amount(cell)
        cells = evidence["cells"]
        assert all(r["label"] in boxes and r["cell"] in boxes for r in cells)
        nums = [read_amount(r["cell"]) for r in cells]
        return (nums[0] if len(nums) == 1 else nums[0] - nums[1] + nums[2]) * Decimal(
            evidence["unit_inr"]
        )
    assert w["method"] == "optical_grouped_statutory_columns", w["method"]
    assert evidence["basis"] == info["basis"] and evidence["period_end"] == info["period_end"]
    anchor = evidence["anchor"]
    assert all(
        b in boxes
        for b in (
            anchor["scope"],
            anchor["period_label"],
            anchor["comparison"],
            *anchor["duration"],
        )
    )
    r = evidence["cells"][field]
    assert r["cell"] in boxes and all(b in boxes for b in r["labels"])
    if field == "eps":
        assert r["section"] in boxes
        return read_amount(r["cell"])
    assert all(b in boxes for b in evidence["unit_legend"])
    return read_amount(r["cell"]) * Decimal(evidence["unit_inr"])


def structured_fields(info):
    h = info["structured_sha256"]
    source = ET.fromstring(exact(N / "objects" / h[:2] / (h + ".gz"), h, True))
    designation = info["tags"]["quarter_context"]
    synthetic = designation == "NSE quarterly duration plus current segment contexts"
    ctx = "OneD" if synthetic else designation
    data = {
        e.tag.split("}")[-1]: e
        for e in source
        if e.get("contextRef") == ctx and e.text and e.text.strip()
    }
    end = date.fromisoformat(info["period_end"])
    start = date(end.year, end.month - 2, 1).isoformat()
    if synthetic:
        if "DateOfStartOfReportingPeriod" in data:
            assert data["DateOfStartOfReportingPeriod"].text[:10] == start
        else:
            # The older banking schema names the financial year and reporting quarter.
            # Its One-dimension segment contexts below carry the actual quarter start.
            assert (
                data["NameOfBank"].text.strip()
                and data["DescriptionOfPresentationCurrency"].text == "INR"
            )
            fiscal_year = end.year - int(end.month <= 3)
            assert data["DateOfStartOfFinancialYear"].text[:10] == f"{fiscal_year}-04-01"
            assert data["DateOfEndOfFinancialYear"].text[:10] == f"{fiscal_year + 1}-03-31"
            quarter_names = {6: "first quarter", 9: "half yearly", 12: "third quarter", 3: "yearly"}
            assert data["ReportingQuarter"].text.lower() == quarter_names[end.month]
            if end.month in (9, 3):
                # The half/full-year reports publish the cumulative income in a separate column.
                (cumulative,) = [
                    a
                    for a in source
                    if a.tag.split("}")[-1] == "InterestEarned" and a.get("contextRef") == "FourD"
                ]
                assert Decimal(cumulative.text) != Decimal(data["InterestEarned"].text)
        assert data["DateOfEndOfReportingPeriod"].text[:10] == info["period_end"]
        assert data["NatureOfReportStandaloneConsolidated"].text.lower() == info["basis"]
        segment_periods = []
        for node in source:
            if not node.tag.endswith("context") or not node.get("id", "").startswith("One"):
                continue
            extent = {q.tag.split("}")[-1]: q.text for q in node.iter()}
            if "startDate" in extent:
                segment_periods.append((extent["startDate"], extent["endDate"]))
        assert segment_periods and set(segment_periods) == {(start, info["period_end"])}
    else:
        context = next(e for e in source if e.tag.endswith("context") and e.get("id") == ctx)
        times = {e.tag.split("}")[-1]: e.text for e in context.iter()}
        assert (times["startDate"], times["endDate"]) == (start, info["period_end"])
        assert "explicitMember" not in times
    values = {}
    for field, tag in info["tags"].items():
        if field not in METRICS or tag is None:
            continue
        if tag == "RevenueFromOperations-Expenses+FinanceCosts":
            v = [data[t] for t in ("RevenueFromOperations", "Expenses", "FinanceCosts")]
            assert all(e.get("unitRef").lower() == "inr" for e in v)
            a, b, c = (Decimal(e.text) for e in v)
            values[field] = a - b + c
        elif tag in data:
            item = data[tag]
            assert "inr" in item.get("unitRef", "").lower()
            values[field] = Decimal(item.text)
    return values


def run(edition):
    body = json.loads(exact(N / "xbrl-optical-editions" / (edition + ".json.gz"), edition, True))
    return verify_records(edition, body["records"])


def verify_records(edition, records):
    proofs = Counter()
    source_cases = {}
    raster_pages = set()
    for r in records:
        if all(v is None for v in r["values"].values()):
            continue
        fact = json.loads(exact(N / r["file"], r["receipt_sha256"], True))
        info = fact["identity"]
        assert (
            info["id"] == r["id"] and r["values"] == fact["values"] and not fact["unreadable_pages"]
        )
        structured = structured_fields(info)
        good = defaultdict(set)
        all_pages = {
            (p["pdf_sha256"], p["page"], p["boxes_sha256"], p["png_sha256"]) for p in fact["pages"]
        }
        for w in fact["observations"]:
            field = w["field"]
            if fact["values"][field] is None or not w.get("admissible", True):
                continue
            assert (w["pdf_sha256"], w["page"], w["boxes_sha256"], w["png_sha256"]) in all_pages
            boxes = verify_page(
                info["original_sha256"],
                w["pdf_sha256"],
                w["page"],
                w["png_sha256"],
                w["boxes_sha256"],
                w["raster_source"].get("rgb_sha256"),
                2400,
            )
            numeric = cell_value(w, info, boxes)
            assert (
                numeric
                == Decimal(w["value"])
                == Decimal(str(fact["values"][field]))
                == structured[field]
            ), (info["id"], field, numeric)
            good[field].add(numeric)
            proofs[w["method"]] += 1
            raster_pages.add((info["original_sha256"], w["pdf_sha256"], w["page"]))
        assert set(good) == {k for k, v in fact["values"].items() if v is not None}
        source_cases[r["id"]] = {k: str(min(vals)) for k, vals in sorted(good.items())}
    return {
        "source_edition": edition,
        "verified_positive_proofs": dict(proofs),
        "physical_source_pages": len(raster_pages),
        "cases": source_cases,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("editions", nargs="+")
    print(json.dumps([run(e) for e in cli.parse_args().editions], indent=2))
