"""Corroborate original financial statements with vertically stacked quarterly headers."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .fundamental_currency import FOREIGN, currency_source
from .fundamental_pdf_match import (
    DATE,
    FIRST_FIELD,
    MONEY,
    MONTHS,
    _classify,
    _legal_header,
    _norm,
    _num,
)

_SCOPE = re.compile(r"\b(standalone|consolidated)\b", re.IGNORECASE)
_OTHER_STANDARD = re.compile(r"\bIFRS\b|US GAAP", re.IGNORECASE)
_HEADER = re.compile(
    r"^\s*(?P<first>Quarter)(?:\s+ended)?\s{2,}(?P<second>Preceding)\b", re.IGNORECASE
)
_CONTINUATION = re.compile(r"^\s*Part\s+I\s*\(cont(?:inue)?d[. ]*\)", re.IGNORECASE)
_END = re.compile(
    r"^\s*(?:Part\s+II\b|Segment\s+(?:revenue|results)|Notes\s*[:.]|See accompanying notes)",
    re.IGNORECASE,
)


def _first_column(lines, period):
    """Bind words vertically to the current-quarter header, never reading a comparison column."""
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        first = (m.start("first") + m.end("first")) / 2
        second = (m.start("second") + m.end("second")) / 2
        radius = (second - first) / 2
        if radius < 4:
            return None
        left, right = first - radius, first + radius
        block = []
        for row in lines[i + 1 : i + 6]:
            if (
                re.search(r"\b(?:Unaudited|Audited)\b", row, re.IGNORECASE)
                or FIRST_FIELD.match(row)
                and not re.search(r"\b(?:19|20)\d{2}\b", row)
            ):
                break
            block.append(row)
        words = [
            word[0]
            for line in block
            for word in re.finditer(r"\S+", line)
            if left <= (word.start() + word.end()) / 2 < right
        ]
        tokens = " ".join(words)
        matches = list(DATE.finditer(tokens))
        if len(matches) != 1:
            return None
        a, b, c, d, e, f, year = matches[0].groups()
        if not year or len(year) != 4:
            return None
        try:
            actual = date(
                int(year), MONTHS[(b or c)[:3].lower()] if b or c else int(f), int(a or d or e)
            )
        except ValueError:
            return None
        if actual == period:
            return (left, right), i, tokens
        return None
    return None


def _statement(filing, pages, first_index):
    """The issuer/basis/units anchor is on the primary statement; an adjacent page must reidentify its basis."""
    period = date.fromisoformat(filing["period_end"])
    primary = pages[first_index].splitlines()
    head = _first_column(primary, period)
    if not head:
        return None
    bounds, header_line, tokens = head
    header = "\n".join(primary[:header_line])
    units = {
        MONEY[u.lower()]
        for u in re.findall(r"\b(crore|lakh|lac|million)s?\b", header, re.IGNORECASE)
    }
    if (
        len(units) != 1
        or FOREIGN.search(header)
        or _OTHER_STANDARD.search(header)
        or not _legal_header(filing, primary[:header_line])
        or {m.lower() for m in _SCOPE.findall(header)} != {filing["basis"]}
        or not re.search(r"financial\s+results", header, re.IGNORECASE)
    ):
        return None
    unit = units.pop()
    scope = filing["basis"]
    statements = [(first_index + 1, primary, bounds, header_line, tokens)]
    if first_index + 1 < len(pages):
        following = pages[first_index + 1].splitlines()
        before_end = []
        for line in following:
            before_end.append(line)
            if _END.search(line):
                break
        next_header = _first_column(before_end, period)
        content = "\n".join(before_end)
        if (
            next_header
            and _CONTINUATION.search(next((s for s in following if s.strip()), ""))
            and {s.lower() for s in _SCOPE.findall(content)} == {scope}
            and re.search(
                r"see accompanying notes to the " + scope + r" financial results",
                content,
                re.IGNORECASE,
            )
            and not FOREIGN.search(content)
            and not _OTHER_STANDARD.search(content)
            and not re.search(r"\b(?:limited|ltd\.?|CIN)\b", content, re.IGNORECASE)
        ):
            b, j, t = next_header
            statements.append((first_index + 2, following, b, j, t))

    cells = defaultdict(list)
    for page_no, lines, (left, right), first_row, _ in statements:
        eps_section = False
        eps_scope = None
        previous = ""
        for row, line in enumerate(lines[first_row + 1 :], first_row + 2):
            if _END.search(line):
                break
            norm = _norm(line)
            if re.match(r"earnings(?:loss)?pershare", norm):
                eps_section, eps_scope = True, None
            if eps_section and norm in {
                "continuingoperations",
                "discontinuedoperation",
                "discontinuedoperations",
                "continuinganddiscontinuedoperations",
                "continuinganddiscontinuedoperation",
            }:
                eps_scope = norm
            cell = FIRST_FIELD.match(line)
            if cell:
                label, token = cell[1], cell[2].strip()
                kind = _classify(_norm(label), previous)
                if eps_section and re.fullmatch(r"(?:a)?basic(?:in[a-z]*)?", _norm(label)):
                    kind = "eps"
                if kind == "eps" and eps_scope and "and" not in eps_scope:
                    kind = None
                center = cell.start(2) + len(token) / 2
                value = _num(token)
                if kind and value is not None and left <= center < right:
                    cells[kind].append(
                        {
                            "value": str(value),
                            "label": " ".join(label.split()),
                            "page": page_no,
                            "row": row,
                        }
                    )
            if norm:
                previous = norm

    def number(key):
        values = {Decimal(c["value"]) for c in cells.get(key, ())}
        return values.pop() if len(values) == 1 else None

    rev_key = (
        "bank_income"
        if filing.get("tags", {}).get("revenue") in {"TotalIncome", "Income", "total income"}
        else "revenue"
    )
    revenue, expenses, finance = number(rev_key), number("expenses"), number("finance")
    operating = number("operating_profit")
    method = "printed"
    if operating is None and rev_key == "revenue" and None not in (revenue, expenses, finance):
        operating = revenue - expenses + finance
        method = "operating_revenue_minus_total_expenses_plus_finance_costs"
    amounts = {
        "revenue": revenue,
        "operating_profit": operating,
        "net_profit": number("net_profit"),
        "eps": number("eps"),
    }
    if any(value is None for value in amounts.values()):
        return None
    for key, value in amounts.items():
        amounts[key] = value * (1 if key == "eps" else unit)
        if filing.get(key) is None or amounts[key] != Decimal(str(filing[key])):
            return None
    return {
        "period_end": filing["period_end"],
        "basis": scope,
        "unit_inr": unit,
        "currency_evidence": currency_source(header),
        "numeric_column": 1,
        "page": first_index + 1,
        "pages_used": sorted({c["page"] for group in cells.values() for c in group}),
        "current_quarter_headers": {str(s[0]): s[4] for s in statements},
        "cells": dict(cells),
        "values": {key: float(value) for key, value in amounts.items()},
        "operating_profit_in_printed_units": str(operating),
        "operating_profit_method": method,
        "method": "issuer_first_column_stacked_quarter_full_structured_agreement",
    }


def corroborate_continuations(filing, documents):
    """Require all four original values, one basis, quarter and scale, and exact structured corroboration."""
    for document in documents:
        for i in range(len(document["pages"])):
            if found := _statement(filing, document["pages"], i):
                return {"pdf_sha256": document["pdf_sha256"], **found}
    return None
