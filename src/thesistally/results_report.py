"""Render Strategy D's signed annual outcomes and filing coverage as Markdown."""

import io
import json
import sys
from pathlib import Path

from .discovery_guard import REPO
from .discovery_registry import digest
from .results_registry import withdrawal


def number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def render(path, root):
    raw = path.read_bytes()
    report = json.loads(raw)
    coverage_id = report["proof"]["coverage"]
    historic_coverage = root / "coverage-history" / (coverage_id + ".json")
    coverage_bytes = (
        historic_coverage if historic_coverage.is_file() else root / "coverage.json"
    ).read_bytes()
    if digest(coverage_bytes) != report["proof"]["coverage"]:
        raise ValueError("The report and the coverage table belong to different releases")
    coverage = json.loads(coverage_bytes)
    historic_signals = (
        root / "signal-history" / report["proof"]["signal_receipt"] / "signals-receipt.json"
    )
    signed = (
        historic_signals if historic_signals.is_file() else root / "signals-receipt.json"
    ).read_bytes()
    if digest(signed) != report["proof"]["signal_receipt"]:
        raise ValueError("The report and the signal census belong to different releases")
    plans = json.loads(signed)
    years = report["by_year_and_window"]
    complete = report["eligible_years"]
    print("# Strategy D · Post-earnings momentum\n")
    if withdrawn := withdrawal(root, coverage_id):
        print(
            f"**Withdrawn acquisition checkpoint.** {withdrawn['reason']} This edition may be used to reproduce the audit only.\n"
        )
    nse = coverage.get("filing_sources", {}).get("nse", {})
    acquisition = nse.get("source_acquisition", {})
    if acquisition:
        states = acquisition["by_status"]
        status = ", ".join(f"{n:,} {k.replace('_', ' ')}" for k, n in sorted(states.items()))
        title = "Completed source collection" if acquisition["complete"] else "Interim filing audit"
        print(
            f"**{title}.** The signed source census lists {acquisition['planned_urls']:,} "
            f"planned original attachments ({status}). The annual decisions below apply to this source edition.\n"
        )
    processing_file = root / "processing-history" / (coverage_id + ".json")
    if processing_file.is_file():
        processing = json.loads(processing_file.read_bytes())
        if (
            processing["filings_sha256"] != nse["sha256"]
            or processing["coverage_sha256"] != coverage_id
        ):
            raise ValueError("Original page evidence and coverage refer to different editions")
        legacy = processing["legacy"]
        print(
            f"The high-resolution check compared {legacy['attachments_checked']:,} original packages with legacy exchange "
            f"results tables. It evaluated {legacy.get('basis_quarters_checked', 0):,} issuer-quarter and accounting-basis "
            f"matches and verified at least one figure in {legacy.get('with_verified_fields', 0):,}. "
            f"[Original-page and BSE fallback evidence](../data/fundamentals/processing-history/{coverage_id}.json)\n"
        )
        modern = (
            processing.get("modern_originals", {}).get("cohorts", {}).get("modern-statement-pages")
        )
        if modern:
            print(
                f"For modern XBRL comparisons, the study selected {modern['nominated_page_reads']:,} "
                f"statement pages across {modern['original_packages']:,} original packets using document text. "
                f"It checked {modern['filings_with_reads']:,} structured filing records against the selected pages. "
                f"For {modern['filings_without_nominated_pages']:,} filings, no page met the selection criteria. "
                "The comparison can miss statements with unintelligible text. "
                f"[Modern source scope and field counts](../data/fundamentals/processing-history/{coverage_id}.json)\n"
            )
    elif acquisition.get("complete"):
        print(
            "**Interim numerical edition.** The public download is complete. Original-page review is still in progress for this coverage edition.\n"
        )
    print(
        f"**{len(complete)} of {len(years)} annual windows met the 80% filing coverage threshold.** "
        f"Across 2014 through {coverage['as_of']}, the archive verifies all four original figures "
        f"for {coverage['covered']:,} of {coverage['eligible']:,} eligible stock-quarters. "
        f"Verified original EPS was available for {coverage['known_fields']['eps']:,} eligible stock-quarters. "
        "[Filing coverage and gaps](FUNDAMENTALS_COVERAGE.md)\n"
    )
    if not complete:
        print(
            "Both the 40-session strategy and its 20-session sensitivity remain untested on a complete annual sample. "
            "The funded and independent books have no reportable trade count, gross or net expectancy, "
            "confidence interval, drawdown, loss diagnosis, benchmark excess or after-tax outcome.\n"
        )
    print("## Annual coverage and decisions\n")
    print(
        "| Test year | Test dates | Verified / eligible stock-quarters | Filing coverage | Decision |"
    )
    print("| --- | --- | ---: | ---: | --- |")
    for year, window in sorted(years.items()):
        q = window["filing_coverage"]
        label = "Replay" if q["coverage_eligible"] else "Excluded"
        print(
            f"| {year} | {window['start']} to {window['end']} | {q['covered']} / {q['eligible']} "
            f"| {q['coverage']:.1%} | {label} |"
        )
    print(
        "\nEach annual test has three training years and starts with a fresh ₹10 lakh reference account. "
        "The coverage audit uses the previous December and the current March, June and September quarter ends. "
        "The last test window ends on 20 November 2026.\n"
    )
    print("## Earnings signals\n")
    print(
        f"The archive contains {plans['originals']:,} original issuer-quarter and basis records including warm-up. "
        f"Of the first announcements from index members, {plans['funnel'].get('sue_scored', 0):,} supplied "
        f"a same-basis sequence long enough to compute the surprise. "
        f"{plans['plans']:,} prospective orders passed the surprise, relative reaction and signal-date liquidity checks "
        "across the available history. These counts precede annual data-coverage and execution checks.\n"
    )
    if plans["plans"]:
        import pandas as pd

        plan_file = historic_signals.parent / "signals.parquet"
        plan_bytes = plan_file.read_bytes()
        if digest(plan_bytes) != plans["signals_sha256"]:
            raise ValueError("Signal comparison sizes differ from the signed order plans")
        cohort = pd.read_parquet(
            io.BytesIO(plan_bytes), columns=["symbol", "entry_session", "peers", "sue"]
        )
        if len(cohort) != plans["plans"]:
            raise ValueError("Plan count differs from the signed signal census")
        sole = cohort.loc[cohort.peers == 1]
        if len(sole):
            print(
                f"For {len(sole):,} of {len(cohort):,} prospective orders, the issuer was the only announcement with a verifiable surprise in its 90-day ranking cohort. Its own score set the percentile threshold.\n"
            )
            if len(cohort) == 1:
                row = sole.iloc[0]
                print(
                    f"The single plan was {row.symbol}, due at the open on {row.entry_session}, with a standardized earnings surprise of {row.sue:+.3f}.\n"
                )
    distribution = {n: plans["funnel"].get(f"consecutive_original_eps_{n}", 0) for n in range(14)}
    if any(distribution.values()):
        print(
            "| Consecutive quarters with first-reported EPS on the selected basis | Member announcements |"
        )
        print("| --- | ---: |")
        for label, lo, hi in (
            ("0", 0, 0),
            ("1–4", 1, 4),
            ("5–8", 5, 8),
            ("9–12", 9, 12),
            ("13 or more", 13, 13),
        ):
            print(f"| {label} | {sum(distribution[n] for n in range(lo, hi + 1)):,} |")
        print(
            f"\n{plans['funnel'].get('zero_prior_change_variance', 0):,} otherwise complete histories had "
            "no variation in the eight prior changes and supplied no surprise score.\n"
        )
    print(
        "The rule needs thirteen dated EPS observations to compute eight prior year-over-year differences plus the current difference. "
        "It ranks surprises against other Nifty 200 announcements in the preceding 90 calendar days. "
        "The three-session stock return must exceed the Nifty 200 price-index return. "
        "The first possible order is the open two trading sessions after the release becomes usable. "
        "[Preregistration](PREREGISTRATION.md#d--post-earnings-momentum) · "
        f"[Signal receipt](../data/fundamentals/signal-history/{report['proof']['signal_receipt']}/signals-receipt.json)\n"
    )
    if complete:
        print("## Annual results for the two books\n")
        print(
            "Net and gross R use each call's original rupee risk. The interval is a 90% bootstrap clustered by signal date. "
            "The independent book can hold overlapping positions and its percentage figures use the initial reference capital.\n"
        )
        print(
            "| Year | Exit/book | Mature trades | Gross R | Net R | Net R interval | Drawdown % | Return % | TRI % | After tax % | TRI after tax % |"
        )
        print("| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        losses = []
        for year in complete:
            for key, book in sorted(years[year]["books"].items()):
                lo, hi = book.get("mean_net_r_ci90") or (None, None)
                tax = book.get("after_tax") or {}
                row = [
                    year,
                    key,
                    number(book["trades"], 0),
                    number(book["mean_gross_r"], 3),
                    number(book["mean_net_r"], 3),
                    f"{number(lo, 3)} to {number(hi, 3)}",
                    number(book["max_drawdown_pct"]),
                    number(book["strategy_return_pct"]),
                    number(book["benchmark_return_pct"]),
                    number((tax.get("strategy") or {}).get("after_tax_return_pct")),
                    number((tax.get("tri") or {}).get("after_tax_return_pct")),
                ]
                print("| " + " | ".join(row) + " |")
                if diag := book.get("loss_diagnosis"):
                    for reason, v in sorted(diag["exit_reasons"].items()):
                        losses.append((year, key, reason, v))
        print(
            "\nFor tax, the annual book realizes gains under the Strategy A Indian financial-year table. "
            "The comparison sells the Nifty 200 TRI equivalent once at each annual account's end. "
            "Each benchmark starts at the prior trading close. Missing exact tax inputs are recorded in the replay.\n"
        )
        print("### Exit reasons\n")
        print("| Year | Exit/book | Exit reason | Closed | Losers | Mean net R |")
        print("| --- | --- | --- | ---: | ---: | ---: |")
        for year, key, reason, v in losses:
            print(
                f"| {year} | {key} | {reason} | {v['closed']} | {v['losers']} | {number(v['mean_net_r'], 3)} |"
            )
        print(
            "\nThe machine report also gives distributions of favorable and adverse excursions, "
            "the share of losers reaching +1R and days to stop.\n"
        )
    print("## Qualification\n")
    for key, q in sorted(report["qualification"].items()):
        print(
            f"- **{key.replace('_', ' ')}** · {q['verdict']}. "
            f"{q['mature_closed_in_counted_windows']} mature trades qualified for the assessment across {len(q['counted_windows'])} counted windows."
        )
    print(
        "\nNSE is the primary clock. An earlier BSE release could move the eligible session when its original filing "
        "is unavailable. Raw EPS is retained as printed, so changes in units per share can distort the surprise. "
        "An ISIN change can also interrupt the thirteen-quarter series for a continuing issuer. "
        "Eicher Motors' August 2020 split is one verified example. "
        "Late revisions cannot repair an earlier signal. Older scans and first reports that later changed are "
        "harder to certify. Requiring thirteen consecutive original EPS figures can therefore favor issuers "
        "with consistently readable filings even in a year that passes the 80% four-field coverage threshold.\n"
    )
    relative = path.relative_to(REPO).as_posix()
    print(
        f"[All annual metrics, thresholds and receipts](../{relative}) · [Experiment register](EXPERIMENTS.md)"
    )


if __name__ == "__main__":
    render(Path(sys.argv[1]).resolve(), REPO / "data/fundamentals")
