"""Rupee fields traced to the dated first column of an original results statement image."""

import re
from decimal import Decimal

from .fundamental_pdf_match import MONEY
from .fundamental_rendered import cell_at, scalar, selector, statement_column

_UNIT = re.compile(r"\bin\s+(crore|lakh|lac|million)s?\b", re.IGNORECASE)
_RUPEE = re.compile(r"₹|\b(?:rs|inr|rupees)\b", re.IGNORECASE)
_FOREIGN = re.compile(
    r"\b(?:USD|dollars?|GBP|pounds?|EUR|euros?|JPY|yen|CNY|RMB|yuan|AUD|CAD|HKD|"
    r"PKR|LKR|NPR|AED|dirhams?|francs?|CHF|SEK|SGD|NZD|SAR|ZAR|KRW|renminbi)\b",
    re.IGNORECASE,
)
_NET_PROFIT = re.compile(
    r"(?:net )?profit(?:\s*/\s*\(loss\))? (?:after tax(?: for the (?:period|quarter|year))?|"
    r"for the (?:period|quarter)(?:/year)?)"
)
_END = re.compile(
    r"attributable to|non[- ]controlling interests|other comprehensive income|"
    r"earnings per (?:equity )?share",
    re.IGNORECASE,
)


def monetary_scalar(text):
    token = text.strip()
    if "," in token:
        # Admit conventional international or Indian thousands grouping only.
        if not re.fullmatch(r"\(?-?\d{1,3}(?:(?:,\d{3})+|(?:,\d{2})+,\d{3})(?:\.\d+)?\)?", token):
            return None
        token = token.replace(",", "")
    return scalar(token)


def printed_label(text):
    text = re.sub(r"^\s*(?:(?:\(?[a-z]\)|[0-9]+[.:)]?)\s+)", "", text.lower())
    text = re.sub(r"\s*[\[(][\d +\-–/=]+[\])]\s*$", "", text)
    return " ".join(text.split()).strip()


def observe(filing, boxes, field):
    if field == "eps":
        evidence = selector(filing, boxes)
        if not evidence:
            return None
        # A face-value denomination can differ from the currency of the EPS line.
        caption = re.split(r"face\s+value", evidence["section"], flags=re.IGNORECASE)[0]
        caption += " " + evidence["basic_label"]
        value = scalar(evidence["cell"]["text"])
        if value is None or _FOREIGN.search(caption) or not _RUPEE.search(caption):
            return None
        return {
            "field": "eps",
            "measure": "INR per ordinary share",
            "unit_inr": 1,
            "currency_explicit": True,
            "value_inr": str(value),
            "basic_eps_observation": evidence,
        }
    anchor = statement_column(filing, boxes)
    if not anchor:
        return None
    header, left, right, duration = anchor
    upper = [b["text"] for b in boxes if b["y"] >= header["y"] - 0.005]
    units = {MONEY[u.lower()] for line in upper for u in _UNIT.findall(line)}
    if len(units) != 1 or any(_FOREIGN.search(text) for text in upper):
        return None
    unit = units.pop()
    rupees = any(_UNIT.search(text) and _RUPEE.search(text) for text in upper)
    labels = [b for b in boxes if b["x"] < left - 0.01 and b["y"] < header["y"]]
    section_end = max((b["y"] for b in labels if _END.search(b["text"])), default=0)
    labels = [b for b in labels if b["y"] > section_end]
    if any(
        re.search(r"\b(?:continuing|discontinued|segment)\b", b["text"], re.IGNORECASE)
        for b in labels
    ):
        return None

    def find(accept):
        matched = []
        for label in labels:
            if accept(printed_label(label["text"])):
                cell = cell_at(label, boxes, left, right)
                if cell and (value := monetary_scalar(cell["text"])) is not None:
                    matched.append((value, {"label": label, "cell": cell}))
        return matched[0] if matched and len({v for v, _ in matched}) == 1 else (None, None)

    bank = filing.get("metadata", {}).get("bank") == "B"

    def revenue():
        if bank:
            return find(lambda l: l == "total income")
        total = find(
            lambda l: l in {"total revenue from operations", "total income from operations (net)"}
        )
        return total if total[0] is not None else find(lambda l: l == "revenue from operations")

    if field == "revenue":
        value, cell = revenue()
        cells = [cell]
    elif field == "net_profit":
        value, cell = find(_NET_PROFIT.fullmatch)
        cells = [cell]
    elif field == "operating_profit":
        value, cell = find(
            lambda l: (
                l
                in {
                    "operating profit before provisions and contingencies",
                    "operating profit before provisions & contingencies",
                    "profit from operations before other income, finance costs and exceptional items",
                }
            )
        )
        cells = [cell]
        if value is None and not bank:
            rev, rc = revenue()
            expenses, ec = find(lambda l: l in {"total expenses", "total expenditure"})
            finance, fc = find(lambda l: l in {"finance costs", "finance cost"})
            if None not in (rev, expenses, finance):
                value, cells = rev - expenses + finance, [rc, ec, fc]
    else:
        raise ValueError("Unknown original monetary field")
    if value is None:
        return None
    return {
        "field": field,
        "period_label": header["text"],
        "duration_label": duration,
        "unit_inr": unit,
        "currency_explicit": rupees,
        "cells": cells,
        "value_inr": str(value * Decimal(unit)),
    }
