"""Observe explicitly labelled legacy financial fields inside the printed accounting grid."""

from __future__ import annotations

import functools
import re
import struct
import subprocess
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from .fundamental_pdf_match import MONEY, _legal_header
from .fundamental_rendered import image_date
from .fundamental_rendered_money import _FOREIGN, _RUPEE, monetary_scalar, printed_label

SCALE = r"(crore|lakh|lac|million)s?"
CURRENCY = r"(?:₹|Rs\.?|INR|rupees)"
AMOUNT_LEGEND = re.compile(
    r"[ (]*(?:(?:amounts?|figures|values) (?:are )?in )?"
    + CURRENCY
    + r"\s*(?:in\s+)?"
    + SCALE
    + r"[ ).]*",
    re.IGNORECASE,
)
SPLIT_SCALE = re.compile(r"[ (]*(?:in\s+)?" + SCALE + r"[ ).]*", re.IGNORECASE)
NOTE = re.compile(r"\b(?:segment revenue|cash flow statement|segment reporting)\b", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _gray(image):
    if image[:8] != b"\x89PNG\r\n\x1a\n" or len(image) < 24:
        raise ValueError("Original optical evidence requires its PNG")
    width, height = struct.unpack(">II", image[16:24])
    if width * height > 40_000_000:
        raise ValueError("Unexpected result page dimensions")
    raw = subprocess.run(
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
            "gray",
            "pipe:1",
        ],
        input=image,
        capture_output=True,
        check=True,
        timeout=45,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width)


def _vertical_rules(gray, bottom, top):
    height, width = gray.shape
    a, b = int(height * (1 - top)), int(height * (1 - bottom))
    if a < 0 or b > height or b - a < 8:
        return []
    dark = gray[a:b] < 128
    # Scanned rules can lean by a few pixels across this narrow header band.
    near = dark.copy()
    near[:, 2:] |= dark[:, :-2]
    near[:, :-2] |= dark[:, 2:]
    near[:, 1:] |= dark[:, :-1]
    near[:, :-1] |= dark[:, 1:]
    xs = np.flatnonzero(near.mean(axis=0) >= 0.80)
    return [
        float(g.mean() / width)
        for g in np.split(xs, np.flatnonzero(np.diff(xs) > 1) + 1)
        if 0 < len(g) <= max(8, width * 0.0045)
    ]


def _mid(box):
    return box["x"] + box["width"] / 2


def _bounds(edges, box):
    left = [x for x in edges if x < box["x"] + 0.003]
    right = [x for x in edges if x > box["x"] + box["width"] - 0.003]
    return (max(left), min(right)) if left and right else None


def _anchor(filing, boxes, image):
    target = date.fromisoformat(filing["period_end"])
    titles = [
        b
        for b in boxes
        if re.search(r"\bfinancial results\b", b["text"], re.IGNORECASE)
        and not NOTE.search(b["text"])
    ]
    if len(titles) != 1:
        return None
    title = titles[0]
    if not _legal_header(filing, [b["text"] for b in boxes if b["y"] > title["y"]]):
        return None
    scopes = [
        b
        for b in boxes
        if b["text"].strip().lower() == filing["basis"] and 0 < title["y"] - b["y"] < 0.12
    ]
    if len(scopes) != 1 or image is None:
        return None
    scope = scopes[0]
    dates = [
        (b, image_date(b["text"].strip(" ,.;")))
        for b in boxes
        if len(b["text"]) <= 24 and 0 < scope["y"] - b["y"] < 0.13
    ]
    dates = [(b, d) for b, d in dates if d]
    if not dates:
        return None
    gray = _gray(image)
    top_rules = _vertical_rules(
        gray, scope["y"] - 0.40 * scope["height"], scope["y"] + 0.35 * scope["height"]
    )
    possible = []
    for cell, day in dates:
        if day != target:
            continue
        date_rules = _vertical_rules(
            gray, cell["y"] - 0.9 * cell["height"], cell["y"] + 1.45 * cell["height"]
        )
        persistent = [x for x in top_rules if any(abs(x - v) < 0.0025 for v in date_rules)]
        group = _bounds(persistent, scope)
        column = _bounds(date_rules, cell)
        if (
            not group
            or not column
            or abs(_mid(scope) - sum(group) / 2) > 0.15 * (group[1] - group[0])
            or abs(group[0] - column[0]) > 0.004
            or column[1] > group[1]
            or column[1] - column[0] < 0.02
        ):
            continue
        subset = sorted(
            (
                (b, d)
                for b, d in dates
                if abs(b["y"] - cell["y"]) < 0.7 * cell["height"] and group[0] < _mid(b) < group[1]
            ),
            key=lambda pair: _mid(pair[0]),
        )
        if (
            len(subset) < 2
            or subset[0][0] != cell
            or not target - timedelta(days=370) <= subset[1][1] < target
        ):
            continue
        between = sorted(
            [
                b
                for b in boxes
                if column[0] < _mid(b) < column[1] and cell["y"] < b["y"] < scope["y"]
            ],
            key=lambda b: -b["y"],
        )
        duration = " ".join(x["text"].strip().lower() for x in between)
        if not re.fullmatch(r"(?:quarter|3 months?|three months?) ended", duration):
            continue
        possible.append(
            {
                "scope": scope,
                "period_label": cell,
                "comparison": subset[1][0],
                "duration": between,
                "scope_grid": list(group),
                "column_grid": list(column),
            }
        )
    return (title, possible[0]) if len(possible) == 1 else None


def _unit(boxes, title, anchor):
    items = [b for b in boxes if anchor["scope"]["y"] - 0.002 <= b["y"] <= title["y"] + 0.009]
    if any(_FOREIGN.search(b["text"]) for b in items):
        return None
    # A legend next to some other accounting basis may only apply to that group.
    # A statement-wide currency legend sits above the row of basis names.
    left, right = anchor["scope_grid"]
    scope = anchor["scope"]
    items = [
        b
        for b in items
        if b["y"] >= scope["y"] + scope["height"] * 0.65
        or left <= b["x"]
        and b["x"] + b["width"] <= right
    ]
    pairs = []
    for b in items:
        legend = AMOUNT_LEGEND.fullmatch(b["text"].strip())
        if legend:
            pairs.append((MONEY[legend[1].lower()], [b]))
            continue
        scale = SPLIT_SCALE.fullmatch(b["text"].strip())
        if not scale:
            continue
        neighbors = [
            q
            for q in items
            if abs(b["y"] - q["y"]) < 0.5 * b["height"]
            and 0 <= b["x"] - q["x"] - q["width"] < 0.025
            and re.fullmatch(r"[ (]*" + CURRENCY + r"[ )]*", q["text"], re.IGNORECASE)
        ]
        if len(neighbors) == 1:
            pairs.append((MONEY[scale[1].lower()], [neighbors[0], b]))
    if len({u for u, w in pairs}) != 1:
        return None
    return pairs[0]


def _cell(labels, boxes, anchor):
    left, right = anchor["column_grid"]
    height = max(l["height"] for l in labels)
    low = min(l["y"] for l in labels)
    high = max(l["y"] for l in labels)
    lookup = [
        b
        for b in boxes
        if low - 0.57 * height <= b["y"] <= high + 0.57 * height
        and left + 0.001 <= b["x"]
        and b["x"] + b["width"] <= right - 0.001
    ]
    if len(lookup) != 1:
        return None
    num = monetary_scalar(lookup[0]["text"])
    return (num, {"labels": labels, "cell": lookup[0]}) if num is not None else None


def _basic_eps(left, boxes, anchor):
    spans = [
        b for b in left if re.search(r"earnings? per (?:equity )?share", b["text"], re.IGNORECASE)
    ]
    if len(spans) != 1:
        return None
    caption = spans[0]
    labels = [
        b
        for b in left
        if 0 < caption["y"] - b["y"] < 0.06
        and re.fullmatch(
            r"(?:\(?a\)?[.)\s-]+)?basic(?:\s*(?:\(?(?:in\s+)?(?:INR|Rs\.?|₹|rupees)\)?))?",
            b["text"].strip(),
            re.IGNORECASE,
        )
    ]
    if len(labels) != 1:
        return None
    label = labels[0]
    # 'of Rs 10 each' identifies the share face value, not the earnings currency.
    near = [x for x in left if label["y"] - 0.001 <= x["y"] <= caption["y"] + 0.001]
    text = " ".join(x["text"] for x in near)
    if re.search(
        r"continuing|discontinued|attributable|ADR", text, re.IGNORECASE
    ) or _FOREIGN.search(text):
        return None
    explicit = re.search(
        r"earnings? per (?:equity )?share\s*[:(,-]?\s*(?:in\s+)?(?:INR|Rs\.?|₹|rupees)(?!\s*[.\d])",
        caption["text"],
        re.IGNORECASE,
    )
    if not (explicit or _RUPEE.search(label["text"])):
        return None
    result = _cell([label], boxes, anchor)
    if not result:
        return None
    value, proof = result
    return value, {**proof, "section": caption}


def observe(filing, boxes, *, image_bytes=None):
    """Return verified rupee amounts only when the original raster supplies issuer, scope, period, units and labels."""
    witness = _anchor(filing, boxes, image_bytes)
    if not witness:
        return None
    title, anchor = witness
    origin = _unit(boxes, title, anchor)
    unit, unit_boxes = origin or (None, [])
    col = anchor["scope_grid"][0]
    left = [
        b
        for b in boxes
        if b["x"] + b["width"] <= col + 0.002 and b["y"] < anchor["period_label"]["y"]
    ]
    end = max(
        (
            b["y"]
            for b in left
            if re.search(
                r"other comprehensive income|earnings per (?:equity )?share|segment reporting",
                b["text"],
                re.IGNORECASE,
            )
        ),
        default=0,
    )
    rows = [b for b in left if b["y"] > end]
    if any(re.search(r"\bcontinuing|\bdiscontinued", b["text"], re.IGNORECASE) for b in rows):
        return None
    bank = filing.get("metadata", {}).get("bank") == "B"
    kinds = {"revenue": [], "operating_profit": [], "net_profit": []}
    for b in rows:
        l = printed_label(b["text"])
        kind = labels = None
        if (bank and l == "total income") or (
            not bank and re.fullmatch(r"total (?:revenue|income) from operations(?: \(net\))?", l)
        ):
            kind, labels = "revenue", [b]
        elif re.fullmatch(
            r"(?:net )?profit(?:\s*/\s*\(loss\))? for the (?:period|quarter)(?:/year)?", l
        ):
            kind, labels = "net_profit", [b]
        elif (
            bank
            and re.fullmatch(r"operating profit before provisions (?:and|&) contingencies", l)
            or not bank
            and re.fullmatch(
                r"profit from operations before other income(?:,| and)? (?:finance costs|financial costs)(?: and exceptional items)?",
                l,
            )
        ):
            kind, labels = "operating_profit", [b]
        elif not bank and l in {
            "profit from operations before other income and",
            "profit from operations before other income,",
        }:
            cont = [
                q
                for q in rows
                if abs(q["x"] - b["x"]) < 0.012
                and 0 < b["y"] - q["y"] < 1.8 * max(q["height"], b["height"])
                and re.fullmatch(
                    r"(?:finance|financial) costs(?: and exceptional items)?",
                    printed_label(q["text"]),
                )
            ]
            if len(cont) == 1:
                kind, labels = "operating_profit", [b, cont[0]]
        if kind and (found := _cell(labels, boxes, anchor)):
            kinds[kind].append(found)
    values = dict.fromkeys(("revenue", "operating_profit", "net_profit", "eps"))
    cells = {}
    for k, observations in kinds.items():
        if not unit or filing.get(k) is None or len(observations) != 1:
            continue
        n, example = observations[0]
        if n * unit == Decimal(str(filing[k])):
            values[k] = float(n * unit)
            cells[k] = example
    eps = _basic_eps(left, boxes, anchor)
    if eps and filing.get("eps") is not None and eps[0] == Decimal(str(filing["eps"])):
        values["eps"], cells["eps"] = float(eps[0]), eps[1]
    if not cells:
        return None
    return {
        "values": values,
        "cells": cells,
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "method": "original_raster_merged_scope_quarter_grid_with_structured_agreement",
        "title": title,
        "anchor": anchor,
        "unit_inr": unit,
        "unit_legend": unit_boxes,
    }
