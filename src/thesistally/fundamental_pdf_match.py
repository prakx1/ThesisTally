"""Corroborate the current-quarter figures in an earlier, dated, original exchange PDF."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .fundamental_currency import FOREIGN, RUPEE_LEGEND, currency_source

MONTHS = {name.lower()[:3]: i for i, name in enumerate(calendar.month_name) if name}
MONTH = r"(?:jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)"
DATE = re.compile(
    rf"(?<!\d)(?:(\d{{1,2}})(?:st|nd|rd|th)?[\s./-]+({MONTH})|"
    rf"({MONTH})[\s.-]+(\d{{1,2}})(?:st|nd|rd|th)?|"
    r"(\d{1,2})[./-](\d{1,2}))[,\s./-]*(\d{4}|\d{2})?\b",
    re.IGNORECASE,
)
FIRST_FIELD = re.compile(
    r"^\s*(?:(?:\([ivx\da-z]+\)|[ivxlcdm\d]+[.):]?)\s+)?(\S.+?)\s{2,}((?:[(-]?\d[\d,.]*\)?|[-–—])(?:\s+|$))",
    re.IGNORECASE,
)
DURATION = re.compile(
    r"\b(quarter(?:ly)?|three\s+months?|3\s+months?|six\s+months?|nine\s+months?|half[- ]year|year\s+ended)\b",
    re.IGNORECASE,
)
MONEY = {"crore": 10_000_000, "lakh": 100_000, "lac": 100_000, "million": 1_000_000}


def _column_header(lines):
    """Identify the first, quarterly header group and its first dated column."""
    for i, line in enumerate(lines):
        low = line.lower()
        if not (m := DURATION.search(low)):
            continue
        lead = _norm(low[: m.start()])
        if lead not in {
            "",
            "particulars",
            "slno",
            "particularsforth",
            "particularsforthe",
            "forthe",
            "srnoparticulars",
        }:
            continue
        if m[0].lower() not in (
            "quarter",
            "quarterly",
            "three months",
            "three month",
            "3 months",
            "3 month",
        ):
            continue
        # A paragraph mentioning quarterly earnings cannot identify the table's date column.
        # Standalone/Consolidated headings can mention the period, so require a column header.
        if not re.search(r"\s{2,}\S", line.lstrip()) and not low.lstrip().startswith(
            ("quarter ended", "quarters ended", "three months ended", "3 months ended")
        ):
            continue
        for j in range(i, min(i + 4, len(lines))):
            found = DATE.search(lines[j])
            if not found:
                continue
            a, b, c, d, e, f, yr = found.groups()
            day = int(a or d or e)
            month = MONTHS[(b or c)[:3].lower()] if b or c else int(f)
            center = found.start() + len(found[0].rstrip(" ,")) / 2
            if not yr and j + 1 < len(lines):
                # A label such as Particulars may precede the year. Read only the
                # year vertically aligned with the first dated header.
                years = [
                    y[0]
                    for y in re.finditer(r"\b20\d\d\b", lines[j + 1])
                    if abs(y.start() + 2 - center) <= 6
                ]
                yr = years[0] if len(years) == 1 else None
            if not yr:
                break
            try:
                period = date(int(yr) if len(yr) == 4 else 2000 + int(yr), month, day)
                other = list(DATE.finditer(lines[j]))[1:2]
                if other:
                    second = other[0].start() + len(other[0][0].rstrip(" ,")) / 2
                else:
                    preceding = re.search(r"\bpreceding\b", line, re.IGNORECASE)
                    second = (preceding.start() + preceding.end()) / 2 if preceding else center
                radius = (second - center) / 2
                return period, ((center - radius, center + radius) if radius > 3 else None)
            except ValueError:
                break
    return None


def _norm(label):
    return re.sub(r"[^a-z]", "", label.lower())


def _classify(label, previous):
    # Ignore comparisons, segment ratios, EPS from discontinued operations alone and
    # profit attributable to shareholders when the structured tag is total period profit.
    if label.startswith(
        (
            "revenuefromoperations",
            "incomefromoperations",
            "totalrevenuefromoperations",
            "totalincomefromoperations",
            "netsalesincomefromoperations",
            "netsalesfromoperations",
        )
    ):
        return "revenue"
    if label in {"totalincome", "totalincomeiii"}:
        return "bank_income"
    if previous.startswith("operatingprofit") and label.startswith("profitbeforeprovisions"):
        return "operating_profit"
    if label.startswith(
        (
            "operatingprofitbeforeprovisions",
            "operatingprofitbeforeprovision",
            "profitlossfromoperationsbeforeotherincome",
            "operatingprofitbeforeinterest",
        )
    ):
        return "operating_profit"
    if label.startswith("totalexpenses") or label == "totalexpenditure":
        return "expenses"
    if label.endswith(("financecosts", "financecost", "financialcosts")) and len(label) < 27:
        return "finance"
    if label == "netprofit":
        return "net_profit"
    if label.startswith(
        (
            "profitfortheperiod",
            "netprofitfortheperiod",
            "netprofitlossfortheperiod",
            "profitlossfortheperiod",
            "profitaftertaxfortheperiod",
            "profitaftertax",
            "netprofitaftertax",
            "profitforthequarter",
        )
    ) and not any(
        s in label
        for s in (
            "attributable",
            "beforetax",
            "beforeextraordinary",
            "discontinued",
            "beforeexceptional",
        )
    ):
        return "net_profit"
    if (
        "basic" in label
        and "diluted" not in label.split("basic")[0]
        and (
            "earningsper" in label
            or "basiceps" in label
            or "earningsper" in previous
            or label
            in {
                "basic",
                "abasic",
                "basicanddiluted",
                "basicearningspershare",
                "abasicanddiluted",
                "basicnotannualised",
                "basicnotannualized",
            }
        )
        and not any(s in label for s in ("discontinuedoperationsalone", "annualisedbasic"))
    ):
        return "eps"
    return None


def _num(token):
    clean = token.strip().replace(",", "")
    if clean in {"-", "–", "—"}:
        return None
    try:
        return Decimal(
            "-" + clean[1:-1] if clean.startswith("(") and clean.endswith(")") else clean
        )
    except InvalidOperation:
        return None


def _legal_header(filing, headers):
    metadata = filing.get("metadata", {})
    names = {str(metadata.get(key) or "") for key in ("companyName", "cmName", "smName")}

    def legal(s):
        s = re.sub(r"\bltd\.?\b", "limited", s.lower()).replace("&", "and")
        return re.sub(r"[^a-z0-9]", "", s)

    expected = {legal(n) for n in names if len(n.strip()) >= 5}
    for line in headers:
        # Match a statement's company heading, including its optional accounting basis.
        name = re.sub(
            r"\s*\((?:standalone|consolidated)\)\s*$", "", line.strip(), flags=re.IGNORECASE
        )
        if legal(name) in expected:
            return True
    return False


def _earnings_scope(label):
    continuing = "continuingoperation" in label or "continuinganddiscontinued" in label
    discontinued = "discontinuedoperation" in label or "discontinuedandcontinuing" in label
    if continuing or discontinued:
        return "total" if continuing and discontinued else "partial"
    return None


def _candidate(filing, text, *, original=False, dated_column=None):
    lines = text.splitlines()
    body = []
    for i, line in enumerate(lines):
        if (cell := FIRST_FIELD.match(line)) and _classify(_norm(cell[1]), "") in (
            "revenue",
            "bank_income",
        ):
            body = lines[i:]
            headers = lines[:i]
            break
    if not body:
        return None
    header = "\n".join(headers)
    kinds = set(re.findall(r"\b(standalone|consolidated)\b", header, re.IGNORECASE))
    if {b.lower() for b in kinds} != {filing["basis"]}:
        return None
    column = dated_column or _column_header(headers)
    if column is None or column[0] != date.fromisoformat(filing["period_end"]):
        return None
    units = {MONEY[u] for u in re.findall(r"\b(crore|lakh|lac|million)s?\b", header.lower())}
    if len(units) != 1 or FOREIGN.search(header):
        return None
    unit = units.pop()
    bounds = column[1] if dated_column else None
    if original:
        bounds = column[1]
        if not bounds or not _legal_header(filing, headers):
            return None
        if re.search(r"US GAAP|IFRS", header, re.IGNORECASE):
            return None
    cells = {}
    previous = ""
    scope = None
    for line in body:
        m = FIRST_FIELD.match(line)
        label = _norm(m[1] if m else line)
        named_scope = _earnings_scope(label)
        if named_scope and (not m or "earningsper" in label or "eps" in label):
            scope = named_scope
        if m:
            typ = _classify(label, previous)
            value = _num(m[2])
            if typ in {"eps", "net_profit"} and (
                (named_scope or scope) == "partial" or "attribut" in label
            ):
                value = None
            if bounds:
                center = m.start(2) + len(m[2].strip()) / 2
                if not bounds[0] <= center < bounds[1]:
                    value = None
            if typ and value is not None:
                cells.setdefault(typ, []).append((value, " ".join(m[1].split())))
        if line.strip():
            previous = _norm(line)

    def single(k):
        values = {v for v, _ in cells.get(k, ())}
        return values.pop() if len(values) == 1 else None

    rev_key = (
        "bank_income"
        if filing.get("tags", {}).get("revenue") in {"TotalIncome", "Income", "total income"}
        or (original and filing.get("metadata", {}).get("bank") == "B")
        else "revenue"
    )
    revenue, eps, net = single(rev_key), single("eps"), single("net_profit")
    op = single("operating_profit")
    expense, finance = single("expenses"), single("finance")
    if op is None and rev_key != "bank_income" and None not in (revenue, expense, finance):
        op = revenue - expense + finance
    levels = {"revenue": revenue, "operating_profit": op, "net_profit": net, "eps": eps}
    amounts = {
        key: val * (1 if key == "eps" else unit) if val is not None else None
        for key, val in levels.items()
    }
    if original:
        # The original PDF alone can establish a value. If the rupee glyph is absent
        # from the text layer, require two independently matched INR monetary fields.
        monetary_matches = sum(
            filing.get(key) is not None and amounts[key] == Decimal(str(filing[key]))
            for key in ("revenue", "operating_profit", "net_profit")
        )
        rupees = RUPEE_LEGEND.search(header)
        if not rupees and monetary_matches < 2:
            return None
        if not any(val is not None for val in amounts.values()):
            return None
    elif any(
        value is None
        or filing.get(k) is None
        or abs(value * (1 if k == "eps" else unit) - Decimal(str(filing[k]))) > Decimal("0.000001")
        for k, value in levels.items()
    ):
        return None
    return {
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "unit_inr": unit,
        "currency_evidence": currency_source(header),
        "numeric_column": 1,
        "values": {k: float(v) if v is not None else None for k, v in amounts.items()},
        "cells": {
            k: [{"value": str(v), "label": lab} for v, lab in pairs] for k, pairs in cells.items()
        },
        "operating_profit_in_printed_units": str(op) if op is not None else None,
        "operating_profit_method": "printed"
        if single("operating_profit") is not None
        else "operating_revenue_minus_total_expenses_plus_finance_costs",
    }


def match(filing, notices_text):
    for document in notices_text:
        for i, page in enumerate(document["pages"]):
            evidence = _candidate(filing, page)
            if evidence:
                return {"pdf_sha256": document["pdf_sha256"], "page": i + 1, **evidence}
    return None


def extract_original(filing, documents):
    """First quarter column of an identified issuer statement, including partial original facts."""
    candidates = []
    for document in documents:
        for i, page in enumerate(document["pages"]):
            proof = _candidate(filing, page, original=True)
            if proof:
                candidates.append({**proof, "pdf_sha256": document["pdf_sha256"], "page": i + 1})
    if not candidates:
        return None
    # Competing statements with different original numbers cannot resolve each other.
    for k in ("revenue", "operating_profit", "net_profit", "eps"):
        if len({r["values"][k] for r in candidates if r["values"][k] is not None}) > 1:
            return None
    result = max(candidates, key=lambda r: sum(v is not None for v in r["values"].values()))
    return {**result, "method": "issuer_heading_dated_first_pdf_column"}
