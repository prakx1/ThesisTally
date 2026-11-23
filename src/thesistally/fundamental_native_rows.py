"""Match native first-disclosure table cells to independently filed, same-quarter XBRL facts."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .discovery_registry import digest
from .fundamental_pdf_match import DATE, DURATION, MONEY, MONTHS, _legal_header
from .fundamental_rendered_money import _FOREIGN, monetary_scalar

FIELDS = ("revenue", "operating_profit", "net_profit", "eps")
CASH = r"(?:₹|INR|Rs\.?|rupees)"
SCALE = r"(crore|lakh|lac|million)s?"
UNIT = re.compile(
    r"[ (\[]*(?:(?:amounts?|values|figures) (?:are )?in )?"
    + CASH
    + r"\s*(?:in\s+)?"
    + SCALE
    + r"(?:[, ]+(?:except|excluding) (?:per[- ]share (?:data|amounts?)|EPS))?[ )\].]*",
    re.IGNORECASE,
)
FORBIDDEN = re.compile(r"\b(?:IFRS|ADR|segment reporting|cash flow statement)\b", re.IGNORECASE)


def _printed_dates(line, following):
    out = []
    for d in DATE.finditer(line):
        day, month, month2, day2, dnum, mnum, year = d.groups()
        center = d.start() + len(d[0].rstrip(" ,")) / 2
        if not year:
            years = [
                x[0]
                for x in re.finditer(r"\b(?:19|20)\d{2}\b", following)
                if abs(x.start() + 2 - center) <= 6
            ]
            year = years[0] if len(years) == 1 else None
        if not year or len(year) != 4:
            return []
        try:
            parsed = date(
                int(year),
                MONTHS[(month or month2)[:3].lower()] if month or month2 else int(mnum),
                int(day or day2 or dnum),
            )
        except ValueError:
            return []
        out.append((parsed, center))
    return out


def _quarter(lines, target):
    for i, head in enumerate(lines):
        d = DURATION.search(head)
        if not d or d[0].lower() not in {"quarter", "quarterly", "three months", "3 months"}:
            continue
        lead = " ".join(head[: d.start()].lower().split())
        if lead not in {
            "",
            "particulars",
            "for",
            "for the",
            "particulars for",
            "particulars for the",
        }:
            continue
        for j in range(i, min(i + 5, len(lines))):
            ds = _printed_dates(lines[j], lines[j + 1] if j + 1 < len(lines) else "")
            if len(ds) < 2:
                continue
            (first, x), (second, y) = ds[:2]
            if first != target or not target - timedelta(days=370) <= second < first or y - x < 8:
                continue
            # Quarter must scope the first date block. A quarter caption beyond a
            # preceding annual column, or a detached heading at the left margin, is ambiguous.
            prefix = re.split(
                r"\b(?:six|nine|twelve|half[- ]year|year)\b",
                head[d.end() :],
                flags=re.IGNORECASE,
                maxsplit=1,
            )
            if len(prefix) > 1 and x >= d.end() + len(prefix[0]):
                continue
            # A grouped caption can be centred across three quarter columns.
            # Where the caption is remote from the dated set its scope is unclear.
            if d.start() > x + 1.6 * (y - x) or d.end() < x - 1.5 * (y - x):
                continue
            prior = [
                m
                for h in lines[max(0, i - 2) : i]
                for m in re.finditer(
                    r"\b(?:year|half[- ]year|nine months|six months)(?:\s+ended)?\b",
                    h,
                    re.IGNORECASE,
                )
                if not re.search(r"statement|financial|results", h, re.IGNORECASE)
                and x - (y - x) / 2 <= m.start() < min(d.start(), x + (y - x) / 2)
            ]
            if prior:
                continue
            return {
                "header_line": i + 1,
                "date_line": j + 1,
                "dates": [first.isoformat(), second.isoformat()],
                "centers": [x, y],
                "labels": lines[i : j + 2],
            }
    return None


def _issuer(filing, lines):
    if _legal_header(filing, lines):
        return next(line.strip() for line in lines if _legal_header(filing, [line]))
    for i in range(len(lines)):
        for n in (2, 3):
            group = lines[i : i + n]
            if len(group) != n or not all(
                re.fullmatch(r"[\w &'.-]+", s.strip()) and len(s.strip()) >= 2 for s in group
            ):
                continue
            indents = [len(s) - len(s.lstrip()) for s in group]
            joined = " ".join(s.strip() for s in group)
            if max(indents) - min(indents) <= 4 and _legal_header(filing, [joined]):
                return joined
    return None


def _scalar(tok):
    return monetary_scalar(tok.group()) if tok else None


def _row(line, centers):
    x, y = centers
    radius = (y - x) / 2
    observed = []
    tokens = list(re.finditer(r"\S+", line))
    for c in (x, y):
        ns = [
            t
            for t in tokens
            if c - radius <= (t.start() + t.end()) / 2 < c + radius and _scalar(t) is not None
        ]
        if len(ns) != 1:
            return None
        observed.append(ns[0])
    cell, second = observed
    label = line[: cell.start()].rstrip()
    if (
        len(line[: cell.start()]) - len(label) < 2
        or re.search(r"(?:^|\s)[-–+₹T]$", label)
        or line[cell.end() : second.start()].strip()
    ):
        return None
    return (
        label,
        _scalar(cell),
        {"raw_cell": cell[0], "columns": [cell.start(), cell.end()], "comparison": second[0]},
    )


def _caption(raw):
    label = re.sub(
        r"^\s*(?:(?:[IVXLCDM]+[.)]?|\d+[.)]?)\s+|(?:\([ivxlcdmIVXLCDM0-9a-z]+\)|[a-z]\))\s*)",
        "",
        raw.strip(),
    )
    label = re.sub(r"\s*[(](?:refer\s+)?note[s]?\s+[\d. ,]+[)]\s*$", "", label, flags=re.IGNORECASE)
    label = re.sub(
        r"\s*\((?:[IVXLCDM\d]+\s*[-+–/]\s*)+[IVXLCDM\d]+\)\s*$", "", label, flags=re.IGNORECASE
    )
    return " ".join(label.lower().split()).strip(" :")


def _kind(label, bank):
    if label == "total income" and bank:
        return "revenue"
    if not bank and re.fullmatch(
        r"(?:total )?(?:revenue|income) from operations(?: \(net\))?", label
    ):
        return "revenue"
    if not bank and label in {"finance costs", "finance cost", "financial costs", "financial cost"}:
        return "finance"
    if not bank and label in {"total expense", "total expenses", "total expenditure"}:
        return "expenses"
    if bank and re.fullmatch(
        r"operating profit (?:before|\(before) provisions (?:and|&) contingencies\)?", label
    ):
        return "operating_profit"
    if re.fullmatch(
        r"(?:net )?profit(?:\s*/\s*\(loss\))? (?:after tax )?for the (?:period|quarter)(?:/year)?(?: after tax)?",
        label,
    ):
        return "net_profit"
    return None


def _eps_label(label, section):
    if not section or not re.match(r"^(?:basic\b|basic and diluted\b)", label):
        return False
    eps_context = re.sub(
        r"(?:not |non[- ])annuali[sz]ed", "", label + " " + section, flags=re.IGNORECASE
    )
    if re.search(
        r"annuali[sz]ed|\b(?:ADR|ADS|GDR|preference shares?)\b", eps_context, re.IGNORECASE
    ):
        return False
    if _FOREIGN.search(label + " " + section):
        return False
    # A face-value denomination cannot supply the earnings unit.
    trailing = re.search(r"\(\s*(?:in\s+)?" + CASH + r"\s*\)\s*$", label, re.IGNORECASE)
    caption = re.search(
        r"earnings? per (?:equity )?share(?:\s*\(eps\))?\s*[(,:]?\s*(?:in\s+)?"
        + CASH
        + r"(?![.\d]|\s*\d)",
        section,
        re.IGNORECASE,
    )
    return bool(trailing or caption) and bool(
        re.fullmatch(r"basic(?: and diluted)?(?:\s*\((?:in\s+)?(?:rs\.?|inr|₹|rupees)\))?", label)
        or re.match(r"basic earnings? per (?:equity )?share\b", label)
    )


def _page(filing, text):
    lines = text.splitlines()
    target = date.fromisoformat(filing["period_end"])
    headings = [
        i for i, l in enumerate(lines[:45]) if re.search(r"\bfinancial results\b", l, re.IGNORECASE)
    ]
    if len(headings) != 1:
        return None
    title = headings[0]
    grid = _quarter(lines[title + 1 : title + 23], target)
    if not grid:
        return None
    grid["header_line"] += title + 1
    grid["date_line"] += title + 1
    header = lines[: grid["date_line"]]
    issuer = _issuer(filing, header[:title])
    basis = {
        x.lower()
        for x in re.findall(r"\b(?:standalone|consolidated)\b", "\n".join(header), re.IGNORECASE)
    }
    if (
        not issuer
        or basis != {filing["basis"]}
        or FORBIDDEN.search("\n".join(header))
        or _FOREIGN.search("\n".join(header))
    ):
        return None
    units = [(MONEY[m[1].lower()], s.strip()) for s in header if (m := UNIT.fullmatch(s.strip()))]
    unit = units[0] if units and len({u for u, _ in units}) == 1 else None
    bank = filing["tags"].get("revenue") in {"TotalIncome", "Income", "total income"}
    obs = defaultdict(list)
    eps_section = None
    total_profit_end = False
    split = bool(re.search(r"\b(?:continuing|discontinued) operations?\b", text, re.IGNORECASE))
    for i, line in enumerate(lines[grid["date_line"] :], grid["date_line"] + 1):
        if re.search(r"\b(?:segment reporting|cash flow statement)\b", line, re.IGNORECASE):
            break
        if re.search(
            r"\b(?:other comprehensive income|attributable to|non[- ]controlling interests)\b",
            line,
            re.IGNORECASE,
        ):
            total_profit_end = True
        section = re.search(r"earnings? per (?:equity )?share\b", line, re.IGNORECASE)
        if section and not re.search(
            r"\bbasic\b|\bdiluted\b", line[: section.start()], re.IGNORECASE
        ):
            eps_section = line.strip()
        if not re.search(
            r"revenue|income|expense|profit|finance|financial cost|basic", line, re.IGNORECASE
        ):
            continue
        row = _row(line, grid["centers"])
        if not row:
            continue
        raw, val, proof = row
        label = _caption(raw)
        kind = _kind(label, bank)
        if kind == "net_profit" and (total_profit_end or split):
            continue
        if _eps_label(label, eps_section) and not split:
            kind = "eps"
        if kind:
            obs[kind].append((val, {"line": i, "label": raw.strip(), **proof}))

    def one(k):
        witnesses = obs.get(k, [])
        return witnesses[0] if len(witnesses) == 1 else (None, None)

    actual = {}
    evidence = {}
    for k in FIELDS:
        if filing.get(k) is None or (k != "eps" and not unit):
            continue
        v, cell = one(k)
        if (
            k == "operating_profit"
            and not bank
            and filing["tags"].get(k) == "RevenueFromOperations-Expenses+FinanceCosts"
        ):
            pieces = [one(x) for x in ["revenue", "expenses", "finance"]]
            if all(n is not None for n, _ in pieces):
                v = pieces[0][0] - pieces[1][0] + pieces[2][0]
                cell = {
                    "arithmetic": "revenue - total expenses + finance costs",
                    "inputs": [p for _, p in pieces],
                }
        if v is not None:
            inr = v * (unit[0] if k != "eps" else 1)
            # A competing print contradicts even a value corroborated on another page.
            actual[k] = inr
            evidence[k] = {
                "issuer_heading": issuer,
                "scope": filing["basis"],
                "statement_heading": lines[title].strip(),
                "quarter": grid,
                "unit": eps_section if k == "eps" else unit[1],
                "cell": cell,
            }
    return actual, evidence


def corroborate(filing, documents):
    if not (filing.get("source_url") or "").lower().endswith(".xml") or not filing.get("tags"):
        return None
    amounts = defaultdict(list)
    for doc in documents:
        for page, text in enumerate(doc["pages"], 1):
            if "financial results" not in text.lower():
                continue
            parsed = _page(filing, text)
            if not parsed:
                continue
            actual, witness = parsed
            for k, amt in actual.items():
                amounts[k].append(
                    (
                        amt,
                        {
                            "pdf_sha256": doc["pdf_sha256"],
                            "page": page,
                            "page_text_sha256": digest(text.encode()),
                            **witness[k],
                        },
                    )
                )
    values = dict.fromkeys(FIELDS)
    proofs = {}
    for k, candidates in amounts.items():
        distinct = {n for n, _ in candidates}
        if len(distinct) == 1 and next(iter(distinct)) == Decimal(str(filing[k])):
            values[k] = float(candidates[0][0])
            proofs[k] = candidates[0][1]
    if not proofs:
        return None
    primary = proofs.get("eps") or next(iter(proofs.values()))
    return {
        "method": "explicit_inr_original_native_quarter_cells_cross_checked_with_xbrl",
        "values": values,
        "field_page_evidence": proofs,
        "pdf_sha256": primary["pdf_sha256"],
        "page": primary["page"],
        "basis": filing["basis"],
        "period_end": filing["period_end"],
    }
