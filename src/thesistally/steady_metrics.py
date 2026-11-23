"""Portfolio and benchmark statistics for the preregistered monthly strategy."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import date
from itertools import groupby
from statistics import mean, stdev

from .steady_momentum import CAPITAL, SETTINGS


def read_days(db, run, benchmark):
    prior_nav = float(CAPITAL)
    prior_invested = 0
    rows = []
    for row in db.execute("SELECT * FROM sessions WHERE run=? ORDER BY day", (run,)):
        current = row["equity"]
        if (
            current <= 0
            or prior_nav <= 0
            or min(benchmark[row["day"]], benchmark[row["prior"]]) <= 0
        ):
            raise ValueError(f"Invalid closing valuation for {run} {row['day']}")
        net = current / prior_nav - 1
        rows.append(
            {
                "day": row["day"],
                "prior": row["prior"],
                "gate": "on" if row["gate"] else "off",
                "net": net,
                "benchmark": benchmark[row["day"]] / benchmark[row["prior"]] - 1,
                "gross": net + (row["fees"] + row["slippage"]) / prior_nav,
                "fees": row["fees"],
                "slippage": row["slippage"],
                "exposure": row["invested"] / current,
                "at_risk": prior_invested > 0 or row["invested"] > 0 or row["turnover"] > 0,
                "turnover": 0.5 * row["turnover"] / prior_nav,
            }
        )
        prior_nav = current
        prior_invested = row["invested"]
    return rows


def curve(rows, column, *, calendar_time):
    wealth = peak = 1.0
    peak_day = rows[0]["prior"]
    dd = 0.0
    trough = peak_at_loss = None
    values = []
    for row in rows:
        value = row[column]
        if not math.isfinite(value) or 1 + value <= 0:
            raise ValueError(f"Invalid {column} return on {row['day']}")
        wealth *= 1 + value
        values.append(value)
        if wealth > peak:
            peak, peak_day = wealth, row["day"]
        draw = 1 - wealth / peak
        if draw > dd:
            dd, trough, peak_at_loss = draw, row["day"], peak_day
    years = (
        sum((date.fromisoformat(r["day"]) - date.fromisoformat(r["prior"])).days for r in rows)
        / 365.2425
        if calendar_time
        else len(rows) / 252
    )
    sigma = stdev(values) if len(values) > 1 else None
    return {
        "return_pct": (wealth - 1) * 100,
        "cagr_pct": math.expm1(math.log(wealth) / years) * 100,
        "max_drawdown_pct": dd * 100,
        "drawdown_peak": peak_at_loss,
        "drawdown_trough": trough,
        "annual_volatility_pct": sigma * math.sqrt(252) * 100 if sigma is not None else None,
        "sharpe": mean(values) * math.sqrt(252) / sigma if sigma else None,
    }


def summarize(rows, *, calendar_time=True):
    if not rows:
        return {"sessions": 0}
    net = curve(rows, "net", calendar_time=calendar_time)
    index = curve(rows, "benchmark", calendar_time=calendar_time)
    gross = curve(rows, "gross", calendar_time=calendar_time)
    invested = [r for r in rows if r.get("at_risk", r["exposure"] > 0)]
    invested_results = {
        "sessions": len(invested),
        "net_return_pct": 100 * (math.prod(1 + r["net"] for r in invested) - 1)
        if invested
        else None,
        "tri_return_pct": 100 * (math.prod(1 + r["benchmark"] for r in invested) - 1)
        if invested
        else None,
    }
    losers = [r["net"] for r in rows if r["net"] < 0]
    months = Counter()
    under = 0
    for _, group in groupby(rows, key=lambda r: r["day"][:7]):
        obs = list(group)
        held = math.prod(1 + r["net"] for r in obs)
        market = math.prod(1 + r["benchmark"] for r in obs)
        months["observed"] += 1
        months["negative"] += int(held < 1)
        under += int(held < market)
    return {
        "sessions": len(rows),
        "first": rows[0]["day"],
        "last": rows[-1]["day"],
        "net": net,
        "benchmark": index,
        "excess_return_pp": net["return_pct"] - index["return_pct"],
        "gross_same_fills": gross,
        "fee_inr": sum(r["fees"] for r in rows),
        "slippage_inr": sum(r["slippage"] for r in rows),
        "cost_drag_cagr_pp": gross["cagr_pct"] - net["cagr_pct"],
        "one_way_turnover_pct": 100 * sum(r["turnover"] for r in rows),
        "annualized_one_way_turnover_pct": 25200 * mean(r["turnover"] for r in rows),
        "mean_exposure_pct": 100 * mean(r["exposure"] for r in rows),
        "while_invested": invested_results,
        "loss_diagnosis": {
            "negative_sessions": len(losers),
            "mean_loss_session_pct": 100 * mean(losers) if losers else None,
            "underperforming_sessions": sum(r["net"] < r["benchmark"] for r in rows),
            "observed_months": months["observed"],
            "negative_months": months["negative"],
            "underperforming_months": under,
            "worst_sessions": [
                {"day": r["day"], "net_pct": 100 * r["net"], "benchmark_pct": 100 * r["benchmark"]}
                for r in sorted(rows, key=lambda r: r["net"])[:5]
            ],
        },
    }


def purchase_coverage(db, run):
    filled, flagged = db.execute(
        "SELECT count(*), coalesce(sum(unknown_band),0) FROM fills WHERE run=? AND side='buy'",
        (run,),
    ).fetchone()
    blocked = Counter()
    for row in db.execute(
        "SELECT detail FROM events WHERE run=? AND kind IN ('deferred','missed')", (run,)
    ):
        e = json.loads(row["detail"])
        if e.get("planned_purchase"):
            blocked[e["reason"]] += 1
    data_blocked = sum(blocked[reason] for reason in ("missing_open", "unknown_upper_band"))
    total = filled + sum(blocked.values())
    last = db.execute(
        "SELECT state FROM sessions WHERE run=? ORDER BY day DESC LIMIT 1", (run,)
    ).fetchone()
    state = json.loads(last["state"]) if last else {}
    return {
        "planned": total,
        "filled": filled,
        "filled_band_unknown": flagged,
        "blocked": sum(blocked.values()),
        "blocked_by_reason": dict(sorted(blocked.items())),
        "data_blocked": data_blocked,
        "data_blocked_pct": 100 * data_blocked / total if total else None,
        "coverage_pass": 10 * data_blocked <= total,
        "last_close_pending_buy_instructions": sum(
            bool(order["buy"]) for order in state.get("orders", {}).values()
        ),
    }


def measure(db, variants, schedule, benchmark, gaps):
    answer = {}
    wins, total = SETTINGS["qualifying_window_fraction"]
    drawdown_limit = SETTINGS["maximum_drawdown_multiple"]
    for variant in variants:
        pooled = []
        windows = {}
        beats = 0
        comparable = []
        n_fills = Counter()
        skipped = Counter()
        ca = Counter()
        sales = []
        fees = Counter()
        for case, days, _ in schedule:
            run = f"{variant}-{case}"
            rows = read_days(db, run, benchmark)
            if [r["day"] for r in rows] != [str(d) for d in days[: len(rows)]]:
                raise ValueError(f"Nonconsecutive exposure or marks in {run}")
            valued = len(rows) == len(days)
            if not valued and run not in gaps:
                raise ValueError(f"Missing terminal gap or mark in {run}")
            coverage = purchase_coverage(db, run)
            included = valued and coverage["coverage_pass"]
            pooled.extend(rows)
            if included:
                comparable.extend(rows)
            block = summarize(rows)
            block.update(
                complete=included,
                valuation_complete=valued,
                coverage=coverage,
                scheduled=[str(days[0]), str(days[-1])],
                scheduled_sessions=len(days),
                gap=gaps.get(run),
            )
            windows[case] = block
            beats += int(included and block["excess_return_pp"] > 0)
            for f in db.execute("SELECT * FROM fills WHERE run=?", (run,)):
                n_fills[f["side"]] += 1
                n_fills["unknown_band_sales" if f["side"] == "sell" else "unknown_band_buys"] += f[
                    "unknown_band"
                ]
                fees["fee"] += f["fee"]
                fees["slippage"] += f["slippage"]
                if f["side"] == "sell":
                    sales.append(f["realized_ex_income"])
            for e in db.execute(
                "SELECT kind,detail FROM events WHERE run=? AND kind IN ('corporate_action','quarantine_release','missed','deferred','dividend')",
                (run,),
            ):
                info = json.loads(e["detail"])
                if e["kind"] in ("missed", "deferred"):
                    skipped[e["kind"] + "/" + info["reason"]] += 1
                else:
                    ca[info.get("stage", info.get("kind", e["kind"]))] += 1
        observed = summarize(pooled)
        if pooled and any(
            abs(a - b) > 1e-4
            for a, b in [
                (observed["fee_inr"], fees["fee"]),
                (observed["slippage_inr"], fees["slippage"]),
            ]
        ):
            raise ValueError(f"Fill ledger cannot reconcile portfolio costs for {variant}")
        by_year = defaultdict(list)
        by_gate = defaultdict(list)
        for r in pooled:
            by_year[r["day"][:4]].append(r)
            by_gate[r["gate"]].append(r)
        qualified_period = summarize(comparable)
        denom = sum(w["complete"] for w in windows.values())
        cagr = (
            bool(comparable)
            and qualified_period["net"]["cagr_pct"] > qualified_period["benchmark"]["cagr_pct"]
        )
        dd = (
            bool(comparable)
            and qualified_period["net"]["max_drawdown_pct"]
            <= drawdown_limit * qualified_period["benchmark"]["max_drawdown_pct"]
        )
        breadth = bool(denom) and beats * total >= denom * wins
        unpriced = sum(not w["valuation_complete"] for w in windows.values())
        all_valued = unpriced == 0
        qualified = all_valued and breadth and cagr and dd
        answer[variant] = {
            "overall": observed,
            "eligible_period": qualified_period,
            "invested_dates_by_window": {
                k: [
                    r["day"]
                    for r in pooled
                    if w["scheduled"][0] <= r["day"] <= w["scheduled"][1] and r["at_risk"]
                ]
                for k, w in windows.items()
            },
            "windows": windows,
            "by_year": {y: summarize(v) for y, v in by_year.items()},
            "by_regime": {
                g: summarize(by_gate.get(g, []), calendar_time=False) for g in ("on", "off")
            },
            "fills": dict(n_fills),
            "order_exceptions": dict(skipped),
            "company_events": dict(ca),
            "realized_sales_excluding_income": {
                "sales": len(sales),
                "losing": sum(p < 0 for p in sales),
                "net_realized_inr": sum(sales),
            },
            "qualification": {
                "verdict": "qualifies"
                if qualified
                else "does not qualify"
                if denom and all_valued
                else "inconclusive",
                "complete_windows": denom,
                "valuation_complete_windows": sum(
                    w["valuation_complete"] for w in windows.values()
                ),
                "excluded_windows": [k for k, w in windows.items() if not w["complete"]],
                "planned_windows": len(schedule),
                "winning_windows": beats,
                "winning_windows_required": math.ceil(wins * denom / total) if denom else None,
                "optimistic_breadth": {
                    "wins_if_all_unpriced_windows_win": beats + unpriced,
                    "windows_if_all_unpriced_windows_count": denom + unpriced,
                    "could_pass": (beats + unpriced) * total >= (denom + unpriced) * wins
                    if denom + unpriced
                    else None,
                },
                "breadth_pass": breadth,
                "cagr_pass": cagr,
                "drawdown_pass": dd,
                "drawdown_multiple_limit": drawdown_limit,
                "qualified": qualified,
            },
        }
    return answer
