"""Publish the Stage 2 baseline from completed replay evidence. No scans or orders."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from thesistally.baseline_funnel import momentum_funnel
from thesistally.baseline_metrics import analyze
from thesistally.baseline_replay import certify_inputs
from thesistally.evaluation import walk_windows
from thesistally.pilot_data import write_json
from thesistally.signals import rules

STRATEGIES = {
    "trend_pullback": "Trend pullback",
    "vcp_breakout": "Volatility breakout",
    "high_momentum": "52-week-high momentum",
}
BOOKS = {"funded": "Funded", "equal_risk": "Equal risk"}
REVISIONS = ("r2", "r1")


def number(value, places=3):
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return f"{value:,.{places}f}"


def percent(value):
    return "—" if value is None else f"{100 * value:.1f}%"


def table(columns, rows):
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        if len(row) != len(columns):
            raise ValueError("Inconsistent baseline table fields")
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in row) + " |")
    return "\n".join(lines)


def groups(report):
    for rev in REVISIONS:
        for book, book_title in BOOKS.items():
            for strategy, title in STRATEGIES.items():
                yield (
                    rev,
                    book_title,
                    title,
                    report["revisions"][rev]["books"][book]["strategies"][strategy],
                )


def interval(bounds):
    return "—" if bounds[0] is None else f"{number(bounds[0])} to {number(bounds[1])}"


def range3(dist):
    return " / ".join(number(dist[k], 2) for k in ("p10", "median", "p90")) if dist["n"] else "—"


def terminal_summary(report):
    lines = [
        "Stage 2 historical baseline · r2 primary",
        f"{report['start']} to {report['end']} · {report['sessions']:,} exchange sessions",
        "Fresh ₹10 lakh portfolio each test year. Equal risk budgets ₹5,000 per eligible call.",
    ]
    for rev, book, title, data in groups(report):
        s, q = data["overall"], data["qualification"]
        lines.append(
            f"{rev}  {book:<10}  {title:<22}  {s['closed']:>5} closed  {number(s['mean_net_r'])}R  {q['verdict']}"
        )
    lines.extend(
        [
            "Final test begins with new forward paper sessions after freeze.",
            "Full tables and data gaps in docs/BASELINE_REPORT.md.",
        ]
    )
    return "\n".join(lines)


def verdict_table(report):
    rows = []
    for rev, book, title, data in groups(report):
        q, s = data["qualification"], data["overall"]
        checks = q["criteria"]
        rows.append(
            [
                rev,
                book,
                title,
                s["closed"],
                q["mature_closed_all"],
                q["mature_closed_in_counted_windows"],
                number(checks["mean_r"]["value"]),
                interval(q["mean_net_r_ci90"]),
                number(checks["profit_factor"]["value"], 2),
                f"{len(q['positive_counted_windows'])}/{q['total_planned_windows']}",
                q["verdict"],
            ]
        )
    return table(
        [
            "Revision",
            "Book",
            "Strategy",
            "All closed",
            "Mature",
            "In 20+ windows",
            "Mature mean R¹",
            "90% CI¹",
            "PF¹",
            "Positive windows / planned",
            "Verdict",
        ],
        rows,
    )


def loss_tables(report):
    costs, paths, exits = [], [], []
    for rev, book, title, data in groups(report):
        s = data["overall"]
        costs.append(
            [
                rev,
                book,
                title,
                s["closed"],
                percent(s["win_rate"]),
                number(s["mean_holding_sessions"], 1),
                number(s["mean_gross_r"]),
                number(s["mean_slippage_r"]),
                number(s["mean_fee_r"]),
                number(s["mean_cost_r"]),
                number(s["mean_net_r"]),
                number(s["profit_factor"], 2),
            ]
        )
        reach, stop = s["losers_reached_1r"], s["days_to_stop_losers"]
        paths.append(
            [
                rev,
                book,
                title,
                range3(s["mfe_r"]),
                range3(s["mae_r"]),
                f"{reach['reached']}/{reach['losers']} ({percent(reach['share'])})",
                f"{reach['before_price_stop_session']} / {reach['on_price_stop_session']} / {reach['after_price_stop_session']}",
                reach["same_session_as_stop_first_collision"],
                stop["losers_with_price_stop"],
                range3(stop["trigger_sessions"]),
                range3(stop["exit_sessions"]),
            ]
        )
        reasons = s["exit_reasons"]
        if not reasons:
            exits.append([rev, book, title, "No closed trades", 0, 0, "—", "—"])
        for reason, item in reasons.items():
            exits.append(
                [
                    rev,
                    book,
                    title,
                    reason,
                    item["closed"],
                    item["losers"],
                    number(item["mean_gross_r"]),
                    number(item["mean_net_r"]),
                ]
            )
    return [
        "## What each closed trade earned and cost",
        "Gross R reverses modeled slippage and actual simulated fees on the executed quantities. Corporate cash is included. Results are before holder-specific income taxes. All four terms use initial filled share risk times quantity. Gross R less slippage R and fee R equals net R on every closed trade. Reversing slippage holds the executed trades fixed. Profit factor divides net rupee gains by absolute net rupee losses.",
        table(
            [
                "Revision",
                "Book",
                "Strategy",
                "Closed",
                "Wins",
                "Mean sessions held",
                "Gross R",
                "Slippage R",
                "Fee R",
                "Total cost R",
                "Net R",
                "PF",
            ],
            costs,
        ),
        "## How losing trades unfolded",
        "Excursions are in units of initial filled risk. The three values in a distribution are the 10th percentile, median and 90th percentile. Positive excursion uses highs while shares were still held. Negative excursion uses lows, with separately reported bounds for a single-share target sale where the same daily bar does not establish whether the low preceded the fill. The +1R count tracks the first recorded reach while shares were held. The before/on/after split counts those who ultimately exited on a price stop. The tie column identifies cases where the same bar also touched both stop and target. The stop has priority. Time exits have their own exit group. Days to stop count exchange sessions from the purchase, with entry day counted as one.",
        table(
            [
                "Revision",
                "Book",
                "Strategy",
                "MFE R p10/50/90",
                "MAE R p10/50/90",
                "Losers reaching +1R",
                "First +1R before / on / after price-stop session",
                "Same-bar ties",
                "Losses with price stop",
                "Sessions to trigger p10/50/90",
                "Sessions to exit p10/50/90",
            ],
            paths,
        ),
        "### Exit reasons",
        table(
            [
                "Revision",
                "Book",
                "Strategy",
                "Journal exit reason",
                "Closed",
                "Losers",
                "Mean gross R",
                "Mean net R",
            ],
            exits,
        ),
    ]


def period_tables(report):
    sections = [
        "## Where the trades occurred",
        "Year and regime refer to the signal close. Both rule revisions require the gate to pass before opening a funded or equal-risk plan. A blank gate-off expectancy means there were no such entries. Open positions can cross into a gate-off period. The machine-readable report includes excluded shadows and window marks. Every cell below shows closed trades and their mean net R.",
    ]
    for rev in REVISIONS:
        books = report["revisions"][rev]["books"]
        for axis, heading in (
            ("by_year", "signal year"),
            ("by_regime", "signal-day gate"),
            ("by_exit_regime", "exit-day gate"),
        ):
            years = sorted(
                {
                    k
                    for book in BOOKS
                    for strat in STRATEGIES
                    for k in books[book]["strategies"][strat][axis]
                }
            )
            rows = []
            for k in years:
                row = [k]
                for book in BOOKS:
                    for strat in STRATEGIES:
                        s = books[book]["strategies"][strat][axis][k]
                        row.append(f"{s['closed']} / {number(s['mean_net_r'])}")
                rows.append(row)
            sections.extend(
                [
                    f"### {rev} by {heading}",
                    table(
                        [
                            heading.title(),
                            "Funded pullback",
                            "Funded breakout",
                            "Funded momentum",
                            "Equal-risk pullback",
                            "Equal-risk breakout",
                            "Equal-risk momentum",
                        ],
                        rows,
                    ),
                ]
            )
    marked_rows = []
    for rev, book, title, data in groups(report):
        marks = data["marked_account_summary"]["regime_pnl"]
        marked_rows.append(
            [
                rev,
                book,
                title,
                marks["on"]["sessions_invested_at_close"],
                number(marks["on"]["marked_net_pnl_inr"], 0),
                marks["off"]["sessions_invested_at_close"],
                number(marks["off"]["marked_net_pnl_inr"], 0),
            ]
        )
    sections.extend(
        [
            "### While positions were exposed",
            "This split attributes each session’s change in marked wealth to that close’s gate. It includes open holdings at window-end. A position sold at the open can contribute P&L on a day ending in cash. Equal-risk values sum independently sized holdings and exclude positions with unresolved valuations; their rupee totals reflect the larger number of simultaneous trades.",
            table(
                [
                    "Revision",
                    "Book",
                    "Strategy",
                    "Gate on · invested closes",
                    "Gate on · net marked ₹",
                    "Gate off · invested closes",
                    "Gate off · net marked ₹",
                ],
                marked_rows,
            ),
        ]
    )
    return sections


def full_momentum_coverage(market, windows, records, rev, counts):
    """Recover detector coverage after a funded valuation gap without continuing its account."""
    import sqlite3
    from collections import Counter
    from contextlib import closing
    from datetime import date

    from thesistally.baseline_replay import training_session

    full = Counter(counts["detector"])
    eligibility = Counter()
    stop_bounds = Counter()
    coverage = {}
    for (name, path), record in zip(windows, records, strict=True):
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            funded = {
                day
                for (day,) in db.execute("SELECT day FROM scans WHERE run=?", (rev + "-portfolio",))
            }
            independent = dict(
                db.execute("SELECT day, regime FROM scans WHERE run=?", (rev + "-equal-risk",))
            )
            missing = [day for day in record["sessions"] if day not in funded]
            coverage[name] = len(missing)
            for day in missing:
                observed = (
                    json.loads(independent[day])["detector_funnel"]["high_momentum"]
                    if day in independent
                    else None
                )
                if observed is not None and not observed["detected"]:
                    full.update(observed)
                    continue
                _, row = training_session((market, date.fromisoformat(day), rules(revision=rev)))
                if observed is not None and observed != row["detector_funnel"]:
                    raise ValueError(
                        f"Complete detector tape and reconstructed census differ on {day}"
                    )
                full.update(row["detector_funnel"])
                eligibility.update(row["signal_dispositions"])
                stop_bounds.update(row["stop_bounds"])
    counts["full_oos_detector"] = dict(full)
    counts["signal_sessions_after_funded_gap"] = coverage
    counts["signal_dispositions_after_funded_gap"] = dict(eligibility)
    counts["stop_bounds_after_funded_gap"] = dict(stop_bounds)


def funnel_text(report):
    training = report["training_momentum"]
    full = report["momentum"]
    counts = training["totals"]["detector_funnel"]
    r2 = full["r2"]
    full_detector = r2.get("full_oos_detector", r2["detector"])
    stages = []
    for label, key in (
        ("Detected", "detected"),
        ("Passed liquidity", "liquid"),
        ("Invalid geometry", "invalid_geometry"),
        ("Valid signal stop", "valid_stop"),
    ):
        stages.append(
            [
                label,
                counts.get(key, 0),
                full_detector.get(key, 0),
                counts.get(key, 0) + full_detector.get(key, 0),
            ]
        )
    reason_keys = sorted(
        set(full["r1"]["allocation"])
        | set(r2["allocation"])
        | set(training["totals"]["signal_dispositions"])
    )
    reasons = [
        [
            k,
            training["totals"]["signal_dispositions"].get(k, 0),
            full["r1"]["allocation"].get(k, 0),
            r2["allocation"].get(k, 0),
        ]
        for k in reason_keys
    ]
    bounds = sorted(
        set(r2["signal_stop_exclusion_combinations"]) | set(training["totals"]["stop_bounds"])
    )
    breakdown = [
        [
            k,
            training["totals"]["stop_bounds"].get(k, 0),
            r2["signal_stop_exclusion_combinations"].get(k, 0),
        ]
        for k in bounds
    ]
    result = [
        "## 52-week-high momentum funnel",
        f"The initial {training['sessions']} training sessions contributed detector and signal-eligibility counts. Funding starts in the first out-of-sample window. A plan gets one primary allocation or exclusion in each portfolio. An eligible training plan has no portfolio disposition.",
        table(["Stage", "Training only", "OOS signals", "Full-history count"], stages),
        "The full detector census includes dates after an interrupted funded account using the complete independent scan tape. Actual funding and rejection counts below stop with the corresponding account.",
        table(["Primary signal disposition", "Training only", "r1 OOS", "r2 OOS"], reasons),
        "The pattern stop is the lowest low of the qualifying 15–40-session base. Its distance from an eventual breakout can exceed 2.5 ATR even when the percentage distance is admissible. Each excluded plan appears once in the combinations below. This documents the conflict for a possible Stage 3 experiment. The ceiling stayed fixed throughout the baseline.",
        table(
            ["Stop-bound failures on the same signal", "Training only", "OOS signals"], breakdown
        ),
    ]
    stages = []
    for rev in REVISIONS:
        for key, title in BOOKS.items():
            f = full[rev]
            s = f["dispositions"][key]
            why = ", ".join(
                f"{reason} ({n})" for reason, n in f["entry_or_censor_reasons"][key].items()
            )
            stages.append(
                [
                    rev,
                    title,
                    s.get("plans", 0),
                    s.get("bought", 0),
                    s.get("closed", 0),
                    s.get("confirmed", 0),
                    why or "—",
                ]
            )
    result.append(
        table(
            [
                "Revision",
                "Book",
                "Allocated",
                "Bought",
                "Closed",
                "Awaiting open",
                "Missed, invalidated or censored by reason",
            ],
            stages,
        )
    )
    return result


def account_tables(report):
    rows = []
    for w in report["windows"]:
        for rev in REVISIONS:
            a = report["revisions"][rev]["books"]["funded"]["account_by_window"][w["name"]]
            book = w.get("books", {}).get(rev + "-portfolio", {})
            rows.append(
                [
                    w["name"],
                    rev,
                    " – ".join(w["test"]),
                    a["end"] or "—",
                    f"{a['sessions']} / {book.get('expected', a['sessions'])}",
                    number(a["return_pct"], 2),
                    number(a["max_drawdown_pct"], 2),
                    number(a["mean_exposure_pct"], 2),
                    number(a["nifty200_buy_hold_return_pct"], 2),
                ]
            )
    return [
        "## Funded accounts and same-period benchmark",
        "Each window starts independently at ₹10 lakh. Returns and drawdowns use marked end-of-day equity including open holdings. The Nifty 200 price-index return uses the same start and last observed session as that row. A shortened row measures only the period through the date shown. Exposure is the average invested percentage. Equal-risk calls can overlap without a pooled capital ceiling. Their individual returns and marks per ₹10 lakh reference are available in the JSON.",
        table(
            [
                "Window",
                "Revision",
                "Scheduled test period",
                "Measured through",
                "Sessions observed / scheduled",
                "Funded return %",
                "Maximum drawdown %",
                "Mean exposure %",
                "Nifty 200 over observed sessions %",
            ],
            rows,
        ),
    ]


def coverage_table(report):
    lines = []
    for r, book, title, data in groups(report):
        x = data["overall"]
        statuses = x["statuses"]
        lines.append(
            [
                r,
                book,
                title,
                x["plans"],
                x["bought"],
                x["closed"],
                statuses.get("active", 0),
                statuses.get("confirmed", 0),
                statuses.get("missed", 0),
                statuses.get("invalidated", 0),
                statuses.get("unmeasurable", 0),
                statuses.get("awaiting_cash", 0),
            ]
        )
    return table(
        [
            "Revision",
            "Book",
            "Strategy",
            "Plans",
            "Bought",
            "Closed",
            "Holding",
            "Pending",
            "Missed",
            "Invalid",
            "Unknown outcome",
            "Awaiting cash",
        ],
        lines,
    )


def sensitivities(report):
    corp, shadows, qualification, sectors = [], [], [], []
    for rev, book, title, block in groups(report):
        cuts = block["corporate_actions"]
        corp.append(
            [
                rev,
                book,
                title,
                cuts["with"]["closed"],
                number(cuts["with"]["mean_net_r"]),
                cuts["without"]["closed"],
                number(cuts["without"]["mean_net_r"]),
                cuts["unresolved_action"]["statuses"].get("unmeasurable", 0),
            ]
        )
        checks = block["qualification"]["criteria"]
        keys = (
            "completed_replays",
            "closed_oos",
            "counted_window_sample",
            "mean_r",
            "bootstrap_lower_r",
            "profit_factor",
            "positive_window_fraction",
        )
        qualification.append(
            [
                rev,
                book,
                title,
                len(block["qualification"]["counted_windows"]),
                *("pass" if checks[k]["passed"] else "fail" for k in keys),
            ]
        )
        for sector, x in block["by_sector"].items():
            if x["closed"]:
                sectors.append(
                    [
                        rev,
                        book,
                        title,
                        sector,
                        x["plans"],
                        x["closed"],
                        number(x["mean_net_r"]),
                        number(x["net_inr"], 0),
                    ]
                )
    for rev in REVISIONS:
        b = report["revisions"][rev]["books"]
        for key, title in STRATEGIES.items():
            selected = b["funded"]["strategies"][key]["overall"]
            rejected = b["excluded_shadow"]["strategies"][key]["overall"]
            shadows.append(
                [
                    rev,
                    title,
                    selected["plans"],
                    selected["closed"],
                    number(selected["mean_net_r"]),
                    rejected["plans"],
                    rejected["closed"],
                    number(rejected["mean_net_r"]),
                    rejected["statuses"].get("unmeasurable", 0),
                ]
            )
    return [
        "## Qualification and sensitivity",
        table(
            [
                "Revision",
                "Book",
                "Strategy",
                "Counted windows",
                "Replay complete",
                "≥100 mature",
                "≥20 in counted windows",
                ">+0.25R",
                "90% lower >0",
                "PF >1.4",
                "Positive in ≥⅔",
            ],
            qualification,
        ),
        "Portfolio exclusions have their own hypothetical fills. Some failed the strategy’s eligibility rules. Their average cannot be substituted for the equal-risk result, which includes only admissible signals.",
        table(
            [
                "Revision",
                "Strategy",
                "Allocated plans",
                "Funded closed",
                "Funded R",
                "Excluded plans",
                "Excluded closed",
                "Excluded R",
                "Excluded unmeasurable",
            ],
            shadows,
        ),
        "The corporate-action comparison holds every other execution fixed. Affected calls include dividend entitlements and compulsory settlements. Separate counts for compulsory exchanges, dividends and unresolved actions are in the JSON.",
        table(
            [
                "Revision",
                "Book",
                "Strategy",
                "With action · closed",
                "With action · R",
                "Without action · closed",
                "Without action · R",
                "Unmeasurable action",
            ],
            corp,
        ),
        "<details>\n<summary>Sector results for every group with a closed trade</summary>\n\n"
        + table(
            [
                "Revision",
                "Book",
                "Strategy",
                "Signal-day sector",
                "Plans",
                "Closed",
                "Mean net R",
                "Net ₹",
            ],
            sectors,
        )
        + "\n\nThe JSON also retains sectors with signals and zero closed trades.\n\n</details>",
    ]


def controls_by_year(receipt):
    rows = {}
    for year, sessions in sorted(receipt["year_sessions"].items()):
        row = {"sessions": sessions}
        for name in ("surveillance", "opening_band", "earnings_calendar"):
            days = [
                d
                for d in receipt["unknown_controls"].get(name, [])
                if d["day"].startswith(str(year))
            ]
            row[name] = {
                "sessions_with_unknown": len(days),
                "unknown_member_observations": sum(d["members"] for d in days),
            }
        rows[str(year)] = row
    return rows


def window_attrition(windows, market):
    import csv
    import sqlite3
    from collections import Counter
    from contextlib import closing
    from functools import lru_cache

    from thesistally.exchange import read_context

    @lru_cache(maxsize=300)
    def ceilings(day):
        context = read_context(market, day, verify_sources=True)
        with (market / "sessions" / day / "members.csv").open(newline="") as members:
            return {
                row["ISIN Code"]: context[row["Symbol"]]["ceiling"]
                for row in csv.DictReader(members)
            }

    result = {}
    for key, path in windows:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            both = {}
            for rev in REVISIONS:
                run = f"{rev}-portfolio"
                first = dict(
                    db.execute(
                        "SELECT allocation,count(*) FROM calls WHERE run=? GROUP BY allocation",
                        (run,),
                    )
                )
                misses = dict(
                    db.execute(
                        """SELECT e.detail,count(*) FROM events e JOIN calls c ON c.id=e.call_id
                    WHERE c.run=? AND c.allocation='portfolio' AND e.kind='missed' GROUP BY e.detail""",
                        (run,),
                    )
                )
                breakdown = Counter()
                for day, isin in db.execute(
                    """SELECT e.day,c.isin FROM events e JOIN calls c ON c.id=e.call_id
                    WHERE c.run=? AND c.allocation='portfolio' AND e.kind='missed'
                    AND e.detail='Opening liquidity or upper price band' ORDER BY e.day""",
                    (run,),
                ):
                    limits = ceilings(day)
                    if isin not in limits:
                        breakdown["off_index_at_open"] += 1
                    elif limits[isin] is None:
                        breakdown["unknown_upper_band"] += 1
                    else:
                        breakdown["published_band_or_liquidity"] += 1
                if sum(breakdown.values()) != misses.get(
                    "Opening liquidity or upper price band", 0
                ):
                    raise ValueError("Opening-band diagnostic does not reconcile to journal misses")
                both[rev] = {
                    "allocation_reasons": first,
                    "allocated_open_misses": misses,
                    "band_misses": dict(breakdown),
                }
            result[key] = both
    return result


def exit_delays(windows, market):
    import csv
    import sqlite3
    from collections import Counter, defaultdict
    from contextlib import closing
    from functools import lru_cache
    from statistics import median

    import pandas as pd

    from thesistally.exchange import decode_controls, read_context

    @lru_cache(maxsize=3)
    def bounds_and_prices(day):
        folder = market / "sessions" / day
        ctx = read_context(market, day, verify_sources=True)
        with (folder / "members.csv").open(newline="") as file:
            limits = {r["ISIN Code"]: ctx[r["Symbol"]]["floor"] for r in csv.DictReader(file)}
        with (folder / "bhavcopy.csv").open(newline="") as file:
            prices = {
                r["ISIN"]: (
                    r["TckrSymb"].strip(),
                    r["SctySrs"].strip(),
                    float(r["OpnPric"]),
                    int(float(r["TtlTradgVol"])),
                )
                for r in csv.DictReader(file)
                if r["SctySrs"].strip() in ("EQ", "BE", "BZ")
            }
        for source in (market / "context" / day, market / "sources/exchange-controls" / day):
            master = source / "security.csv.gz"
            if master.exists():
                off = pd.DataFrame(
                    [(s, info[0], info[1]) for s, info in prices.items() if s not in limits],
                    columns=["isin", "symbol", "series"],
                )
                if len(off):
                    additional = decode_controls(off, None, master.read_bytes())
                    for r in off.itertuples():
                        limits[r.isin] = additional[r.symbol]["floor"]
                break
        return limits, prices

    events = defaultdict(Counter)
    calls = defaultdict(Counter)
    observed_status = defaultdict(dict)
    for window, path in windows:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            for day, cid, isin, run, strategy, status in db.execute(
                """SELECT e.day,c.id,COALESCE((SELECT json_extract(a.detail,'$.successor_isin')
                FROM events a WHERE a.call_id=c.id AND a.kind='corporate_action' AND a.day<=e.day
                AND json_extract(a.detail,'$.stage')='converted' ORDER BY a.day DESC,a.id DESC LIMIT 1), c.isin),
                c.run,c.strategy,c.status FROM events e JOIN calls c ON c.id=e.call_id
                WHERE e.kind='exit_deferred' AND (c.allocation='portfolio' OR c.run LIKE '%-equal-risk') ORDER BY e.day"""
            ):
                limits, prices = bounds_and_prices(day)
                low = limits.get(isin)
                if low is None:
                    cause = "unknown_lower_band"
                elif isin not in prices:
                    cause = "missing_equity_quote"
                elif prices[isin][2] <= low + 0.005:
                    cause = "at_lower_circuit"
                elif prices[isin][3] == 0:
                    cause = "zero_volume"
                else:
                    raise ValueError(
                        f"Exit deferral contradicts the observed opening for {isin} {day}"
                    )
                key = (run, strategy)
                events[key][cause] += 1
                calls[key][window, cid] += 1
                observed_status[key][window, cid] = status
    result = {}
    for rev in REVISIONS:
        result[rev] = {}
        for book, runbook in (("funded", "portfolio"), ("equal_risk", "equal-risk")):
            result[rev][book] = {}
            for strategy in STRATEGIES:
                key = (f"{rev}-{runbook}", strategy)
                waits = list(calls[key].values())
                result[rev][book][strategy] = {
                    "events": dict(events[key]),
                    "affected_trades": len(waits),
                    "median_deferred_sessions": median(waits) if waits else None,
                    "max_deferred_sessions": max(waits, default=None),
                    "statuses_at_window_end": dict(Counter(observed_status[key].values())),
                }
    return result


def delay_table(report):
    rows = []
    for rev, books in report.get("exit_delays", {}).items():
        for book, strategies in books.items():
            for strategy, row in strategies.items():
                why = row["events"]
                rows.append(
                    [
                        rev,
                        BOOKS[book],
                        STRATEGIES[strategy],
                        row["affected_trades"],
                        why.get("unknown_lower_band", 0),
                        why.get("at_lower_circuit", 0),
                        why.get("zero_volume", 0),
                        why.get("missing_equity_quote", 0),
                        number(row["median_deferred_sessions"], 1),
                        number(row["max_deferred_sessions"], 0),
                    ]
                )
    return table(
        [
            "Revision",
            "Book",
            "Strategy",
            "Trades delayed",
            "Unknown floor days",
            "Lower circuit days",
            "Zero volume days",
            "Missing quote days",
            "Median delay",
            "Maximum delay",
        ],
        rows,
    )


def coverage_sections(report):
    sources = report["data_coverage"]["by_year"]
    rows = []
    for year, r in sorted(sources.items()):
        row = [year, r["sessions"]]
        for field in ("surveillance", "opening_band", "earnings_calendar"):
            info = r[field]
            row.append(
                f"{info['sessions_with_unknown']:,} / {info['unknown_member_observations']:,}"
            )
        rows.append(row)
    counts = []
    for w in report["windows"]:
        both = report["window_signal_dispositions"][w["name"]]
        a = both["r2"]["allocation_reasons"]
        totals = [
            sum(a.values()),
            a.get("stop_out_of_bounds", 0),
            a.get("surveillance_unknown", 0),
            a.get("surveillance", 0),
        ]
        for rev in REVISIONS:
            b = both[rev]
            totals.extend(
                [
                    b["allocation_reasons"].get("portfolio", 0),
                    b["allocated_open_misses"].get("Opening liquidity or upper price band", 0),
                    b.get("band_misses", {}).get("unknown_upper_band", 0),
                ]
            )
        counts.append([w["name"], *totals])
    return [
        "### Exchange controls by calendar year",
        "Each cell gives sessions with at least one unknown member and total unknown member-session observations. A known surveillance exclusion differs from an unavailable classification. A null opening limit prevents a fill.",
        table(
            [
                "Year",
                "Sessions",
                "Surveillance · days / observations",
                "Opening band · days / observations",
                "Results calendar · days / observations",
            ],
            rows,
        ),
        "### Why a test window had few or no funded trades",
        "The first four counts describe primary signal-day dispositions from r2. Stop and surveillance gates precede portfolio allocation. The last columns show allocations and opening liquidity or band misses after allocation under each revision. The unknown-upper-band column is a subset of those misses, matched by dated security ISIN at the open.",
        table(
            [
                "Window",
                "Liquid plans",
                "Stop out of bounds",
                "Surveillance unknown",
                "Surveillance flagged",
                "r2 allocated",
                "r2 band/liquidity misses",
                "r2 with unknown upper band",
                "r1 allocated",
                "r1 band/liquidity misses",
                "r1 with unknown upper band",
            ],
            counts,
        ),
    ]


def render(report, artifact):
    provenance = report["provenance"]
    review = report.get("independent_review")
    source = report["data_coverage"]
    sections = [
        "# ThesisTally Stage 2 baseline",
        f"Fixed r2 and r1 rules on {report['sessions']:,} NSE sessions from {report['start']} through {report['end']}. R2 is primary. Each test uses the preceding three calendar years as training history and starts its own uninvested account. Fixed detectors need no parameter fitting. The last test ends on the last verified session. The final out-of-sample test begins with new paper sessions after a future rule freeze.",
        "## Verdicts",
        verdict_table(report),
        "¹ Qualification uses closed trades whose complete 20-session opportunity was observable within their test window. A window contributes trades to the qualification sample only with at least 20 mature closes. All planned windows remain in the denominator for the two-thirds positive-window requirement. The all-closed tables below also include trades that actually completed inside younger cohorts. The fixed qualification policy requires at least 100 counted trades, mean after-cost R above +0.25, a positive lower 90% confidence bound, cash profit factor above 1.4, and positive mean R in at least two thirds of test windows. The bootstrap resamples whole signal dates. Simultaneous trades move together in a draw.",
        "The equal-risk verdict answers whether eligible setups showed an edge. The funded verdict adds ranking, cash, concentration and drawdown controls. “No edge” requires at least 100 counted mature trades and an entire 90% interval at or below zero. A small sample or an interval compatible with competing conclusions gives “not enough data”. A negative verdict is limited to dates on which the strategy could be measured.",
        *report.get("interpretation", []),
        "## Sample and missing outcomes",
        coverage_table(report),
        f"Both versions used the same market fingerprint. The archive records {source['membership']['traded_isins']} traded ISINs and {source['membership']['interval_count']} constituent intervals. Surveillance or other unobserved controls retain their unknown status. Unsupported settlements or closing valuations remain recorded gaps. Sector splits preserve Unknown. [Dated data coverage](DATA_GAPS.md)",
        "Before the public absolute-limit archive, some ordinary EQ opening ranges could be reconstructed from the previously published static percentage and closing price. Both boundaries had to land exactly on the applicable ₹0.05 tick. Dynamic-band names, ex-dates and unresolved cases remain unknown. Earlier fills therefore cover a selected subset of securities and dates. [NSE price-band definition](https://nsearchives.nseindia.in/web/sites/default/files/2023-02/Basic%20FAQs%20on%20Secondary%20Market.pdf) · [NSE tick circular](https://nsearchives.nseindia.in/content/circulars/CMTR62174.pdf)",
        *coverage_sections(report),
        "### How often an intended sale was delayed",
        "An exit order needs a known lower band. Each deferred session is attributed using the held ISIN and opening evidence. The median and maximum count blocked sessions per affected trade within the window. A position can still be open when observation ends. Sparse historical limits can lengthen holding time and change measured losses.",
        delay_table(report),
        *account_tables(report),
        *loss_tables(report),
        *period_tables(report),
        *funnel_text(report),
        *sensitivities(report),
        "## Reproducibility",
        f"The replay data SHA-256 is `{provenance['snapshot_sha256']}`. The source snapshot commit is `{provenance['code_commit']}`, and the checkout commit is `{provenance['checkout_commit']}`. The JSON includes the exact rule and qualification hashes, daily-coverage counts, every criterion with its threshold and result, window/sector/regime splits, and corporate-action sensitivity.",
        f"[Complete baseline JSON](../data/backtests/{artifact}) · [Validation policy](../src/thesistally/validation/stage-2-v1.json) · [Research log](RESEARCH_LOG.md)",
    ]
    if review:
        sections.extend(["## Independent review", review["summary"]])
    return "\n\n".join(sections) + "\n"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_schedule(replay, receipt):
    dates = [date.fromisoformat(d) for d in receipt["sessions"]]
    expected = walk_windows(dates, 3, 1, None)
    if [(name, list(map(str, days)), train) for name, days, train in expected] != [
        (w["name"], w["sessions"], w["training"]) for w in replay["windows"]
    ]:
        raise ValueError("Replay does not cover every scheduled test window")
    if not receipt["complete"] or replay["snapshot_sha256"] != receipt["snapshot_sha256"]:
        raise ValueError("Market inputs lack a matching completed preflight")

    for revision in REVISIONS:
        cfg = rules(revision=revision)
        if replay["configs"][revision] != {
            "rule_revision": cfg["revision"],
            "config_hash": cfg["config_hash"],
        }:
            raise ValueError(f"Stored {revision} settings differ from the analysis rules")


def publish(replay_file, market_root, interpretation=None, review=None):
    replay_file = replay_file.resolve()
    replay = json.loads(replay_file.read_bytes())
    receipt_path = market_root / "baseline" / "preflight.json"
    certify_inputs(market_root / "baseline" / "market", receipt_path)
    receipt_raw = receipt_path.read_bytes()
    if hashlib.sha256(receipt_raw).hexdigest() != replay["preflight_sha256"]:
        raise ValueError("Replay and preflight receipt disagree")
    receipt = json.loads(receipt_raw)
    validate_schedule(replay, receipt)
    windows = [(w["name"], replay_file.parent / w["journal"]) for w in replay["windows"]]
    journals_before = {w: file_hash(path) for w, path in windows}
    result = analyze(replay_file)
    result.update(
        type="stage2_baseline",
        primary="r2",
        rules="Stage 2 · r2 and r1",
        start=replay["start"],
        end=replay["end"],
        sessions=replay["sessions"],
        complete=replay["complete"],
        final_test="forward_since_freeze",
        training_momentum=replay["training_momentum"],
        windows=[
            {
                **{k: w[k] for k in ("name", "training", "test", "journal")},
                "books": {
                    b: {k: rec[k] for k in ("scanned", "expected", "complete", "valuation_gap")}
                    for b, rec in w["books"].items()
                },
            }
            for w in replay["windows"]
        ],
        provenance={
            k: replay[k]
            for k in (
                "identity",
                "code_hash",
                "code_commit",
                "checkout_commit",
                "preflight_sha256",
                "snapshot_sha256",
                "supplementary_inputs_sha256",
                "configs",
            )
        },
        replay_manifest_sha256=file_hash(replay_file),
        journal_sha256=journals_before,
        report_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        data_coverage={
            k: receipt[k]
            for k in (
                "start",
                "end",
                "calendar",
                "year_sessions",
                "unknown_sector_observations",
                "static_opening_bands",
                "legacy_surveillance",
                "earnings_by_isin",
                "corporate_actions_by_isin",
                "primary_archives",
            )
        },
        momentum={r: momentum_funnel(windows, r, rules(revision=r)) for r in REVISIONS},
        interpretation=interpretation or [],
        independent_review=review,
    )
    for rev in REVISIONS:
        full_momentum_coverage(
            market_root / "baseline" / "market",
            windows,
            replay["windows"],
            rev,
            result["momentum"][rev],
        )
    result["data_coverage"]["by_year"] = controls_by_year(receipt)
    result["exit_delays"] = exit_delays(windows, market_root / "baseline" / "market")
    result["window_signal_dispositions"] = window_attrition(
        windows, market_root / "baseline" / "market"
    )
    result["data_coverage"]["membership"] = {
        k: receipt["membership"][k]
        for k in ("interval_count", "traded_isins", "unverified_traded_member_sessions")
    }
    result["terminal_summary"] = terminal_summary(result)
    name = f"stage2-baseline-{replay['identity'][:20]}.json"
    document = render(result, name)
    if {w: file_hash(path) for w, path in windows} != journals_before:
        raise ValueError("A test-window journal changed during publication")
    dest = market_root / "backtests" / name
    write_json(dest, result)
    (Path(__file__).resolve().parents[1] / "docs" / "BASELINE_REPORT.md").write_text(document)
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--review", type=Path, help="Independent reviewer’s machine-readable disposition"
    )
    parser.add_argument(
        "--interpretation", type=Path, help="JSON array of evidence-based report paragraphs"
    )
    args = parser.parse_args()
    print(
        publish(
            args.replay,
            args.data,
            json.loads(args.interpretation.read_bytes()) if args.interpretation else None,
            json.loads(args.review.read_bytes()) if args.review else None,
        )
    )
