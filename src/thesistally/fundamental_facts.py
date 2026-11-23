"""Deterministic extraction of the issuer's own current-quarter column and units."""

from __future__ import annotations

import itertools
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from .fundamental_nse import dt, members

FIELDS = ("revenue", "operating_profit", "net_profit", "eps")
MONEY = ("revenue", "operating_profit", "net_profit")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def number(s):
    if not s:
        return None
    s = s.strip().replace(",", "").replace("−", "-")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        v = Decimal(s)
        return float(v) if v.is_finite() else None
    except InvalidOperation:
        return None


def has_quarter(start, end):
    return start and end and 75 <= (end - start).days <= 100


@lru_cache(maxsize=1)
def symbol_intervals():
    from collections import defaultdict

    out = defaultdict(list)
    for r in members():
        out[r["isin"]].append((r["from_date"], r["to_date"], r["symbol"], r["source_url"]))
    return out


def confirmed_symbol(row, reported):
    if reported == row["symbol"]:
        return {"symbol": reported}
    if not reported or row["metadata"].get("isin") != row.get("isin"):
        return None
    cert = row.get("source_security_proof")
    if (
        cert
        and cert["symbol"] == reported
        and cert["isin"] == row["isin"]
        and cert["period_end"] == row["period_end"]
    ):
        return cert
    dated = [
        (s, url) for a, b, s, url in symbol_intervals()[row["isin"]] if a <= row["period_end"] <= b
    ]
    if {s for s, _ in dated} == {reported}:
        return {"symbol": reported, "dated_isin": row["isin"], "membership_source": dated[0][1]}
    return None


def xml_facts(raw, row):
    root = ET.fromstring(raw)
    if local(root.tag).lower() != "xbrl":
        raise ValueError("Not XBRL")
    end = date.fromisoformat(row["period_end"])
    # The Indian results taxonomy reserves OneD for the current reporting period.
    # FourD commonly repeats OneD's XML period but actually contains year-to-date values.
    ctx = next((n for n in root if local(n.tag) == "context" and n.get("id") == "OneD"), None)
    if ctx is not None and any(
        local(n.tag) in ("explicitMember", "typedMember") for n in ctx.iter()
    ):
        raise ValueError("Current-period context is dimensioned")
    data = {}
    for n in root:
        if n.get("contextRef") == "OneD":
            key = local(n.tag)
            value = ((n.text or "").strip(), n.get("unitRef"))
            if key in data and data[key] != value:
                data[key] = ("", None)
            else:
                data[key] = value
    context_source = "OneD"
    if ctx is None:
        # Some original NSE banking documents omit the OneD declaration, despite
        # referring to it for every current-period scalar. Require independent
        # agreement of the dated catalogue and every current-period segment context.
        m = row.get("metadata", {})
        reference = dt(m.get("fromDate"))
        if (
            m.get("period") != "Quarterly"
            or m.get("cumulative") != "Non-cumulative"
            or not reference
            or not confirmed_symbol(row, data.get("Symbol", (None,))[0])
            or data.get("DateOfEndOfReportingPeriod", (None,))[0] != str(end)
        ):
            raise ValueError("Current-quarter duration has no supporting contexts")
        contexts = [
            n
            for n in root
            if local(n.tag) == "context"
            and n.get("id", "").startswith("One")
            and n.get("id", "").endswith("D")
        ]
        for context in contexts:
            fields = {local(n.tag): n.text for n in context.iter()}
            if (
                fields.get("identifier") != data.get("Symbol", (None,))[0]
                or fields.get("startDate") != str(reference.date())
                or fields.get("endDate") != str(end)
            ):
                raise ValueError("Reporting contexts disagree with quarterly catalogue")
        if not contexts:
            raise ValueError("Missing independently dated current quarter")
        starts, ends = [str(reference.date())], [str(end)]
        context_source = "NSE quarterly duration plus current segment contexts"
    else:
        starts = [n.text for n in ctx.iter() if local(n.tag) == "startDate"]
        ends = [n.text for n in ctx.iter() if local(n.tag) == "endDate"]
    explicit_start = data.get("DateOfStartOfReportingPeriod", (None,))[0]
    explicit_end = data.get("DateOfEndOfReportingPeriod", (None,))[0]
    if (
        len(starts) != 1
        or len(ends) != 1
        or ends[0] != str(end)
        or not has_quarter(date.fromisoformat(starts[0]), end)
        or (explicit_start and explicit_start != starts[0])
        or (explicit_end and explicit_end != ends[0])
    ):
        raise ValueError("Not the registered three-month duration")
    basis = data.get("NatureOfReportStandaloneConsolidated", ("",))[0].lower().strip()
    if basis and basis != row["basis"]:
        raise ValueError("Reporting basis mismatch")
    issuer = data.get("ISIN", ("",))[0]
    if (issuer or row.get("identity") == "require_original_isin") and issuer != row["isin"]:
        raise ValueError("Issuer ISIN disagrees with certificate")
    reported_symbol = data.get("Symbol", (None,))[0]
    if (
        reported_symbol
        and reported_symbol != row["symbol"]
        and issuer != row.get("isin")
        and not confirmed_symbol(row, reported_symbol)
    ):
        raise ValueError("Structured ticker differs without a dated alias or exact ISIN")
    units = {
        n.get("id"): [
            s.text.split(":")[-1] for s in n.iter() if local(s.tag) == "measure" and s.text
        ]
        for n in root
        if local(n.tag) == "unit"
    }

    def first(tags, per_share=False):
        for key in tags:
            text, unit = data.get(key, ("", None))
            permitted = [["INR", "shares"]] if per_share else [["INR"]]
            if units.get(unit) in permitted and number(text) is not None:
                return number(text), key
        return None, None

    bank = any(
        k in data
        for k in (
            "OperatingProfitBeforeProvisionAndContingencies",
            "OperatingProfitBeforeProvisionsAndContingencies",
            "InterestExpended",
        )
    )
    insurer = "GrossPremiumIncome" in data or "NetPremiumIncome" in data
    revenue = (
        first(("Income", "TotalIncome"))
        if bank
        else first(
            (
                "RevenueFromOperations",
                "TotalRevenueFromOperations",
                "IncomeFromOperations",
                "TotalIncomeFromOperationsNet",
                *(["NetPremiumIncome"] if insurer else []),
            )
        )
    )
    net = first(
        (
            "ProfitLossForPeriod",
            "ProfitLossForThePeriod",
            "NetProfitLossForThePeriod",
            "ProfitLossAfterTaxAndExtraordinaryItems",
        )
    )
    eps = first(
        (
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsPerShareAfterExtraordinaryItems",
            "BasicEarningsLossPerShare",
            "BasicEarningsPerShare",
            "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",
        ),
        True,
    )
    op = first(
        (
            "OperatingProfitBeforeProvisionAndContingencies",
            "OperatingProfitBeforeProvisionsAndContingencies",
            "ProfitLossFromOperationsBeforeOtherIncomeFinanceCostsAndExceptionalItems",
        )
    )
    if op[0] is None:
        expenses = first(("Expenses", "TotalExpenses"))
        finance = first(("FinanceCosts",))
        if not bank and not insurer and all(i[0] is not None for i in (revenue, expenses, finance)):
            # EBIT on operating revenue, including depreciation and excluding other income.
            op = (revenue[0] - expenses[0] + finance[0], f"{revenue[1]}-{expenses[1]}+{finance[1]}")
    pairs = dict(zip(FIELDS, (revenue, op, net, eps), strict=True))
    tags = {k: t for k, (_, t) in pairs.items() if t}
    tags["quarter_context"] = context_source
    reported = data.get("Symbol", (None,))[0]
    if reported and (alias := confirmed_symbol(row, reported)) and "membership_source" in alias:
        tags["dated_symbol_identity"] = alias
    return {k: v for k, (v, _) in pairs.items()}, tags


class Cells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
        self.active = False
        self.text = []

    def close_cell(self):
        if self.active:
            self.values.append(" ".join("".join(self.text).split()))
        self.text = []
        self.active = False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self.close_cell()
            self.active = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.close_cell()

    def handle_data(self, data):
        if self.active:
            self.text.append(data)


def html_facts(raw, row):
    metadata = row["metadata"]
    if row.get("identity") == "require_original_isin":
        raise ValueError("Old HTML lacks a current-quarter ISIN for catalogue alias")
    start = dt(metadata.get("fromDate"))
    end = dt(metadata["toDate"])
    if (
        not start
        or not end
        or not has_quarter(start.date(), end.date())
        or metadata.get("period") != "Quarterly"
    ):
        raise ValueError("HTML table is cumulative or annual")
    parser = Cells()
    parser.feed(raw.decode("latin-1"))
    cells = [" ".join(s.lower().split()) for s in parser.values if s]

    if cells == ["no data available"]:
        raise ValueError("Exchange HTML table explicitly reports no data available")

    def meta(label):
        try:
            return cells[cells.index(label) + 1]
        except (ValueError, IndexError):
            return ""

    if (
        not confirmed_symbol(row, meta("symbol").upper())
        or meta("period ended") != metadata["toDate"].lower()
    ):
        raise ValueError("HTML identity/quarter mismatch")
    expected_scope = {
        "non-consolidated": "standalone",
        "standalone": "standalone",
        "consolidated": "consolidated",
    }
    if expected_scope.get(meta("consolidated / non-consolidated")) != row["basis"]:
        raise ValueError("HTML accounting basis disagrees with the issuer table")
    if meta("period") != "quarterly" or meta("cumulative / non-cumulative") != "non-cumulative":
        raise ValueError("HTML table does not identify a separate non-cumulative quarter")
    if not any("lakhs" in s and "amount" in s for s in cells):
        raise ValueError("Unverified HTML monetary unit")
    found = {}
    tags = {}
    eps_kind = ""
    numeric = dict(itertools.pairwise(cells))
    for i, k in enumerate(cells[:-1]):
        if "segment reporting" in k:
            break
        val = number(cells[i + 1])
        if "earnings per share" in k:
            eps_kind = "after" if "after extraordinary" in k else ""
        if val is None:
            continue
        key = None
        if (
            k.startswith(("total income from operations", "net income from sales / services"))
            or metadata.get("bank") == "B"
            and k == "total income"
        ):
            key = "revenue"
        elif k.startswith(
            (
                "profit / (loss) from operations before other income",
                "operating profit before provisions",
                "operating profit before interest",
            )
        ):
            key = "operating_profit"
        elif re.match(
            r"net profit\s*(?:\(\+\))?\s*/\s*\(?loss\)?\s*(?:\(-\))?\s*for the period", k
        ):
            key = "net_profit"
        elif (eps_kind == "after" and k in ("(a) basic", "basic")) or k.startswith(
            "basic eps after extraordinary"
        ):
            key = "eps"
        if key:
            found[key] = val if key == "eps" else val * 100000
            tags[key] = k
    if "operating_profit" not in found and "revenue" in found:
        exp = number(numeric.get("total expenses"))
        fin = next(
            (
                number(value)
                for label, value in numeric.items()
                if re.fullmatch(r"(?:\([a-z]\)\s*)?finance costs", label)
            ),
            None,
        )
        if exp is not None and fin is not None:
            found["operating_profit"] = found["revenue"] - (exp - fin) * 100000
            tags["operating_profit"] = "revenue - total expenses + finance costs"
    return {k: found.get(k) for k in FIELDS}, tags
