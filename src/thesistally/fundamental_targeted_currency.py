"""Bind one original native revenue cell to its independently observed printed INR scale."""

import json
from decimal import Decimal

from .discovery_guard import REPO
from .discovery_registry import digest
from .fundamental_native_rows import FIELDS, _page

ACCESSION = "legacy/BPCL/INE029A01011/2021-12-31/standalone/1125748"
PDF = "34bef44abddf163f1dddbc7eddb0b00ae634e9e5fa2d910cf93d440a7f477853"
XML = "b4be9cd55b7344047a15874f36cd5d8d52e668f7bd9c7e30a5c086edcee30bd3"
RECEIPT = "4cb31492995cd7e924f695da69618d01a676236c69e8dfd1e5e9e18f238e6118"
ORIGINAL_URL = "https://nsearchives.nseindia.in/corporate/resultsdec211_31012022172039.pdf"
STRUCTURED_URL = (
    "https://nsearchives.nseindia.in/corporate/xbrl/INDAS_80869_601445_31012022114505_WEB.xml"
)
DATA = REPO / "data/fundamentals/nse/targeted-currency"


def explicit_unit(view):
    """The Hindi alphabet includes the rupee sign. Read the English scale independently."""
    hindi, english = (view["symbols"][k] for k in ("hin", "eng"))
    if not hindi or not english or len(hindi) < 2 or len(english) != len("(2inCrores)"):
        return False
    if english[0]["character"] != "(" or hindi[0]["character"] != "(":
        return False
    h, e = hindi[1], english[1]
    # The English recognizer confused this glyph with a digit. Its placement agrees
    # exactly with the unforced Hindi symbol boxes, before any letters of the scale.
    if not (
        h["character"] == "₹"
        and h["confidence"] >= 95
        and e["character"] in {"₹", "2"}
        and h["bounds"] == e["bounds"]
        and h["bounds"][2] < english[2]["bounds"][0]
    ):
        return False
    return (
        "".join(c["character"] for c in english[2:-1]) == "inCrores"
        and english[-1]["character"] in {")", "}"}
        and all(c["confidence"] >= 95 for c in english[2:-1])
    )


def corroborate_targeted_currency(filing, documents, release, package):
    if (
        filing.get("id") != ACCESSION
        or (filing.get("isin"), filing.get("period_end"), filing.get("basis"))
        != ("INE029A01011", "2021-12-31", "standalone")
        or filing.get("source_url") != STRUCTURED_URL
        or filing.get("structured_sha256") != XML
        or filing.get("tags", {}).get("revenue") != "RevenueFromOperations"
        or filing.get("revenue") is None
        or release.get("source_url") != ORIGINAL_URL
        or release.get("announced_at") != "2022-01-31T17:22:05+05:30"
        or package.get("sha256") != PDF
        or package.get("source_url") != ORIGINAL_URL
    ):
        return None
    raw = (DATA / "evidence.json").read_bytes()
    if digest(raw) != RECEIPT:
        raise ValueError("Changed targeted currency receipt")
    receipt = json.loads(raw)
    for name, path in {
        "capture_program_sha256": REPO / "research/capture_bpcl_currency.py",
        "page_parser_sha256": REPO / "src/thesistally/fundamental_native_rows.py",
    }.items():
        if digest(path.read_bytes()) != receipt[name]:
            raise ValueError("Changed source program for targeted currency witness")
    views = receipt["views"]
    if [v["size"] for v in views] != [2400, 3200, 4200]:
        raise ValueError("Incomplete targeted currency views")
    for v in views:
        if digest((DATA / f"legend-{v['size']}.png").read_bytes()) != v[
            "crop_sha256"
        ] or not explicit_unit(v):
            raise ValueError("Unverified original currency glyph or monetary scale")
    pages = [d["pages"][3] for d in documents if d["pdf_sha256"] == PDF and len(d["pages"]) >= 4]
    if len(pages) != 1 or digest(pages[0].encode()) != receipt["page_text_sha256"]:
        raise ValueError("Currency legend belongs to a different source page")
    native = pages[0]
    missing = '(" in Crores)'
    if native.count(missing) != 1:
        return None
    # Only the separately witnessed currency label enters a working copy; monetary
    # characters, quarter columns and the archived native text retain their original form.
    parsed = _page(filing, native.replace(missing, "(₹ in Crores)"))
    if not parsed or parsed[0].get("revenue") != Decimal(str(filing["revenue"])):
        return None
    cell = {
        **parsed[1]["revenue"],
        "pdf_sha256": PDF,
        "page": 4,
        "page_text_sha256": receipt["page_text_sha256"],
        "structured_sha256": XML,
        "currency_witness_sha256": RECEIPT,
        "currency_witness_file": "nse/targeted-currency/evidence.json",
        "native_unit": missing,
        "printed_unit": "(₹ in Crores)",
    }
    return {
        "method": "original_native_quarter_revenue_with_scoped_printed_rupee_witness",
        "page": 4,
        "pdf_sha256": PDF,
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "values": {**dict.fromkeys(FIELDS), "revenue": float(parsed[0]["revenue"])},
        "field_page_evidence": {"revenue": cell},
    }
