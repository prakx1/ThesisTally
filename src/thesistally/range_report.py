"""Compile Range Bouncers results from the immutable C run and its setup census."""

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import pandas as pd

from . import baseline_metrics, dip_coverage
from .baseline_qualification import validation_config
from .pilot_data import write_json
from .stage3_dip import covered_qualification

TARGETS = {"C50": "Midpoint", "C80": "Top fifth"}
BOOKS = {"funded": "Funded", "range_equal": "Range, equal risk", "other_equal": "Other, equal risk"}
TARGET_REASON = "Range target close"
TIME_CLOSE = "Scheduled maximum holding close"


def sha_file(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def bounce_counts(rows):
    """Credit the first close trigger, independently of the subsequent sell opening."""
    counts = Counter()
    for row in rows:
        if row["buy_day"] is None:
            continue
        if not row["mature"]:
            counts["horizon_censored"] += 1
            continue
        due = row["first_exit_due"]
        if due and due.startswith(TARGET_REASON):
            counts["target"] += 1
        elif (
            due
            and due.startswith(("Closing stop", "Maximum holding period"))
            or row["exit_reason"] == TIME_CLOSE
        ):
            counts["stop_or_time"] += 1
        elif due:
            raise ValueError(f"Unrecognized Range Bouncers exit trigger {due!r}")
        elif row["status"] in {"unmeasurable", "active", "awaiting_cash"}:
            counts["path_censored"] += 1
        elif row["exit_reason"] in {
            "Compulsory delisting settlement",
            "Fractional-only compulsory settlement",
        }:
            counts["early_compulsory_exit"] += 1
        else:
            raise ValueError(
                f"Completed position lacks a recognizable trigger: {row['exit_reason']}"
            )
    return {
        k: counts[k]
        for k in (
            "target",
            "stop_or_time",
            "horizon_censored",
            "path_censored",
            "early_compulsory_exit",
        )
    }


def compare(range_counts, other_counts):
    def side(counts):
        observed = counts["target"] + counts["stop_or_time"]
        return {
            **counts,
            "observed": observed,
            "rate": counts["target"] / observed if observed else None,
        }

    left, right = side(range_counts), side(other_counts)
    difference = None
    if left["rate"] is not None and right["rate"] is not None:
        difference = 100 * (left["rate"] - right["rate"])
    return {"range": left, "other": right, "difference_pp": difference}


def pooled(pairs):
    fields = tuple(bounce_counts([]))
    return compare(
        *(
            {field: sum(p[side][field] for p in pairs) for field in fields}
            for side in ("range", "other")
        )
    )


def selection_counts(signals, daily, days):
    start, end = days[0], days[-1]
    observations = daily.loc[daily.day.between(start, end)]
    if list(observations.day) != days:
        raise ValueError("Daily member coverage does not match scheduled sessions")
    chosen = signals.loc[signals.day.between(start, end)]
    counts = chosen.cohort.value_counts().to_dict()
    if set(counts) - {"range", "other", "unknown", "ineligible"}:
        raise ValueError("Unexpected monthly classification")
    if len(chosen) != int(observations.raw_setups.sum()):
        raise ValueError("Daily signal counts and saved raw signals disagree")
    return {
        "member_sessions": int(observations.members.sum()),
        "daily_unobservable": int(observations.daily_unobservable.sum()),
        "raw_setups": len(chosen),
        "by_cohort": {
            k: int(counts.get(k, 0)) for k in ("range", "other", "unknown", "ineligible")
        },
        "unclassified_reasons": {
            str(k): int(v)
            for k, v in chosen.loc[chosen.cohort == "unknown", "reason"]
            .value_counts(dropna=False)
            .items()
        },
        "ineligible_reasons": {
            str(k): int(v)
            for k, v in chosen.loc[chosen.cohort == "ineligible", "reason"]
            .value_counts(dropna=False)
            .items()
        },
    }


def journal_book(db, name, window, cohort_signals, cfg):
    meta = window["books"][name]
    days = [r[0] for r in db.execute("SELECT day FROM scans WHERE run=? ORDER BY day", (name,))]
    if days != window["sessions"][: meta["scanned"]] or (
        meta["complete"] and len(days) != len(window["sessions"])
    ):
        raise ValueError(f"{window['name']} {name} journal and schedule differ")
    archive = db.execute(
        "SELECT v.config FROM runs r JOIN rule_configs v ON v.revision=r.rules WHERE r.id=?",
        (name,),
    ).fetchone()
    if days and (archive is None or json.loads(archive[0]) != cfg):
        raise ValueError(f"{name} archived rules disagree with study settings")
    expected = set(
        cohort_signals.loc[cohort_signals.day.isin(days), ["day", "isin"]].itertuples(
            index=False, name=None
        )
    )
    actual = {
        (r[0], r[1])
        for r in db.execute("SELECT day,isin FROM dip_setup_census WHERE run=?", (name,))
    }
    if actual != expected:
        raise ValueError(
            f"{window['name']} {name} setup membership differs by "
            f"{len(expected - actual)} missing and {len(actual - expected)} extra"
        )
    selected = "portfolio" if name.endswith("-funded") else "unconstrained"
    sample = dip_coverage.entry_coverage(db, name, window["sessions"][-1], selected)
    rows = [
        r
        for r in baseline_metrics.read_calls(db, name, window["name"], days, cfg)
        if r["allocation"] == selected
    ]
    triggers = {}
    for cid, detail in db.execute(
        "SELECT e.call_id,e.detail FROM events e JOIN calls c ON c.id=e.call_id "
        "WHERE c.run=? AND e.kind='exit_due' ORDER BY e.id",
        (name,),
    ):
        triggers.setdefault(cid, detail)
    for row in rows:
        row["first_exit_due"] = triggers.get(row["id"])
    if sample["filled"] != sum(r["buy_day"] is not None for r in rows):
        raise ValueError(f"{name} actual and reported entries differ")
    return rows, {
        "complete": meta["complete"],
        "scanned": len(days),
        "expected": len(window["sessions"]),
        "last_observed_day": days[-1] if days else None,
        "valuation_gap": meta.get("valuation_gap"),
        "audit_discrepancies": meta["audit"]["discrepancies"],
        "entry_coverage": sample,
    }


def mature(rows):
    return [r for r in rows if r["mature"] and r["status"] == "closed"]


def book_results(rows, coverage, window_names, stats):
    groups = defaultdict(list)
    for r in rows:
        groups[r["window"]].append(r)
    finished = mature(rows)
    return {
        "qualification": covered_qualification(rows, coverage, stats),
        "coverage_by_window": coverage,
        "all_observed": baseline_metrics.summary(rows),
        "mature_closes": baseline_metrics.summary(finished),
        "mature_by_window": {k: baseline_metrics.summary(mature(groups[k])) for k in window_names},
        "mature_by_gate": {
            k: baseline_metrics.summary([r for r in finished if r["gate"] == k])
            for k in ("on", "off")
        },
        "mature_loss_diagnosis": baseline_metrics.loss_diagnosis(finished, stats),
    }


def verify_selection(directory, proof):
    manifest = directory / "manifest.json"
    if sha_file(manifest) != proof["signal_manifest_sha256"]:
        raise ValueError("The selection manifest differs from the replay receipt")
    receipt = json.loads(manifest.read_bytes())
    for name in ("signals.parquet", "coverage.parquet", "monthly.parquet"):
        if sha_file(directory / name) != receipt["products"][name]["sha256"]:
            raise ValueError(f"Selection product differs from the replay receipt: {name}")
    if (
        proof["signals_sha256"] != receipt["products"]["signals.parquet"]["sha256"]
        or proof["market"]["snapshot_sha256"] != receipt["snapshot_sha256"]
    ):
        raise ValueError("Replay source and selection source disagree")
    return receipt


def compile_report(replay_path, selection_dir, benchmark_dir):
    from .range_replay import registered_books

    replay = json.loads(replay_path.read_bytes())
    signal_receipt = verify_selection(selection_dir, replay["proof"])
    windows = replay["windows"]
    if len(windows) != 10 or len({w["name"] for w in windows}) != 10:
        raise ValueError("The registered test requires ten distinct scheduled windows")
    configs = dict(registered_books())
    expected_books = {f"{t}-{b}" for t in TARGETS for b in BOOKS}
    if set(configs) != expected_books:
        raise ValueError("Unexpected target or book registration")
    signal_path, daily_path = (selection_dir / n for n in ("signals.parquet", "coverage.parquet"))
    signals = pd.read_parquet(signal_path, columns=["day", "isin", "cohort", "reason"])
    if signals.duplicated(["day", "isin"]).any():
        raise ValueError("Repeated raw setup within day and ISIN")
    daily = pd.read_parquet(daily_path).sort_values("day")
    all_rows = defaultdict(list)
    coverage = defaultdict(dict)
    selections, persistence, marked, journals = {}, defaultdict(dict), defaultdict(dict), {}
    by_cohort = {k: signals.loc[signals.cohort == k] for k in ("range", "other")}
    benchmark_path = benchmark_dir / "benchmark.csv"
    benchmark_receipt = json.loads((benchmark_dir / "benchmark.json").read_bytes())
    if sha_file(benchmark_path) != benchmark_receipt["sha256"]:
        raise ValueError("TRI observation hash differs from its receipt")
    with benchmark_path.open() as stream:
        benchmark = {r["day"]: float(r["close"]) for r in csv.DictReader(stream)}
    for window in windows:
        w = window["name"]
        selections[w] = selection_counts(signals, daily, window["sessions"])
        path = (replay_path.parent / window["journal"]).resolve(strict=True)
        journals[w] = sha_file(path)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            for target in TARGETS:
                counts = {}
                for book in BOOKS:
                    name = f"{target}-{book}"
                    cohort = "other" if book == "other_equal" else "range"
                    rows, sample = journal_book(db, name, window, by_cohort[cohort], configs[name])
                    coverage[name][w] = sample
                    if book != "other_equal":
                        all_rows[name].extend(rows)
                    if book != "funded":
                        counts[cohort] = bounce_counts(rows)
                    else:
                        marks = db.execute(
                            "SELECT day,equity,invested FROM account_marks "
                            "WHERE run=? AND book='portfolio' ORDER BY day",
                            (name,),
                        )
                        marked[target][w] = account_summary(
                            list(marks),
                            configs[name]["risk"]["capital"],
                            benchmark,
                            window["training"][-1],
                            sample,
                        )
                pair = compare(counts["range"], counts["other"])
                pair["both_coverage_eligible"] = all(
                    coverage[f"{target}-{b}"][w]["complete"]
                    and not coverage[f"{target}-{b}"][w]["entry_coverage"]["coverage_excluded"]
                    for b in ("range_equal", "other_equal")
                )
                persistence[target][w] = pair
    stats = validation_config()
    window_names = [w["name"] for w in windows]
    return {
        "registration_commit": "8a67849",
        "experiment": "C",
        "variants": TARGETS,
        "replay_identity": replay["identity"],
        "execution_commit": replay["code_commit"],
        "provenance": {
            "replay_sha256": sha_file(replay_path),
            "journal_sha256": journals,
            "selection_manifest": signal_receipt,
            "signals_sha256": sha_file(signal_path),
            "coverage_sha256": sha_file(daily_path),
            "benchmark": {k: benchmark_receipt[k] for k in ("sha256", "series")},
            "proof": replay.get("proof"),
            "rule_config_sha256": {name: cfg["config_hash"] for name, cfg in configs.items()},
        },
        "validation_config": stats,
        "windows": {w["name"]: w["test"] for w in windows},
        "selection": selections,
        "books": {
            name: book_results(all_rows[name], coverage[name], window_names, stats)
            for name in configs
            if not name.endswith("-other_equal")
        },
        "funded_account_marks": marked,
        "persistence": {
            t: {
                "by_window": persistence[t],
                "pooled_all_observed": pooled(persistence[t].values()),
                "pooled_both_coverage_eligible": pooled(
                    [p for p in persistence[t].values() if p["both_coverage_eligible"]]
                ),
                "control_coverage": coverage[f"{t}-other_equal"],
            }
            for t in TARGETS
        },
    }


def account_summary(marks, capital, benchmark, anchor, sample):
    if len(marks) != sample["scanned"]:
        raise ValueError("Missing or duplicate funded marks")
    if not marks:
        return {k: None for k in ("net_return_pct", "tri_pct", "max_drawdown_pct", "exposure_pct")}
    peak, drawdown = capital, 0
    fractions = []
    for m in marks:
        peak = max(peak, m["equity"])
        drawdown = max(drawdown, 1 - m["equity"] / peak)
        fractions.append(m["invested"] / m["equity"])
    return {
        "net_return_pct": 100 * (marks[-1]["equity"] / capital - 1),
        "tri_pct": 100 * (benchmark[marks[-1]["day"]] / benchmark[anchor] - 1),
        "max_drawdown_pct": 100 * drawdown,
        "exposure_pct": 100 * sum(fractions) / len(fractions),
    }


def number(value, places=3):
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return f"{value:,.{places}f}"


def signed(value, places=3):
    return "—" if value is None else f"{value:+.{places}f}"


def fraction(group):
    rate = group["rate"]
    return f"{group['target']:,}/{group['observed']:,}" + (
        f" ({100 * rate:.1f}%)" if rate is not None else ""
    )


def table(headers, rows):
    return "\n".join(
        "| " + " | ".join(map(str, row)) + " |" for row in (headers, ["---"] * len(headers), *rows)
    )


def markdown(report, companion):
    windows = report["windows"]
    sections = [
        "# Range Bouncers",
        "One preregistered experiment tested a monthly range-stock screen followed by a daily oversold entry. The primary sold after a close at the old midpoint. Its only sensitivity waited for the boundary of the old top fifth. Each test year opened a fresh ₹10 lakh simulated account. The last year ends on "
        + list(windows.values())[-1][1]
        + ". [Registered definition](PREREGISTRATION.md#c--range-bouncers)",
        "## Did the strategy qualify?",
    ]
    for target, title in TARGETS.items():
        funded = report["books"][f"{target}-funded"]["qualification"]
        independent = report["books"][f"{target}-range_equal"]["qualification"]
        if not funded["counted_windows"] and not independent["counted_windows"]:
            sections.append(
                f"**{title}.** Neither book qualified. The funded book recorded "
                f"{funded['observed_mature_closes_including_exclusions']} mature closed trades and "
                f"the equal-risk book recorded {independent['observed_mature_closes_including_exclusions']}. "
                "No coverage-eligible window supplied the required 20 trades."
            )
        else:
            parts = []
            for q, label in ((funded, "funded"), (independent, "equal-risk")):
                verdict = "qualified" if q["qualified"] else "did not qualify"
                parts.append(
                    f"The {label} book {verdict} with {q['mature_closed_in_counted_windows']} "
                    f"counted trades from {len(q['counted_windows'])} windows."
                )
            sections.append(f"**{title}.** {' '.join(parts)}")
    summary_rows = []
    for name, data in report["books"].items():
        target, book = name.split("-")
        q = data["qualification"]
        c = q["criteria"]
        obs = data["mature_closes"]
        breadth = f"{len(q['positive_counted_windows'])}/{q['breadth_denominator']}"
        ci = q["mean_net_r_ci90"]
        summary_rows.append(
            (
                TARGETS[target],
                BOOKS[book],
                obs["closed"],
                signed(obs["mean_net_r"]),
                c["closed_oos"]["value"],
                signed(c["mean_r"]["value"]),
                f"{signed(ci[0])}, {signed(ci[1])}",
                number(c["profit_factor"]["value"], 2),
                breadth,
                "Yes" if q["qualified"] else "No",
            )
        )
    sections += [
        table(
            (
                "Target",
                "Book",
                "All mature trades",
                "All mean net R",
                "Counted trades",
                "Counted mean net R",
                "90% date-cluster CI",
                "Cash profit factor",
                "Positive counted / coverage-eligible windows",
                "Qualified",
            ),
            summary_rows,
        ),
        "R is the risk between the actual entry fill and the original stop. Net R includes slippage and trading costs. Independent calls each risk up to ₹5,000 and can overlap. Funded trades also face cash, position, sector and drawdown limits. A window contributes to the counted mean with at least 20 mature closes, a completed replay and at most 10% of planned entries blocked by data. Every coverage-eligible window is in the breadth denominator. Qualification also requires 100 counted trades, mean net R above +0.25, a positive 90% lower confidence bound, cash profit factor above 1.4, and positive mean R in at least two thirds of eligible windows.",
    ]
    coverage_rows = []
    for name, data in report["books"].items():
        q = data["qualification"]
        t, b = name.split("-")
        excluded = [windows[w][0][:4] for w in q["coverage_excluded_windows"]]
        counted = [windows[w][0][:4] for w in q["counted_windows"]]
        observed = q["criteria"]["completed_replays"]
        coverage_rows.append(
            (
                TARGETS[t],
                BOOKS[b],
                f"{observed['value']}/{observed['threshold']}",
                ", ".join(sorted(excluded)) or "None",
                ", ".join(sorted(counted)) or "None",
            )
        )
    for name, data in report["books"].items():
        t, b = name.split("-")
        for w, c in data["coverage_by_window"].items():
            if gap := c["valuation_gap"]:
                sections.append(
                    f"{TARGETS[t]}, {BOOKS[b].lower()}, {windows[w][0][:4]}: the ledger ends on "
                    f"{c['last_observed_day']}. Its next session ({gap['day']}) could not value "
                    f"{gap['symbol']} ({gap['reason']}). The return and index comparison use that earlier endpoint."
                )
    sections += [
        table(
            (
                "Target",
                "Book",
                "Finished replays",
                "Years excluded by the data gate",
                "Years with 20+ mature closes that counted",
            ),
            coverage_rows,
        ),
        "Only windows meeting the trade floor can earn a positive-window vote. An eligible year with fewer than 20 mature trades earns zero votes even when its trades made money. A dash means the qualifying sample was too small to estimate the statistic.",
        "## Do past range stocks bounce again?",
    ]
    p = report["persistence"]
    pooled_rows = []
    for target, title in TARGETS.items():
        pairs = p[target]["by_window"]
        wins = sum(
            d["difference_pp"] is not None and d["difference_pp"] > 0 for d in pairs.values()
        )
        comparable = sum(d["difference_pp"] is not None for d in pairs.values())
        overall = p[target]["pooled_all_observed"]
        eligible = p[target]["pooled_both_coverage_eligible"]
        n = sum(d["both_coverage_eligible"] for d in pairs.values())
        for label, value, total in (
            ("All observed", overall, len(pairs)),
            ("Both data gates pass", eligible, n),
        ):
            pooled_rows.append(
                (
                    title,
                    label,
                    total,
                    fraction(value["range"]),
                    fraction(value["other"]),
                    signed(value["difference_pp"], 1),
                )
            )
        sections.append(
            f"**{title}.** Range stocks had the higher target-hit rate in "
            f"{wins} of {comparable} windows with observations in both groups."
        )
    sections.append(
        table(
            (
                "Target",
                "Pooled sample",
                "Windows",
                "Range hits / fills",
                "Other hits / fills",
                "Gap (percentage points)",
            ),
            pooled_rows,
        )
    )
    rows = []
    for w, (lo, hi) in windows.items():
        for t, title in TARGETS.items():
            d = p[t]["by_window"][w]
            rows.append(
                (
                    lo[:4],
                    title,
                    fraction(d["range"]),
                    fraction(d["other"]),
                    signed(d["difference_pp"], 1),
                    "Yes" if d["both_coverage_eligible"] else "No",
                )
            )
    sections += [
        table(
            (
                "Year",
                "Target",
                "Range hits / observable fills",
                "Other hits / observable fills",
                "Gap (pp)",
                "Both data gates pass",
            ),
            rows,
        ),
        "A hit is the first qualifying held close before the stop or time exit. The entry, tradability and eligibility checks are identical for both groups. The comparison includes overlapping independent calls and weights each observed trade equally. Only filled calls with a full 20-session observation horizon and a resolved price path enter the denominator. Unknown classifications, short follow-up and early compulsory exits are kept separately. The same stock can signal on adjacent days, and each group can trade on different dates. Overlapping trades and small range-stock samples limit how confidently these observed differences can be attributed to past bounces.",
        "## What happened to trades that timed out?",
    ]
    for target, title in TARGETS.items():
        data = report["books"][f"{target}-range_equal"]
        closes = data["mature_closes"]["closed"]
        reasons = data["mature_loss_diagnosis"]["exit_reasons"]
        timed = [
            r
            for k, r in reasons.items()
            if k == TIME_CLOSE or k.startswith("Maximum holding period")
        ]
        n = sum(r["closed"] for r in timed)
        if n:
            net = sum(r["closed"] * r["mean_net_r"] for r in timed) / n
            sections.append(
                f"**{title}.** The 20-session time limit ended {n} of {closes} mature "
                f"equal-risk trades. They averaged {signed(net)}R after costs."
            )
    sections.append("## Trade outcomes by year")
    rows = []
    for w, (lo, hi) in windows.items():
        for t, title in TARGETS.items():
            funded = report["books"][f"{t}-funded"]["mature_by_window"][w]
            equal = report["books"][f"{t}-range_equal"]["mature_by_window"][w]
            account = report["funded_account_marks"][t][w]
            rows.append(
                (
                    lo[:4],
                    title,
                    funded["closed"],
                    signed(funded["mean_gross_r"]),
                    signed(funded["mean_net_r"]),
                    equal["closed"],
                    signed(equal["mean_gross_r"]),
                    signed(equal["mean_net_r"]),
                    signed(account["net_return_pct"], 2),
                    signed(account["tri_pct"], 2),
                )
            )
    sections += [
        table(
            (
                "Year",
                "Target",
                "Funded trades",
                "Gross R",
                "Net R",
                "Equal-risk trades",
                "Gross R",
                "Net R",
                "Funded return %",
                "Nifty 200 TRI %",
            ),
            rows,
        ),
        "Trade columns use all observed mature closes by signal year, including coverage-excluded windows. The funded return marks capital through the last verified session and includes open holdings. The gross total-return index uses the same observation dates and the prior session’s closing anchor. Exact dates, exposure, drawdown, censored positions, and exit diagnostics appear in the JSON.",
        "## Sample coverage",
    ]
    rows = []
    for w, (lo, hi) in windows.items():
        s = report["selection"][w]
        c = s["by_cohort"]
        rows.append(
            (
                lo[:4],
                s["member_sessions"],
                s["daily_unobservable"],
                s["raw_setups"],
                c["range"],
                c["other"],
                c["unknown"],
                c["ineligible"],
            )
        )
    sections += [
        table(
            (
                "Year",
                "Member-days",
                "Daily signal unobservable",
                "Raw setups",
                "Range",
                "Other",
                "Monthly class unknown",
                "Ineligible",
            ),
            rows,
        ),
        "An unobservable member-day has insufficient daily inputs to establish whether the entry signal fired. A monthly class unknown is an observed entry signal whose range-stock classification cannot be reconstructed. The control group requires a resolved classification and entry signal. Some historical NSE dividend records give only an ex-date, so the timestamp of a later correction can remain uncertain. [Historical classification and dividend gaps](DATA_GAPS.md#range-bouncers-historical-classification)",
    ]
    rows = []
    for t, title in TARGETS.items():
        for w, (lo, hi) in windows.items():
            s = p[t]["by_window"][w]
            row = [lo[:4], title]
            for side in ("range", "other"):
                coverage = (
                    report["books"][f"{t}-range_equal"]["coverage_by_window"]
                    if side == "range"
                    else p[t]["control_coverage"]
                )[w]["entry_coverage"]
                obs = s[side]
                pre = sum(coverage["pre_order_exclusions"].values())
                cens = obs["horizon_censored"] + obs["path_censored"] + obs["early_compulsory_exit"]
                row.append(
                    " / ".join(
                        map(
                            str,
                            (
                                pre,
                                coverage["blocked_data"] + coverage["blocked_other"],
                                coverage["censored"],
                                cens,
                            ),
                        )
                    )
                )
            rows.append(row)
    sections += [
        table(
            (
                "Year",
                "Target",
                "Range exclusions (pre / no fill / pending / filled censored)",
                "Other exclusions (pre / no fill / pending / filled censored)",
            ),
            rows,
        ),
        "“Pre” counts rule or allocation exclusions. “No fill” counts data blocks and other opening failures. “Pending” counts orders with no observed opening decision. “Filled censored” counts fills without sufficient follow-up or a classifiable target outcome. The JSON gives each blocked reason and marks each book’s coverage decision.",
        f"[Machine-readable counts, criteria, ledger fingerprints and exclusions]({companion})",
    ]
    return "\n\n".join(sections) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path, help="Completed identity-specific replay.json")
    parser.add_argument("--selection-dir", type=Path, default=Path("data/range-bouncers"))
    parser.add_argument("--benchmark-dir", type=Path, default=Path("data/stage3"))
    parser.add_argument("--out", type=Path, default=Path("docs"))
    args = parser.parse_args()
    report = compile_report(args.replay, args.selection_dir, args.benchmark_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    companion = "range-bouncers-results.json"
    write_json(args.out / companion, report)
    (args.out / "RANGE_BOUNCERS_REPORT.md").write_text(markdown(report, companion))


if __name__ == "__main__":
    main()
