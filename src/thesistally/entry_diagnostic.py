"""Read-only entry diagnostics for the original development folds of a sealed study."""

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .journal import STRATEGIES, saved_rules
from .market import MarketError, staging

BUCKETS = ("< -1%", "-1% to < 0%", "0% to +1%", "> +1%", "unavailable")
OUTCOMES = ("stop", "target", "neither", "censored", "no_entry")
RR_REASON = "Opening gap or slippage reduced reward/risk"


def gap_bucket(gap):
    # Ignore floating-point noise at exact exchange-price boundaries.
    g = gap.round(10)
    return np.select([g < -1, g < 0, g <= 1, g > 1], BUCKETS[:-1], default=BUCKETS[-1])


def development(db, folds):
    plans, sessions = [], []
    has_plan = "planned_target" in {r[1] for r in db.execute("PRAGMA table_info(calls)")}
    target = "coalesce(c.planned_target,c.target)" if has_plan else "c.target"
    for f in folds:
        days = [
            r[0] for r in db.execute("SELECT day FROM scans WHERE run=? ORDER BY day", (f["run"],))
        ]
        if (
            not f["complete"]
            or len(days) != f["scheduled_sessions"]
            or days[0] != f["start"]
            or days[-1] != f["end"]
        ):
            raise MarketError(f"Incomplete development window {f['period']}")
        sessions.extend(days)
        p = pd.read_sql_query(
            f"""SELECT c.id AS call_id,c.day AS signal_date,c.symbol,c.isin,c.strategy,
                c.allocation,c.entry AS signal_close,c.stop,{target} AS target,c.status,c.buy,
                EXISTS(SELECT 1 FROM events e WHERE e.call_id=c.id AND e.kind='missed'
                       AND e.detail=?) AS rr_rejected
               FROM calls c WHERE c.run=? ORDER BY c.day,c.id""",
            db,
            params=(RR_REASON, f["run"]),
        )
        plans.append(p.astype({"buy": "float64"}).assign(window=f["period"]))
    if sessions != sorted(set(sessions)):
        raise MarketError("Development windows must be distinct and chronological.")
    return pd.concat(plans, ignore_index=True), pd.Index(sessions)


def price_tape(root, sessions):
    folder = root / "sources" / "adjusted"
    start, end = (date.fromisoformat(sessions[i]) for i in (0, -1))
    cols = [
        "day",
        "chain",
        "isin",
        "open",
        "close",
        "adjusted_open",
        "adjusted_close",
        "adjusted_low",
        "adjusted_high",
        "price_factor",
        "volume",
        "unexplained_jump",
    ]
    tape = pd.concat(
        [
            pd.read_parquet(
                folder / f"{y}.parquet",
                columns=cols,
                filters=[("day", ">=", start), ("day", "<=", end)],
            )
            for y in range(start.year, end.year + 1)
        ],
        ignore_index=True,
    )
    tape["day"] = tape.day.astype(str)
    benchmark = tape[tape["isin"] == "NIFTY 200"].set_index("day")
    if not benchmark.index.sort_values().equals(sessions):
        raise MarketError("Development scan calendar and Nifty 200 tape disagree.")
    if (benchmark[["open", "close"]] <= 0).any().any():
        raise MarketError("Invalid Nifty 200 price.")
    flags = {
        (x["day"], x["isin"])
        for x in json.loads((folder / "quarantine.json").read_text())
        if str(start) <= x["day"] <= str(end)
        and x.get("isin")
        and x["reason"] == "unresolved_action"
    }
    tape["unresolved"] = pd.MultiIndex.from_frame(tape[["day", "isin"]]).isin(flags)
    return tape, benchmark


def observed_paths(plans, sessions, tape, benchmark, slip, rr, horizon):
    """Always keep the signal row. Only use elapsed sessions inside development."""
    out = plans.copy()
    pos = sessions.get_indexer(out.signal_date)
    if (pos < 0).any():
        raise MarketError("Signal outside its archived scan calendar.")
    signal_keys = pd.MultiIndex.from_frame(out[["signal_date", "isin"]])
    signals = tape.set_index(["day", "isin"], verify_integrity=True).reindex(signal_keys)
    if not np.allclose(signals.close, out.signal_close, rtol=0, atol=0.005):
        raise MarketError("Signal close or ISIN does not reconcile to the archived tape.")
    chain = signals.chain.to_numpy()
    base_factor = signals.price_factor.to_numpy()
    tape = tape.set_index(["day", "chain"], verify_integrity=True)
    # Pad only with a missing date, never borrow the first sealed holdout session.
    dates = np.append(sessions.to_numpy(), None)

    def ahead(n):
        return dates[np.minimum(pos + n, len(sessions))]

    def bars(n):
        return tape.reindex(pd.MultiIndex.from_arrays([ahead(n), chain])).reset_index(drop=True)

    first = bars(1)
    out["entry_session"] = ahead(1)
    out["next_open"] = first.open.where(first.volume > 0)
    out["gap_pct"] = 100 * (out.next_open - out.signal_close) / out.signal_close
    out["gap_bucket"] = gap_bucket(out.gap_pct)
    # Translate original plan levels to the new share unit on known split/bonus dates.
    out["open_stop"] = out.stop / base_factor * first.price_factor
    out["open_target"] = out.target / base_factor * first.price_factor
    slipped = out.next_open * (1 + slip)
    risk = slipped - out.open_stop
    rr_valid = risk.gt(0) & first.unresolved.eq(False) & first.unexplained_jump.eq(False)
    out["opening_rr"] = ((out.open_target - slipped) / risk).where(rr_valid)
    out["price_rr_miss"] = (out.opening_rr < rr).where(out.opening_rr.notna()).astype("boolean")

    # All strategies enter the RR denominator, only pullbacks need a full price path.
    pullback = out.strategy.eq("trend_pullback").to_numpy()
    p = out[pullback].reset_index(drop=True)
    pos, chain, base_factor = pos[pullback], chain[pullback], base_factor[pullback]
    first = first[pullback].reset_index(drop=True)
    stop, target = p.stop / base_factor, p.target / base_factor
    index_open = benchmark.open.reindex(ahead(1)).to_numpy()
    p["entry_issue"] = np.select(
        [p.entry_session.isna(), p.next_open.isna()],
        ["development_end", "no_traded_open"],
        default="",
    )
    live = p.entry_issue.eq("")
    path_issue = p.entry_issue.copy()
    p["first_hit"] = np.where(live, "pending", "no_entry")
    p["hit_session"] = None
    p["barrier_note"] = p.entry_issue
    p["same_bar_tie"] = False
    p["observed_sessions"] = 0
    p["flagged_price_move"] = signals.unexplained_jump.to_numpy()[pullback]
    for n in range(1, horizon + 1):
        day = ahead(n)
        b = first if n == 1 else bars(n)
        p["flagged_price_move"] |= live & b.unexplained_jump.eq(True)
        good = b.volume.gt(0) & b.unresolved.eq(False) & b.unexplained_jump.eq(False)
        interrupted = live & ~good
        pending = p.first_hit.eq("pending")
        p.loc[interrupted & pending, "first_hit"] = "censored"
        issue = np.select(
            [pd.isna(day), b.unresolved.eq(True), b.unexplained_jump.eq(True)],
            ["development_end", "unresolved_action", "quarantined_jump"],
            default="missing_traded_bar",
        )
        p.loc[interrupted & pending, "barrier_note"] = issue[interrupted & pending]
        path_issue.loc[interrupted] = issue[interrupted]
        live &= good
        p["observed_sessions"] += live.astype(int)
        low, high = b.adjusted_low <= stop, b.adjusted_high >= target
        hit = live & pending & (low | high)
        p.loc[hit, "first_hit"] = np.where(low[hit], "stop", "target")
        p.loc[hit, "hit_session"] = day[hit]
        p.loc[hit, "same_bar_tie"] = low[hit] & high[hit]
        if n in (5, 10):
            stock = (100 * (b.adjusted_close / first.adjusted_open - 1)).where(live)
            idx = 100 * (benchmark.close.reindex(day).to_numpy() / index_open - 1)
            p[f"end_{n}"] = day
            p[f"issue_{n}"] = path_issue.copy()
            p[f"stock_{n}_pct"] = stock
            p[f"nifty_{n}_pct"] = idx
            p[f"excess_{n}_pp"] = stock - idx
    p.loc[p.first_hit.eq("pending"), "first_hit"] = "neither"
    return p, out


def gap_summary(p):
    keys = ["gap_bucket", "signal_date"]
    frame = p.copy()
    for k in OUTCOMES:
        frame[k] = frame.first_hit.eq(k).astype(int)
    daily = (
        frame.groupby(keys)
        .agg(
            plans=("call_id", "size"),
            **{k: (k, "sum") for k in OUTCOMES},
            **{f"median_{n}_pp": (f"excess_{n}_pp", "median") for n in (5, 10)},
            **{f"measured_{n}": (f"excess_{n}_pp", "count") for n in (5, 10)},
        )
        .reset_index()
    )
    summary = []
    for bucket in BUCKETS:
        g, d = frame[frame.gap_bucket == bucket], daily[daily.gap_bucket == bucket]
        row = {
            "bucket": bucket,
            "plans": len(g),
            "dates": len(d),
            "ties": int(g.same_bar_tie.sum()),
        }
        for n in (5, 10):
            row[f"measured_{n}"] = int(d[f"measured_{n}"].sum())
            row[f"dates_{n}"] = int(d[f"median_{n}_pp"].count())
            for name, values in (("pooled", g[f"excess_{n}_pp"]), ("cluster", d[f"median_{n}_pp"])):
                row[f"{name}_{n}_pp"] = float(values.median()) if values.notna().any() else None
        for k in OUTCOMES:
            row[k] = int(d[k].sum())
            row[k + "_dates"] = int(d[k].gt(0).sum())
        summary.append(row)
    return summary, daily


def rr_counts(plans):
    result = []
    for strategy in STRATEGIES:
        g = plans[plans.strategy == strategy]
        funded = g[g.allocation == "portfolio"]
        all_checks = g[g.buy.notna() | g.rr_rejected.eq(1)]
        all_misses = int(all_checks.rr_rejected.sum())
        evaluated = funded[funded.buy.notna() | funded.rr_rejected.eq(1)]
        decided = funded[~funded.status.eq("confirmed")]
        prices = g.price_rr_miss.dropna()
        misses = int(evaluated.rr_rejected.sum())
        result.append(
            {
                "strategy": strategy,
                "plans": len(g),
                "funded": len(funded),
                "funded_decided": len(decided),
                "rr_evaluated": len(evaluated),
                "all_checks": len(all_checks),
                "all_misses": all_misses,
                "all_recorded_rate": all_misses / len(all_checks) if len(all_checks) else None,
                "actual_misses": misses,
                "actual_rate": misses / len(evaluated) if len(evaluated) else None,
                "eligible_prices": len(prices),
                "price_misses": int(prices.sum()),
                "price_rate": float(prices.mean()) if len(prices) else None,
            }
        )
    return result


def exclusions(plans, folds):
    rows = []
    for f in folds[:7]:
        reasons = plans.loc[plans.window.eq(f["period"]), "allocation"].value_counts().to_dict()
        rows.append(
            {
                "window": f["period"],
                "start": f["start"],
                "end": f["end"],
                "plans": sum(reasons.values()),
                "reasons": reasons,
            }
        )
    return rows


def holdout_costs(db, fold, slip):
    # All ordinary execution prices are adversely slipped, including partial exits.
    # Corporate payments and dividend entitlements are separate cash receipts.
    ledger = pd.read_sql_query(
        """SELECT c.id AS call_id,c.symbol,c.net AS net_inr,c.outcome_r AS net_r,
            (c.buy-c.stop)*c.size AS risk_inr,SUM(f.fees) AS fees_inr,
            SUM(CASE f.side WHEN 'buy' THEN f.qty*(f.price-f.price/(1+?))
                ELSE f.qty*(f.price/(1-?)-f.price) END) AS slippage_inr,
            SUM((CASE f.side WHEN 'buy' THEN -1 ELSE 1 END)*f.qty*f.price-f.fees)
              + (SELECT coalesce(sum(amount),0) FROM cash_receipts cr WHERE cr.call_id=c.id)
              AS reconstructed_net
           FROM calls c JOIN fills f ON f.call_id=c.id
           WHERE c.run=? AND c.allocation='portfolio' AND c.status='closed' GROUP BY c.id""",
        db,
        params=(slip, slip, fold["run"]),
    )
    if ledger.empty or (ledger.risk_inr <= 0).any():
        raise MarketError("The frozen ledger has no usable closed-call risk denominators.")
    for name in ("fees", "slippage"):
        ledger[name + "_r"] = ledger[name + "_inr"] / ledger.risk_inr
    ledger["cost_inr"] = ledger.fees_inr + ledger.slippage_inr
    ledger["cost_r"] = ledger.cost_inr / ledger.risk_inr
    ledger["gross_inr"] = ledger.reconstructed_net + ledger.cost_inr
    ledger["gross_r"] = ledger.gross_inr / ledger.risk_inr
    frozen = fold["portfolio"]
    if not (
        np.allclose(ledger.reconstructed_net, ledger.net_inr, rtol=0, atol=1e-7)
        and np.allclose(ledger.gross_r - ledger.cost_r, ledger.net_r, rtol=0, atol=1e-9)
        and len(ledger) == frozen["closed"]
        and math.isclose(ledger.net_inr.sum(), frozen["net_inr"], abs_tol=1e-6)
        and math.isclose(ledger.net_r.mean(), frozen["expectancy_r"], abs_tol=1e-10)
    ):
        raise MarketError("Frozen holdout accounting failed to reconcile.")
    result = {"closed": len(ledger)}
    for k in ("gross", "slippage", "fees", "cost", "net"):
        result[k + "_r"] = float(ledger[k + "_r"].mean())
        result[k + "_inr"] = float(ledger[k + "_inr"].sum())
    return result, ledger


def number(x, digits=2):
    return "—" if x is None else f"{x:+,.{digits}f}"


def rate(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def report_text(r):
    costs = r["holdout"]
    lines = [
        "# ThesisTally entry diagnostic",
        (
            f"Development from {r['start']} through {r['end']}, covering {r['windows']} walk-forward windows. "
            f"The journal contains {r['plans']:,} plans, including {r['pullbacks']:,} trend pullbacks."
        ),
        "## Next-open pullbacks",
        (
            "The hypothetical purchase uses the raw next NSE open irrespective of allocation, opening RR "
            "or band availability. Returns measure stock price change minus Nifty 200 price change, "
            "in percentage points, from that open through the fifth or tenth session close. "
            "Both legs exclude dividends, fees and slippage. These hypothetical prices can include "
            "opens where a funded order would have failed an exchange control."
        ),
        (
            "The pooled median weights each plan equally. The date median gives each signal date one "
            "vote after taking its within-bucket median. Counts are plans / distinct signal dates. "
            "One date can contribute to several buckets or outcomes."
        ),
        "| Overnight gap | Plans / dates | 5-session measured | 5 pooled | 5 date median | 10-session measured | 10 pooled | 10 date median |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for a in r["buckets"]:
        lines.append(
            f"| {a['bucket']} | {a['plans']:,} / {a['dates']:,} | "
            f"{a['measured_5']:,} / {a['dates_5']:,} | {number(a['pooled_5_pp'])} | {number(a['cluster_5_pp'])} | "
            f"{a['measured_10']:,} / {a['dates_10']:,} | {number(a['pooled_10_pp'])} | {number(a['cluster_10_pp'])} |"
        )
    lines += [
        "",
        (
            f"First touches use the original stop and signal-close 2R target through "
            f"{r['horizon']} sessions, including entry day. Stop wins every same-bar tie. These "
            "touch labels are separate from the funded strategy’s closing-stop and partial-exit rules."
        ),
        "| Gap | Stop first | Target first | Neither | Censored | No entry | Same-bar ties |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for a in r["buckets"]:
        counts = " | ".join(f"{a[k]:,} / {a[k + '_dates']:,}" for k in OUTCOMES)
        lines.append(f"| {a['bucket']} | {counts} | {a['ties']:,} |")
    lines += [
        "",
        (
            "Adjacent development windows can supply follow-up bars. The final development close "
            "is the hard cutoff. Missing traded bars, quarantined jumps and unresolved corporate actions censor the path "
            "at the first gap. The raw gap uses contemporaneous prices. Holding returns and original "
            "barriers use the archive’s split-adjusted ISIN chain. The per-plan CSV records raw opens, flags and the first interruption at each horizon."
        ),
        "## Original RR filter",
        (
            "The funded rate divides recorded RR rejections by funded plans that reached the RR check "
            "(rejected or bought). The price-only rate applies the original slipped-entry RR formula "
            "to every plan with a traded open, comparable levels and positive opening risk, regardless "
            "of funding or earlier exchange controls. A dash means the denominator is zero."
        ),
        "| Setup | Plans | Funded | Funded decisions | Funded RR misses / checks | Funded rate | Price-only misses / checks | Price-only rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for a in r["rr"]:
        lines.append(
            f"| {a['strategy']} | {a['plans']:,} | {a['funded']} | {a['funded_decided']} | "
            f"{a['actual_misses']} / {a['rr_evaluated']} | {rate(a['actual_rate'])} | "
            f"{a['price_misses']:,} / {a['eligible_prices']:,} | {rate(a['price_rate'])} |"
        )
    lines += [
        "",
        "Across all journaled legs, including unfunded calls that reached the RR check, "
        + ", ".join(
            f"{a['strategy']} recorded {a['all_misses']:,}/{a['all_checks']:,} "
            f"({rate(a['all_recorded_rate'])})"
            for a in r["rr"]
        )
        + ".",
    ]
    lines += [
        "",
        "## First seven funding decisions",
        (
            "Each plan appears once under its first recorded exclusion. Quarantine and stop "
            "distance are checked before surveillance, so these counts cannot measure overlap."
        ),
        "| Window | Detected | Missing surveillance | Stop distance | Quarantined | Other | Funded |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for w in r["exclusions"]:
        a = w["reasons"]
        other = sum(
            n
            for k, n in a.items()
            if k
            not in ("surveillance_unknown", "stop_out_of_bounds", "data_quarantined", "portfolio")
        )
        lines.append(
            f"| {w['window']} ({w['start'][:4]}) | {w['plans']:,} | {a.get('surveillance_unknown', 0):,} | "
            f"{a.get('stop_out_of_bounds', 0):,} | {a.get('data_quarantined', 0)} | {other} | {a.get('portfolio', 0)} |"
        )
    lines += [
        "",
        "## Sealed holdout cost reconciliation",
        (
            f"The same {costs['closed']} closed funded calls and fills are used throughout. Each "
            "call’s R denominator is (slipped buy price − original stop) × original quantity. "
            "The table averages those per-call R values equally and sums rupees. Gross keeps actual "
            "fill dates, quantities and cash receipts, reversing only modeled fill slippage and fees."
        ),
        "| Component | Mean R per trade | Total ₹ |",
        "|---|---:|---:|",
    ]
    for k, label in (
        ("gross", "Gross before costs"),
        ("slippage", "Slippage deducted"),
        ("fees", "Fees and statutory charges deducted"),
        ("cost", "Total costs deducted"),
        ("net", "Net after costs"),
    ):
        sign = -1 if k in ("slippage", "fees", "cost") else 1
        lines.append(
            f"| {label} | {number(sign * costs[k + '_r'], 6)} | {number(sign * costs[k + '_inr'])} |"
        )
    lines += [
        "",
        "## Forward paper comparison",
        (
            "On new observations, the independent shadow skips raw opens strictly above "
            "signal close + 0.5 × signal ATR(14). An accepted fill sets its target to "
            "fill + 2 × (fill − original stop). It uses the original plan quantity, "
            "liquidity controls, costs and exit lifecycle. Paper Trading → V shows both legs. "
            "No historical shadow performance is estimated here."
        ),
        "## Audit files",
        (
            "[Per-pullback outcomes](pullbacks.csv) · [Date clusters](signal-dates.csv) · "
            "[Holdout cost ledger](holdout-costs.csv) · [Full precision summary](summary.json)"
        ),
        (
            f"Source report `{r['source_report']}`, SHA-256 `{r['source_sha256']}`. "
            f"The holdout register reads `{r['seal']['status']}` for {r['seal']['start']} through {r['seal']['end']}."
        ),
        "",
    ]
    # Keep tables contiguous and separate prose and headings in Markdown.
    return "\n".join(line + ("\n" if line and not line.startswith("|") else "") for line in lines)


def study(root, report_file=None):
    root = Path(root).resolve()
    uri = (root / "journal.sqlite").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if report_file is None:
            ref = db.execute(
                "SELECT * FROM holdout_uses WHERE status='used' ORDER BY end DESC LIMIT 1"
            ).fetchone()
            if not ref:
                raise MarketError("No sealed walk-forward report available.")
            report_file = root / "backtests" / f"{ref['revision']}-{ref['experiment']}.json"
        path = Path(report_file)
        raw = path.read_bytes()
        source = json.loads(raw)
        seal = db.execute(
            "SELECT * FROM holdout_uses WHERE revision=? AND status='used'", (source["rules"],)
        ).fetchone()
        folds = [f for f in source["folds"] if f["period"].startswith("walk-")]
        holdouts = [f for f in source["folds"] if f["period"] == "holdout"]
        if (
            not seal
            or not folds
            or len(holdouts) != 1
            or source["parameters"]["mode"] != "walk_forward"
        ):
            raise MarketError("Use the sealed baseline walk-forward report.")
        holdout = holdouts[0]
        prefix = f"bt-{seal['revision']}-{seal['experiment']}-"
        if (
            holdout["run"] != prefix + "holdout"
            or holdout["start"] != seal["start"]
            or holdout["end"] != seal["end"]
            or any(f["end"] >= seal["start"] or f["run"] != prefix + f["period"] for f in folds)
        ):
            raise MarketError("Report folds disagree with the sealed ledger.")
        cfg = saved_rules(db, source["rules"])
        slip = cfg["cost"]["slippage_bps"] / 10000
        rr, horizon = cfg["exits"]["target_r"], cfg["exits"]["max_days"]
        if rr != 2 or horizon < 10:
            raise MarketError(
                "This diagnostic requires a 2R signal target and at least ten holding sessions."
            )
        plans, sessions = development(db, folds)
        tape, benchmark = price_tape(root, sessions)
        pullbacks, opening = observed_paths(plans, sessions, tape, benchmark, slip, rr, horizon)
        buckets, daily = gap_summary(pullbacks)
        costs, ledger = holdout_costs(db, holdout, slip)
    result = {
        "start": sessions[0],
        "end": sessions[-1],
        "windows": len(folds),
        "plans": len(plans),
        "pullbacks": len(pullbacks),
        "horizon": horizon,
        "buckets": buckets,
        "rr": rr_counts(opening),
        "exclusions": exclusions(plans, folds),
        "holdout": costs,
        "seal": dict(seal),
        "source_report": path.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
    }
    folder = root / "diagnostics" / f"entry-v1-{seal['experiment']}"
    with staging(folder) as tmp:
        pullbacks.to_csv(tmp / "pullbacks.csv", index=False)
        daily.to_csv(tmp / "signal-dates.csv", index=False)
        ledger.to_csv(tmp / "holdout-costs.csv", index=False)
        (tmp / "summary.json").write_text(json.dumps(result, allow_nan=False, indent=2) + "\n")
        (tmp / "report.md").write_text(report_text(result))
        for file in tmp.iterdir():
            file.replace(folder / file.name)
    return folder / "report.md"
