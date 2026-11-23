"""Read earlier NSE earnings disclosures omitted by the standard results and board categories."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

from .fundamental_notices import BOARD, NEGATIVE, RESULT
from .fundamental_nse import ROOT, members, published_at, write_json

SUMMARY = re.compile(
    r"Results Update,\s*(Quarter|Year)\s+ended,?\s*(\d{1,2}-[a-z]{3}-20\d{2})\s*\((Standalone|Consolidated)\)\s*$",
    re.IGNORECASE,
)
EXCLUDE = re.compile(
    r"analyst|news?\s*paper|paper clippings|paper cutting|notice (?:of|published)|meeting to consider|to declare|will declare|con[- ]?call|conference|earnings? call|webcast|transcript|investor meet|voting|postal ballot|trading window|investor presentation|audio recording|subsidiar|joint venture|other companies|by its associate|wholly[- ]owned|will (?:announce|publish|release)|to (?:announce|publish|release)|to be (?:announce|publish|release)|shall (?:announce|publish|release)|upcoming|proposed|invitation|schedule|intimation of (?:the )?board|financial projections|business update|operational update|forward.looking|unpublished price sensitive",
    re.IGNORECASE,
)
RELEASE = re.compile(
    r"\b(?:q[1-4](?:\s*(?:fy|cy)?[\s'’-]*\d{2,4}(?:-\d{2,4})?)?\s*(?:earnings|results|net profit|(?:gross |net )?revenue)|"
    r"(?:earnings|results)\s+(?:for |of )?(?:the )?q[1-4]\b|"
    r"(?:results|financial performance|financial statements) (?:for|of) (?:the )?(?:(?:first|second|third|fourth) )?quarter (?:ended|ending)|"
    r"(?:announces|reports|declares|posts).{0,80}\b(?:earnings|results|net profit).{0,40}\b(?:quarter|q[1-4])\b|"
    r"earnings release.{0,35}(?:quarter|q[1-4])\b|(?:quarter|q[1-4]).{0,35}earnings release)\b",
    re.IGNORECASE,
)
FISCAL_DAY = r"\d{1,2}-[a-z]{3}-20\d{2}"
NET = re.compile(
    rf"\b(Net Profit\s*/\s*\(Loss\))\s+of\s+(?:of\s+)?Rs\.?\s*(\(?-?[\d,]+(?:\.\d+)?\)?)\s+(lacs?|lakhs?|crores?|millions?)\s+for\s+the\s+quarter\s+ending\s+on\s+({FISCAL_DAY})\b",
    re.IGNORECASE,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def accession(row):
    return sha(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def classify(row):
    desc = str(row.get("desc") or "").strip()
    body = str(row.get("attchmntText") or "")
    if m := SUMMARY.fullmatch(desc):
        period = datetime.strptime(m[2], "%d-%b-%Y").date().isoformat()
        return {
            "release_language": "legacy_results_summary",
            "declared_basis": m[3].lower(),
            "declared_period_end": period,
            "declared_duration": m[1].lower(),
        }
    if not NEGATIVE.search(desc) and (
        RESULT.search(desc) or BOARD.search(desc) or RESULT.search(body)
    ):
        return None
    if EXCLUDE.search(desc + " " + body):
        return None
    if desc.lower() not in {"press release", "results", "updates", "general updates", "dividend"}:
        return None
    outcome = desc.lower() == "results" and re.search(
        r"outcome of (?:the )?board meeting", body, re.IGNORECASE
    )
    if not (RELEASE.search(body) or outcome):
        return None
    url = row.get("attchmntFile") or ""
    if not url.startswith("https://") and not re.search(r"<a\s+[^>]*href=", body, re.IGNORECASE):
        return None
    return {"release_language": "issuer_earnings_release"}


def read_period(receipt):
    path = ROOT / "catalogues" / (sha(receipt["url"].encode()) + ".json.gz")
    zipped = path.read_bytes()
    raw = gzip.decompress(zipped)
    if sha(raw) != receipt["sha256"]:
        raise ValueError("Market-wide notice response changed")
    return zipped, json.loads(raw)


def main():
    receipts = json.loads((ROOT / "notice-market-sources.json").read_bytes())
    oms = {r["symbol"] for r in members()}
    out, sources = [], []
    source_dir = ROOT / "notice-language-sources"
    source_dir.mkdir(exist_ok=True)
    for src in receipts:
        if src["kind"] != "period":
            continue
        zipped, rows = read_period(src)
        inline_body = False
        for r in rows:
            if r.get("symbol") not in oms or not (kind := classify(r)):
                continue
            stamp = published_at(r.get("exchdisstime")) or published_at(r.get("an_dt"))
            if not stamp:
                continue
            out.append(
                dict(
                    symbol=r["symbol"],
                    isin=r.get("sm_isin"),
                    announced_at=stamp.isoformat() + "+05:30",
                    description=r["desc"],
                    text=r.get("attchmntText") or "",
                    sequence=r.get("seq_id"),
                    source_url=None
                    if r.get("attchmntFile") in (None, "", "-")
                    else r["attchmntFile"],
                    catalogue_url=src["url"],
                    catalogue_sha256=src["sha256"],
                    accession_sha256=accession(r),
                    **kind,
                )
            )
            inline_body |= (
                kind["release_language"] == "legacy_results_summary"
                and kind["declared_duration"] == "quarter"
            )
        if inline_body:
            file = source_dir / (src["sha256"] + ".json.gz")
            if not file.exists():
                file.write_bytes(zipped)
            if sha(gzip.decompress(file.read_bytes())) != src["sha256"]:
                raise ValueError("Archived body source differs from the attested NSE response")
            sources.append({k: src[k] for k in ("url", "sha256", "bytes", "rows")})
    write_json(ROOT / "notice-language-rows.json", out)
    write_json(
        ROOT / "notice-language-manifest.json",
        {
            "period_blocks": len([x for x in receipts if x["kind"] == "period"]),
            "raw_market_census_sha256": sha((ROOT / "notice-market-sources.json").read_bytes()),
            "supplement_sha256": sha((ROOT / "notice-language-rows.json").read_bytes()),
            "counts": dict(
                Counter(
                    (r["release_language"] + "_" + r.get("declared_duration", "attachment"))
                    for r in out
                )
            ),
            "inline_body_sources": sources,
        },
    )


@lru_cache(maxsize=32)
def original_bodies(raw_sha):
    if not re.fullmatch("[a-f0-9]{64}", raw_sha):
        raise ValueError("Invalid original-body fingerprint")
    raw = gzip.decompress((ROOT / "notice-language-sources" / (raw_sha + ".json.gz")).read_bytes())
    if sha(raw) != raw_sha:
        raise ValueError("Original bulletin fingerprint differs")
    return {
        accession(r): r
        for r in json.loads(raw)
        if SUMMARY.fullmatch(str(r.get("desc") or "").strip())
    }


def exhaustive_body(public, filing):
    """Prove the entire official item contains only the enumerated amounts and no attachment."""
    if public.get("hasXbrl") or any(
        public.get(k) not in (None, "", "-", 0, "0", False)
        for k in ("attFilePath", "attFileSize", "attchmntFile", "fileSize", "csvName")
    ):
        return False
    body = str(public.get("attchmntText") or "")
    if re.search(r"<|https?://|www\.|attached|enclosed", body, re.IGNORECASE):
        return False
    end = datetime.strptime(filing["period_end"], "%Y-%m-%d").strftime("%d-%b-%Y")
    basis = filing["basis"]
    m = re.fullmatch(
        r"(.{3,150}?) has informed the Exchange regarding the "
        + basis
        + r" Results for the quarter ended on "
        + end
        + r" as follows:\s*(.*)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return False

    def norm(value):
        return re.sub(
            "[^a-z0-9]",
            "",
            re.sub(r"\bltd\b", "limited", str(value or "").lower()).replace("&", "and"),
        )

    if norm(m[1]) != norm(public.get("sm_name")):
        return False
    number = r"(?:\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?)"
    currency = r"(?:Rs\.?\s*)?" + number + r"(?:\s+(?:lacs?|lakhs?|crores?|millions?))?"
    comparator = (
        r"(?:\s+against\s+"
        + currency
        + r"(?:\s+and\s+"
        + currency
        + r")?\s+for (?:the )?quarters? ending on "
        + FISCAL_DAY
        + r"(?: and "
        + FISCAL_DAY
        + r" respectively)?)?"
    )

    def clause(label):
        return (
            label
            + r" of (?:of )?Rs\.?\s*"
            + number
            + r" (?:lacs?|lakhs?|crores?|millions?) for (?:the )?quarter ending on "
            + end
            + comparator
            + r"\.\s*"
        )

    expenses = clause("Interest earned") + clause("Interest expended")
    expected = (
        "(?:" + clause("Net Sales") + "|" + expenses + ")" + clause(r"Net Profit\s*/\s*\(Loss\)")
    )
    return bool(re.fullmatch(expected, m[2], re.IGNORECASE))


def inline_fact_proof(notice, filing):
    """Read the dated current-quarter net profit as printed. No amount comes from a comparison."""
    if (
        notice.get("release_language") != "legacy_results_summary"
        or notice.get("declared_duration") != "quarter"
        or notice.get("declared_period_end") != filing["period_end"]
        or notice.get("declared_basis") != filing["basis"]
        or notice.get("symbol") != filing["symbol"]
        or notice.get("isin") != filing["isin"]
    ):
        return None
    public = original_bodies(notice["catalogue_sha256"]).get(notice["accession_sha256"])
    if not public:
        raise ValueError("Announcement absent from signed bulletin")
    stamp = published_at(public.get("exchdisstime")) or published_at(public.get("an_dt"))
    if (
        public.get("symbol") != notice["symbol"]
        or public.get("sm_isin") != notice["isin"]
        or public.get("attchmntText") != notice["text"]
        or classify(public)
        != {
            k: notice[k]
            for k in (
                "release_language",
                "declared_duration",
                "declared_basis",
                "declared_period_end",
            )
        }
        or not stamp
        or stamp.isoformat() + "+05:30" != notice["announced_at"]
    ):
        raise ValueError("Original inline filing identity, time, or body changed")
    m = NET.search(notice["text"])
    if not m or datetime.strptime(m[4], "%d-%b-%Y").date().isoformat() != filing["period_end"]:
        return None
    amount = m[2].replace(",", "")
    if amount.startswith("(") and amount.endswith(")"):
        amount = "-" + amount[1:-1]
    scale = (
        10_000_000
        if m[3].lower().startswith("crore")
        else 1_000_000
        if m[3].lower().startswith("million")
        else 100_000
    )
    val = Decimal(amount) * scale
    exhausted = exhaustive_body(public, filing)
    return {
        "values": {
            "net_profit": float(val),
            "operating_profit": None,
            "revenue": None,
            "eps": None,
        },
        "basis": filing["basis"],
        "period_end": filing["period_end"],
        "source_url": notice["catalogue_url"],
        "sha256": notice["catalogue_sha256"],
        "accession_sha256": notice["accession_sha256"],
        "unit_inr": scale,
        "exhaustive_body": exhausted,
        "absent_fields": ["eps", "operating_profit"] if exhausted else [],
        "printed_field": m[1],
        "printed_net_profit": m[2],
        "current_quarter": m[4],
        "method": "exchange_announcement_body_explicit_current_quarter",
    }


if __name__ == "__main__":
    main()
