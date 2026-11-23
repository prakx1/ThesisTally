"""Verify the original BSE quarterly XBRL facts against the registered Strategy D definitions."""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from .fundamental_facts import FIELDS, xml_facts

XBRL = "{http://www.xbrl.org/2003/instance}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
IX = {"http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL"}
TAXONOMY = re.compile(r"https?://(?:www\.)?(?:bseindia\.com|sebi\.gov\.in)/xbrl/", re.IGNORECASE)


def _qname(value, namespaces):
    prefix, sep, name = (value or "").partition(":")
    return (namespaces.get(prefix), name) if sep else (None, "")


def _inline_value(node, numeric, namespaces):
    # Only transform formats actually used for Indian financial facts. An unfamiliar
    # representation stays missing, even if its displayed text resembles a number.
    if node.get("continuedAt") or any(e.tag.endswith("}exclude") for e in node.iter()):
        return ""
    value = "".join(node.itertext()).strip()
    namespace, fmt = _qname(node.get("format"), namespaces)
    if node.get("format") and (
        not namespace or not namespace.startswith("http://www.xbrl.org/inlineXBRL/transformation/")
    ):
        return ""
    if not numeric:
        if not fmt:
            return value
        if fmt in {"datedaymonthyear", "date-day-month-year"}:
            parts = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", value)
            if parts:
                d, m, y = map(int, parts.groups())
                return date(y, m, d).isoformat()
        return ""
    if fmt not in {"", "numdotdecimal", "numdotdecimalin", "num-dot-decimal", "num-dot-decimal-in"}:
        return ""
    pattern = r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d+)?"
    if not re.fullmatch(pattern if fmt else r"\d+(?:\.\d+)?", value):
        return ""
    scale = int(node.get("scale", "0"))
    sign = node.get("sign", "")
    if not -20 <= scale <= 20 or sign not in {"", "-"}:
        raise ValueError("Unsupported inline scale or sign")
    return format(Decimal(sign + value.replace(",", "")).scaleb(scale), "f")


def _project(raw):
    # All used QName prefixes must have the same binding throughout this document.
    namespaces = {}
    parser = ET.iterparse(io.BytesIO(raw), events=("start-ns", "end"))
    for event, item in parser:
        if event == "start-ns":
            prefix, uri = item
            if prefix in namespaces and namespaces[prefix] != uri:
                namespaces[prefix] = None
            else:
                namespaces[prefix] = uri
    root = parser.root
    inline = root.tag in {"{http://www.w3.org/1999/xhtml}html", "html"}
    if root.tag != XBRL + "xbrl" and not inline:
        raise ValueError("Not an XBRL or inline XBRL instance")
    instance = ET.Element(XBRL + "xbrl")
    identity = {}
    unit_ids = set()
    for n in root.iter():
        if n.tag == XBRL + "context" and n.get("id") == "OneD":
            instance.append(n)
        if n.tag == XBRL + "unit":
            if n.get("id") in unit_ids:
                raise ValueError("Ambiguous currency unit")
            unit_ids.add(n.get("id"))
            _copy_unit(instance, n, namespaces)
        if n.get("contextRef") != "OneD" or n.get(XSI + "nil") in {"true", "1"}:
            continue
        ns, _, tag = n.tag.lstrip("{").partition("}")
        if inline:
            if ns not in IX or tag not in {"nonNumeric", "nonFraction"}:
                continue
            ns, concept = _qname(n.get("name"), namespaces)
            text = _inline_value(n, tag == "nonFraction", namespaces)
        else:
            concept, text = tag, (n.text or "").strip()
        if not ns or not TAXONOMY.match(ns) or not text:
            continue
        attrs = {"contextRef": "OneD"}
        if n.get("unitRef"):
            attrs["unitRef"] = n.get("unitRef")
        ET.SubElement(instance, concept, attrs).text = text
        if concept in {
            "ISIN",
            "ScripCode",
            "NatureOfReportStandaloneConsolidated",
            "ReportingQuarter",
        }:
            identity.setdefault(concept, set()).add(text)
    return instance, identity, inline


def _copy_unit(instance, unit, namespaces):
    def measure(node):
        ns, value = _qname(node.text, namespaces)
        if ns == "http://www.xbrl.org/2003/iso4217" and value == "INR":
            return value
        if ns == XBRL[1:-1] and value == "shares":
            return value
        return None

    # INR/share must be a division, not two flat measures with those labels.
    flat = unit.find(XBRL + "measure")
    if flat is not None and len(unit) == 1 and measure(flat) == "INR":
        instance.append(unit)
    divide = unit.find(XBRL + "divide")
    if divide is not None and len(divide) == 2 and len(unit) == 1:
        num = divide.findall(f"{XBRL}unitNumerator/{XBRL}measure")
        den = divide.findall(f"{XBRL}unitDenominator/{XBRL}measure")
        if len(num) == len(den) == 1 and measure(num[0]) == "INR" and measure(den[0]) == "shares":
            instance.append(unit)


def verify_facts(
    raw: bytes, *, symbol: str, isin: str, period_end: str, basis: str, scrip_code: str
) -> tuple[dict | None, str]:
    """Return one complete INR quarter certified by filing ISIN, scrip, scope and duration."""
    try:
        root, identity, inline = _project(raw)
        if not isin or identity.get("ISIN") != {isin}:
            return None, "missing or conflicting filing ISIN"
        if not re.fullmatch(r"[0-9]{6}", scrip_code) or identity.get("ScripCode") != {scrip_code}:
            return None, "missing or conflicting BSE scrip code"
        if basis not in {"consolidated", "standalone"} or {
            text.lower() for text in identity.get("NatureOfReportStandaloneConsolidated", ())
        } != {basis}:
            return None, "missing or conflicting reporting basis"
        ctx = root.findall(XBRL + "context")
        if len(ctx) != 1 or [n.text for n in ctx[0].iter(XBRL + "identifier")] != [scrip_code]:
            return None, "missing or conflicting dated BSE issuer context"
        row = {
            "symbol": symbol,
            "isin": isin,
            "period_end": period_end,
            "basis": basis,
            "metadata": {},
        }
        values, tags = xml_facts(ET.tostring(root), row)
        if any(values[field] is None for field in FIELDS):
            missing = ",".join(k for k in FIELDS if values[k] is None)
            return None, "missing required current-quarter facts: " + missing
        if all(values[field] == 0 for field in FIELDS) and not any(
            "quarter" in text.lower() for text in identity.get("ReportingQuarter", ())
        ):
            return None, "all-zero column without an explicitly reported quarter"
        return {
            **values,
            "tags": tags,
            "isin": isin,
            "scrip_code": scrip_code,
            "period_end": period_end,
            "basis": basis,
            "format": "ixbrl" if inline else "xbrl",
        }, "verified"
    except (ValueError, TypeError, ArithmeticError, LookupError, ET.ParseError) as exc:
        return None, str(exc)
