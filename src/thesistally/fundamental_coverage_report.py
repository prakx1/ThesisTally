"""Print coverage tables from a single immutable quarterly filings edition."""

import csv
import gzip
import io
import json
from collections import Counter

from .discovery_registry import digest
from .fundamental_coverage import ROOT

SECTION = "## Results by year"
GROUPS = {
    "no_attributed_formal_or_board_record": "No formal result or board notice could be attributed to this ISIN and quarter",
    "attachment_unavailable": "A listed structured or original release attachment was unavailable",
    "unresolved_fields_period_or_identity": "Original figures, quarter column, reporting period or issuer could not be certified",
    "unverified_original_or_timing": "First-publication status or usable time could not be verified",
    "attachment_or_revision_undistinguished": "An unavailable attachment could not be distinguished from a revised notice",
}


def acquisition_note(source):
    status = source.get("source_acquisition")
    if status is None:
        return (
            "This edition predates the pinned attachment census. Its acquisition status is unknown."
        )
    if status["complete"]:
        summary = "The official attachment acquisition pass finished."
    else:
        summary = (
            "**Interim coverage.** Attachment acquisition was still in progress at this checkpoint."
        )
    counts = ", ".join(
        f"{n:,} {k.replace('_', ' ')}" for k, n in sorted(status["by_status"].items())
    )
    return (
        f"{summary} The signed plan counted {status['planned_urls']:,} URLs ({counts}). "
        f"[Source census](../data/fundamentals/nse/checkpoints/{source['sha256']}/"
        f"{source['source_census_sha256']}/index.json)"
    )


def product_csv(root, receipt, name):
    sha = receipt["products"][name]
    raw = gzip.decompress((root / "coverage-history/objects" / (sha + ".gz")).read_bytes())
    if digest(raw) != sha:
        raise ValueError(f"Coverage evidence differs from the recorded {name}")
    return list(csv.DictReader(io.StringIO(raw.decode())))


def bse_results(root, coverage_id):
    seal = root / "processing-history" / (coverage_id + ".json")
    if not seal.is_file():
        return []
    record = json.loads(seal.read_bytes())
    if record.get("coverage_sha256") != coverage_id:
        raise ValueError("BSE source review belongs to another coverage edition")
    summary = record["bse_fallback"]
    if "receipt" not in summary:
        return []
    raw = (root / summary["receipt"]).read_bytes()
    if digest(raw) != summary["receipt_sha256"]:
        raise ValueError("BSE cross-exchange recheck differs from the processing seal")
    study = json.loads(raw)
    s = study["original_packages"]["counts"]
    per_link = study["linked_bse_sources"]["per_link"]
    classes = Counter(v["document_purpose"] for v in per_link.values())
    links = len(per_link)
    lines = [
        "",
        "## BSE cross-exchange results",
        "",
        (
            f"The final check covered {s['original_packages']:,} original NSE packages, "
            f"{s.get('native_pages', 0):,} pages of native text, {s.get('optical_pages', 0):,} OCR page reads and "
            f"{s.get('original_pdf_annotation_reads', 0):,} original PDF hyperlink checks. The official market "
            f"bulletins contributed {summary['eligible_symbol_announcements']:,} announcements for the study symbols. "
            f"The check was completed at {summary['checked_at_utc']} and identified {links:,} distinct direct BSE links. "
            f"{summary['verified_bse_clocks']:,} original BSE dissemination timestamps were verified. "
            f"[Per-link evidence and timestamps](../data/fundamentals/{summary['receipt']})"
        ),
    ]
    if classes:
        lines.extend(["", "| Linked document assessment | Distinct BSE URLs |", "| --- | ---: |"])
        lines.extend(f"| {label} | {count:,} |" for label, count in sorted(classes.items()))
    lines.extend(
        [
            "",
            (
                f"{summary['unresolved_nse_sources']:,} planned NSE original URLs lacked a usable source. "
                f"{s.get('sparse_native_pages_without_readable_optical_text', 0):,} source page reads had fewer than "
                f"50 native or optically recognized characters. Company signature dates, board meeting times, "
                f"PDF download times and dates on later BSE or NSE documents were excluded as evidence of first exchange publication."
            ),
        ]
    )
    return lines


def render(root, narrative):
    raw = (root / "coverage.json").read_bytes()
    edition = digest(raw)
    if (root / "coverage-history" / (edition + ".json")).read_bytes() != raw:
        raise ValueError("Coverage report lacks its immutable receipt")
    c = json.loads(raw)
    rows = product_csv(root, c, "quarter-coverage.csv")
    stocks = [r for r in product_csv(root, c, "stock-coverage.csv") if int(r["eligible"])]
    lines = [
        narrative.split("\n" + SECTION + "\n")[0].rstrip(),
        "",
        SECTION,
        "",
        acquisition_note(c["filing_sources"]["nse"]),
        "",
        (
            f"Through {c['as_of']}, {c['covered']:,} of {c['eligible']:,} eligible security-quarters "
            f"had four verified first-reported fields ({c['covered'] / c['eligible']:.2%}). "
            f"{sum(w['coverage_eligible'] for w in c['windows'].values())} of {len(c['windows'])} "
            f"walk-forward windows reached 80%. [Coverage receipt](../data/fundamentals/coverage-history/{edition}.json)"
        ),
        "",
        "| Year | Quarter-end cohort covered / eligible | Coverage | Filing-season covered / eligible | Coverage | Window status |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for year, cohort in sorted(c["years"].items()):
        window = c["windows"].get(year)
        prefix = f"| {year} | {cohort['covered']:,} / {cohort['eligible']:,} | {cohort['coverage']:.1%} |"
        if window:
            status = "Complete" if window["coverage_eligible"] else "Incomplete"
            lines.append(
                f"{prefix} {window['covered']:,} / {window['eligible']:,} | {window['coverage']:.1%} | {status} |"
            )
        else:
            lines.append(f"{prefix} — | — | Development period |")
    kwil = next(r for r in rows if r["isin"] == "INE2KCE01013" and r["period_end"] == "2025-12-31")
    window = c["windows"]["2026"]
    num = window["covered"] - (kwil["covered_by_window_end"] == "True")
    den = window["eligible"] - 1
    changed = (num * 5 >= den * 4) != window["coverage_eligible"]
    lines.extend(
        [
            "",
            (
                "The December 2025 official membership contained the demerger placeholder INE2KCE01013 "
                "(DUMMYHDLVR, later KWIL). The registered denominator includes it. Removing it would leave "
                f"{c['eligible'] - 1:,} historical quarters and {num} / {den} ({num / den:.2%}) for 2026. "
                f"The 2026 window classification {'would change' if changed else 'would stay the same'}."
            ),
            "",
            "## Field coverage and gaps",
            "",
            "| First-reported field | Eligible security-quarters |",
            "| --- | ---: |",
        ]
    )
    for field, count in c["known_fields"].items():
        name = "Basic EPS" if field == "eps" else field.replace("_", " ").capitalize()
        lines.append(f"| {name} | {count:,} |")
    lines.extend(
        [
            "",
            (
                f"Among eligible quarters, {c['eps_known_while_four_field_coverage_missing']:,} had EPS and lacked a full set of four fields. "
                f"EPS was the only attested field for {c['eps_is_only_known_field']:,}. Across both accounting bases, warmup quarters and "
                f"stocks outside the quarter-end universe, {c['canonical_eps_basis_records_including_warmup']:,} canonical filings had verified EPS."
            ),
            "",
            "| Gap among incomplete quarters (groups can overlap) | Security-quarters |",
            "| --- | ---: |",
        ]
    )
    for key, label in GROUPS.items():
        lines.append(f"| {label} | {c['practical_gap_groups'].get(key, 0):,} |")
    lines.extend(
        [
            "",
            "The following historical gap reasons were the most common. The per-quarter ledger links each observed quarter and gap to its attributed source where available. [Quarter-level evidence](../data/fundamentals/quarter-coverage.csv)",
            "",
            "| Gap reason | Security-quarters |",
            "| --- | ---: |",
        ]
    )
    reasons = Counter(s for r in rows for s in r["gap"].split("|") if s)
    for reason, n in reasons.most_common(12):
        lines.append(f"| {reason.replace('_', ' ')} | {n:,} |")
    lines.extend(
        [
            "",
            "## Coverage by filing format and stock",
            "",
            "A stock-quarter can have multiple submission formats. The second count requires an original complete set from a filing recorded in that format. The final two columns count gaps on uncovered quarters and may overlap.",
            "",
            "| Official source format | Eligible security-quarters with format | Complete in format | Unavailable attachment | Unresolved facts, period or identity |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for fmt, v in c.get("candidate_formats", {}).items():
        lines.append(
            f"| {fmt} | {v['eligible_with_format']:,} | {v['certified_original_in_this_format']:,} | {v['gap_attachment_unavailable']:,} | {v['gap_facts_or_identity']:,} |"
        )
    if "candidate_formats" not in c:
        lines.append("| Format breakdown was unavailable in this edition | — | — | — | — |")
    misses = {r["isin"]: int(r["eligible"]) - int(r["covered"]) for r in stocks}
    largest = max(misses.values())
    held = sorted(r["symbols"] for r in stocks if misses[r["isin"]] == largest)
    lines.extend(
        [
            "",
            (
                f"Of {len(stocks)} securities with quarter-end membership, {sum(int(r['covered']) == 0 for r in stocks)} had no fully covered quarter. "
                f"The largest per-security gap was {largest} quarters. {len(held)} shared it, including {', '.join(held[:5])}. "
                "[Each stock by source format](../data/fundamentals/stock-format-coverage.csv) · [Each stock by year](../data/fundamentals/stock-year-coverage.csv)"
            ),
        ]
    )
    lines.extend(bse_results(root, edition))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(
        render(ROOT / "data/fundamentals", (ROOT / "docs/FUNDAMENTALS_COVERAGE.md").read_text()),
        end="",
    )
