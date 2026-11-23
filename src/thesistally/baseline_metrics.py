"""Read-only Stage 2 trade diagnostics and window-relative account measurements."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import numpy as np

from .baseline_qualification import (
    cash_profit_factor,
    clustered_mean_ci,
    maturity_cutoff,
    qualification,
    validation_config,
)
from .journal import STRATEGIES

BOOKS = ("funded", "equal_risk", "excluded_shadow")
QUANTILES = {"min": 0, "p10": 0.1, "p25": 0.25, "median": 0.5, "p75": 0.75, "p90": 0.9, "max": 1}


def mean(values):
    return sum(values) / len(values) if values else None


def distribution(values):
    levels = (
        np.quantile(values, list(QUANTILES.values())).tolist()
        if values
        else [None] * len(QUANTILES)
    )
    return {"n": len(values), "mean": mean(values), **dict(zip(QUANTILES, levels))}


def stop_exit(reason):
    """Price-stop exits only. A time stop is reported in its own exit group."""
    return reason.startswith(("Closing stop", "Stop-first collision"))


def read_calls(db, run, window, sessions, cfg):
    """Hold original-size risk fixed. Use the last stock exit even if cash settled later."""
    day_index = {day: i for i, day in enumerate(sessions)}
    max_hold = cfg["exits"]["max_days"]
    cutoff = maturity_cutoff(sessions, max_hold) or ""
    slip = cfg["cost"]["slippage_bps"] / 10000
    events = defaultdict(dict)
    for ev in db.execute(
        """SELECT e.call_id,e.day,e.kind,e.detail FROM events e
           JOIN calls c ON c.id=e.call_id WHERE c.run=? AND e.kind IN
           ('exit_due','closed','awaiting_cash','corporate_action','dividend','first_1r',
            'target_exit_excursion','invalidated','unmeasurable','missed') ORDER BY e.id""",
        (run,),
    ):
        events[ev["call_id"]].setdefault(ev["kind"], []).append((ev["day"], ev["detail"]))
    transactions = {
        r["call_id"]: r
        for r in db.execute(
            """SELECT c.id call_id,
                min(CASE WHEN f.side='buy' THEN f.day END) buy_day,
                sum(f.fees) fees,
                sum(CASE f.side WHEN 'buy' THEN f.qty*(f.price-f.price/(1+?))
                    ELSE f.qty*(f.price/(1-?)-f.price) END) slippage,
                sum((CASE f.side WHEN 'buy' THEN -1 ELSE 1 END)*f.qty*f.price-f.fees) cash
               FROM fills f JOIN calls c ON c.id=f.call_id WHERE c.run=? GROUP BY c.id""",
            (slip, slip, run),
        )
    }
    receipts = dict(
        db.execute(
            """SELECT c.id,sum(cr.amount) FROM calls c JOIN cash_receipts cr ON cr.call_id=c.id
           WHERE c.run=? GROUP BY c.id""",
            (run,),
        )
    )
    rows = []
    for c in db.execute(
        """SELECT id,day,strategy,sector,status,allocation,buy,stop,size,net,outcome_r,age,mae,mfe,
                  json_extract(regime,'$.enabled') gate
           FROM calls WHERE run=? ORDER BY day,id""",
        (run,),
    ):
        cid = c["id"]
        ev = events[cid]
        status = c["status"]
        if c["gate"] not in (0, 1):
            raise ValueError(f"{run} call {cid}: missing signal-date regime")
        fills = transactions.get(cid)
        if (c["buy"] is None) != (fills is None):
            raise ValueError(f"{run} call {cid}: purchase and fills disagree")
        reasons = ev.get(status, [])
        reason = (
            reasons[-1][1]
            if reasons and status in {"missed", "invalidated", "unmeasurable"}
            else None
        )
        row = {
            "id": cid,
            "day": c["day"],
            "year": c["day"][:4],
            "window": window,
            "strategy": c["strategy"],
            "sector": c["sector"],
            "gate": "on" if c["gate"] else "off",
            "status": status,
            "allocation": c["allocation"],
            "buy_day": fills["buy_day"] if fills else None,
            "mature": c["day"] <= cutoff,
            "status_reason": reason,
            "compulsory": "corporate_action" in ev,
            "dividend": "dividend" in ev,
            "unresolved_action": bool(
                reason and reason.startswith("Corporate action requires reconciliation")
            ),
        }
        row["corporate_action"] = row["compulsory"] or row["dividend"] or row["unresolved_action"]
        if not fills:
            rows.append(row)
            continue
        if not sessions:
            raise ValueError(f"{run}: fills without scanned sessions")
        # Holdings with a pending cash distribution cease stock exposure at the economic exit.
        exit_events = ev.get("awaiting_cash") or ev.get("closed") or ev.get("unmeasurable")
        exit_day, exit_reason = exit_events[0] if exit_events else (sessions[-1], None)
        row.update(exit_day=exit_day, exit_reason=exit_reason)
        if status == "closed":
            risk = (c["buy"] - c["stop"]) * c["size"]
            net = fills["cash"] + receipts.get(cid, 0)
            if risk <= 0 or not math.isclose(net, c["net"], rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"{run} call {cid}: original risk or net cash does not reconcile")
            if not math.isclose(net / risk, c["outcome_r"], rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"{run} call {cid}: net R does not reconcile")
            if not exit_reason:
                raise ValueError(f"{run} call {cid}: no economic exit reason")
            fee_r, slip_r = fills["fees"] / risk, fills["slippage"] / risk
            reached = ev.get("first_1r", [])
            if len(reached) > 1 or bool(reached) != (c["mfe"] >= 1):
                raise ValueError(f"{run} call {cid}: first +1R event and excursion disagree")
            if reached and not row["buy_day"] <= reached[0][0] <= exit_day:
                raise ValueError(f"{run} call {cid}: +1R event falls outside exposure")
            stop_day = None
            if stop_exit(exit_reason):
                due = [d for d, name in ev.get("exit_due", []) if name == exit_reason]
                if not due:
                    raise ValueError(f"{run} call {cid}: price stop has no trigger event")
                stop_day = due[0]
            bounds = ev.get("target_exit_excursion", [])
            upper = json.loads(bounds[-1][1])["mae_upper_r"] if bounds else c["mae"]
            row.update(
                net_inr=net,
                net_r=c["outcome_r"],
                fee_r=fee_r,
                slippage_r=slip_r,
                cost_r=fee_r + slip_r,
                gross_r=c["outcome_r"] + fee_r + slip_r,
                held_sessions=day_index[exit_day] - day_index[row["buy_day"]] + 1,
                mfe=c["mfe"],
                mae=c["mae"],
                mae_upper=upper,
                first_1r_day=reached[0][0] if reached else None,
                stop_day=stop_day,
                days_to_stop=(day_index[stop_day] - day_index[row["buy_day"]] + 1)
                if stop_day
                else None,
            )
        rows.append(row)
    return rows


def summary(rows):
    closed = [r for r in rows if r["status"] == "closed"]
    return {
        "plans": len(rows),
        "bought": sum(r["buy_day"] is not None for r in rows),
        "closed": len(closed),
        "mature_closed": sum(r["mature"] for r in closed),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "net_inr": sum(r["net_inr"] for r in closed),
        "win_rate": mean([r["net_inr"] > 0 for r in closed]),
        "mean_net_r": mean([r["net_r"] for r in closed]),
        "mean_gross_r": mean([r["gross_r"] for r in closed]),
        "mean_cost_r": mean([r["cost_r"] for r in closed]),
        "mean_fee_r": mean([r["fee_r"] for r in closed]),
        "mean_slippage_r": mean([r["slippage_r"] for r in closed]),
        "profit_factor": cash_profit_factor(closed),
        "mean_holding_sessions": mean([r["held_sessions"] for r in closed]),
    }


def loss_diagnosis(rows, cfg):
    closed = [r for r in rows if r["status"] == "closed"]
    exits = defaultdict(list)
    for r in closed:
        exits[r["exit_reason"]].append(r)
    losers = [r for r in closed if r["net_inr"] < 0]
    with_gain = [r for r in losers if r["first_1r_day"]]
    stopped = [r for r in losers if r["stop_day"]]
    ambiguity = [r for r in closed if r["mae_upper"] > r["mae"] + 1e-12]
    return {
        "mean_net_r_ci90": clustered_mean_ci(closed, cfg),
        "signal_date_clusters": len({r["day"] for r in closed}),
        "exit_reasons": {
            k: {
                "closed": len(v),
                "losers": sum(r["net_inr"] < 0 for r in v),
                "mean_net_r": mean([r["net_r"] for r in v]),
                "mean_gross_r": mean([r["gross_r"] for r in v]),
            }
            for k, v in sorted(exits.items())
        },
        "mfe_r": distribution([r["mfe"] for r in closed]),
        "mae_r": distribution([r["mae"] for r in closed]),
        "mae_upper_bound_r": distribution([r["mae_upper"] for r in closed]),
        "loser_mfe_r": distribution([r["mfe"] for r in losers]),
        "loser_mae_r": distribution([r["mae"] for r in losers]),
        "ambiguous_final_bar_mae": len(ambiguity),
        "losers_reached_1r": {
            "losers": len(losers),
            "reached": len(with_gain),
            "share": len(with_gain) / len(losers) if losers else None,
            "on_exit_session": sum(r["first_1r_day"] == r["exit_day"] for r in with_gain),
            "before_price_stop_session": sum(
                r["first_1r_day"] < r["stop_day"] for r in with_gain if r["stop_day"]
            ),
            "on_price_stop_session": sum(
                r["first_1r_day"] == r["stop_day"] for r in with_gain if r["stop_day"]
            ),
            "after_price_stop_session": sum(
                r["first_1r_day"] > r["stop_day"] for r in with_gain if r["stop_day"]
            ),
            "same_session_as_stop_first_collision": sum(
                r["first_1r_day"] == r["stop_day"]
                and r["exit_reason"].startswith("Stop-first collision")
                for r in with_gain
            ),
        },
        "days_to_stop_losers": {
            "losers": len(losers),
            "losers_with_price_stop": len(stopped),
            "trigger_sessions": distribution([r["days_to_stop"] for r in stopped]),
            "exit_sessions": distribution([r["held_sessions"] for r in stopped]),
            "losers_with_other_exit": len(losers) - len(stopped),
        },
    }


def describe_strategy(rows, accounts, complete_by_window, years, cfg, *, qualify):
    from .baseline_accounts import summarize_windows

    groups = {}
    for field, levels in (
        ("year", years),
        ("gate", ("on", "off")),
        ("window", complete_by_window),
        ("sector", ()),
    ):
        partition = {level: [] for level in levels}
        for row in rows:
            partition.setdefault(row[field], []).append(row)
        groups[field] = {value: summary(group) for value, group in sorted(partition.items())}
    for window in complete_by_window:
        groups["window"][window]["complete"] = complete_by_window[window]
        if accounts:
            groups["window"][window]["account"] = accounts[window]
    exit_groups = {value: [] for value in ("on", "off")}
    for row in rows:
        if row["status"] == "closed":
            exit_groups[row["exit_gate"]].append(row)
    output = {
        "overall": {**summary(rows), **loss_diagnosis(rows, cfg)},
        "by_window": groups["window"],
        "by_year": groups["year"],
        "by_regime": groups["gate"],
        "by_exit_regime": {k: summary(v) for k, v in exit_groups.items()},
        "by_sector": groups["sector"],
        "corporate_actions": {
            "with": summary([r for r in rows if r["corporate_action"]]),
            "without": summary([r for r in rows if not r["corporate_action"]]),
            "with_compulsory_exchange_action": summary([r for r in rows if r["compulsory"]]),
            "with_dividend": summary([r for r in rows if r["dividend"]]),
            "unresolved_action": summary([r for r in rows if r["unresolved_action"]]),
        },
        "allocation_reasons": dict(Counter(r["allocation"] for r in rows)),
        "status_reasons": {
            status: dict(Counter(r["status_reason"] for r in rows if r["status"] == status))
            for status in ("missed", "invalidated", "unmeasurable")
        },
    }
    if accounts:
        output["marked_account_summary"] = summarize_windows(accounts)
    if qualify:
        output["qualification"] = qualification(rows, complete_by_window, cfg)
        output["verdict"] = output["qualification"]["verdict"]
    return output


METHODOLOGY = {
    "diagnostic_cohort": "All journalled calls in the scheduled test windows. Outcomes, distributions, costs, exit statistics and win rates use measured closed trades. Open, pending, unmeasurable and immature counts are shown separately. Results by year/sector/regime use information on the signal date.",
    "qualification": "Only closed trades whose signal had at least the configured full holding horizon of subsequent observed exchange sessions by that window's end. An incomplete replay cannot qualify. A calendar-short last window is evaluated over its scheduled sessions. Only complete windows with at least 20 mature closes contribute trades or positive votes; all planned test windows remain in the breadth denominator.",
    "bootstrap": "Percentile 90% two-sided interval on the trade-weighted mean. Resample distinct signal sessions with replacement, keeping all same-date trades together. Fewer than two distinct dates have no interval. This controls same-date dependence; overlapping positions opened on different dates may still be correlated.",
    "costs": "For each trade, R is original whole shares times slipped buy less original stop. Gross keeps the actual entries, exits, quantity, dividends and compulsory cash fixed, reverses each fill's adverse slippage and adds back actual charged fees. Mean gross R minus mean fee R and slippage R equals mean net R. Holder-specific income taxes are excluded.",
    "exit_and_risk": "Final economic exit reason and date are recorded at the stock disposition if fractional cash arrives later. MAE is signed in original risk units; MFE is positive favourable movement. On an intraday single-share exit the adverse low may follow the sale; lower/upper possible MAE quantiles are shown. A +1R event occurs while exposed by final sale. An opening exit uses only the opening price. Same-session +1R vs a stop-first collision has uncertain intraday ordering.",
    "days_to_stop": "Only net losing trades exited by a price stop. Count exchange sessions from purchase through stop trigger, inclusive (entry day = 1); also show time through actual sale. A time exit has its own exit-reason group.",
    "funded_account": "Window starts at the configured capital. Drawdown uses the peak of daily marked equity including starting cash. Exposure is mean daily marked stock value divided by daily equity. Per-strategy curves attribute that strategy's positions and cash flows to the same starting capital. Nifty 200 buy-and-hold uses the first and last observed session closes.",
    "equal_risk": "Independent positions can overlap without a pooled balance. A descriptive curve sums measured calls' actual cash flows, remaining marked shares and outstanding receivables. It retains live positions and excludes every entered unmeasurable call throughout. P&L, peak-to-trough rupee loss and gross exposure are normalized to the fixed INR 1,000,000 reference. They are not investable account returns, drawdown or margin requirements.",
    "regime": "Entry cohorts are conditioned on the signal-date gate. Exit-date net P&L includes the full life of trades ending under each gate. Marked daily P&L from both new and existing positions is also split by that day's closing gate; this descriptive attribution does not use the gate to trade retroactively.",
    "corporate_actions": "With/without splits include cash dividends, compulsory corporate-action journal events and unresolved corporate actions. The separate compulsory and dividend subsets may overlap.",
    "verdict": "Edge requires every qualification criterion. No edge requires at least 100 mature trades in counted windows, all windows replayed and a 90% upper bound at or below zero. Other cases receive not enough data, including evidence that is positive but inconclusive. Verdicts are conditional on available market and surveillance data.",
}


def analyze(replay_path):
    from .baseline_accounts import funded_marks, independent_marks, summarize_windows
    from .signals import rules

    replay_path = Path(replay_path)
    replay = json.loads(replay_path.read_text())
    cfg = validation_config()
    windows = replay["windows"]
    revisions = ("r2", "r1")
    all_rows = {rev: {b: [] for b in BOOKS} for rev in revisions}
    coverage = {rev: {b: {} for b in BOOKS} for rev in revisions}
    marks = {rev: {b: {} for b in BOOKS} for rev in revisions}
    complete = {rev: {b: {} for b in BOOKS} for rev in revisions}
    years = sorted({str(d)[:4] for w in windows for d in w["sessions"]})
    for w in windows:
        name, scheduled = w["name"], w["sessions"]
        path = (replay_path.parent / w["journal"]).resolve()
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            benchmark = dict(
                db.execute(
                    "SELECT DISTINCT day,benchmark FROM account_marks WHERE book='portfolio'"
                )
            )
            for rev in revisions:
                rule = rules(revision=rev)
                for run, book in (
                    (rev + "-portfolio", "funded"),
                    (rev + "-equal-risk", "equal_risk"),
                ):
                    meta = w["books"][run]
                    expected = replay.get("configs", {}).get(rev)
                    if expected is not None and expected != {
                        "rule_revision": rule["revision"],
                        "config_hash": rule["config_hash"],
                    }:
                        raise ValueError(
                            f"{rev}: manifest differs from versioned rule configuration"
                        )
                    saved = db.execute(
                        "SELECT r.rules, rc.config FROM runs r LEFT JOIN rule_configs rc ON rc.revision=r.rules WHERE r.id=?",
                        (run,),
                    ).fetchone()
                    if saved is None and meta.get("scanned"):
                        raise ValueError(f"{name} {run}: scanned run has no saved configuration")
                    if saved and (
                        saved["rules"] != rule["revision"]
                        or not saved["config"]
                        or json.loads(saved["config"]) != rule
                    ):
                        raise ValueError(
                            f"{name} {run}: saved rules differ from versioned configuration"
                        )
                    if db.execute(
                        "SELECT 1 FROM calls WHERE run=? AND revision!=? LIMIT 1",
                        (run, rule["revision"]),
                    ).fetchone():
                        raise ValueError(
                            f"{name} {run}: journal contains calls from another revision"
                        )

                    scans = list(
                        db.execute(
                            "SELECT day,json_extract(regime,'$.enabled') gate FROM scans WHERE run=? ORDER BY day",
                            (run,),
                        )
                    )
                    gate = {r["day"]: "on" if r["gate"] else "off" for r in scans}
                    days = list(gate)
                    if days != scheduled[: len(days)] or (meta["complete"] and days != scheduled):
                        raise ValueError(
                            f"{name} {run}: replay manifest and journal session prefix disagree"
                        )
                    rows = read_calls(db, run, name, days, rule)
                    for r in rows:
                        if r["status"] == "closed":
                            r["exit_gate"] = gate[r["exit_day"]]
                    if meta.get("scanned", len(days)) != len(days):
                        raise ValueError(
                            f"{name} {run}: reported session count differs from journal"
                        )
                    complete[rev][book][name] = meta["complete"]
                    limit = rule["exits"]["max_days"]
                    coverage[rev][book][name] = {
                        "training": w.get("training"),
                        "test": w.get("test", [scheduled[0], scheduled[-1]]),
                        "scheduled_sessions": len(scheduled),
                        "scanned_sessions": len(days),
                        "evaluated_through": days[-1] if days else None,
                        "maturity_signal_cutoff": maturity_cutoff(days, limit),
                        "complete": meta["complete"],
                        "valuation_gap": meta.get("valuation_gap"),
                        "signal_gate_sessions": {
                            "on": sum(g == "on" for g in gate.values()),
                            "off": sum(g == "off" for g in gate.values()),
                        },
                    }
                    capital = rule["risk"]["capital"]
                    if book == "funded":
                        complete[rev]["excluded_shadow"][name] = meta["complete"]
                        coverage[rev]["excluded_shadow"][name] = coverage[rev][book][name]
                        account = funded_marks(db, run, days, gate, benchmark, capital)
                        for r in rows:
                            key = "funded" if r["allocation"] == "portfolio" else "excluded_shadow"
                            all_rows[rev][key].append(r)
                    else:
                        account = independent_marks(db, run, days, gate, benchmark, capital, rows)
                        all_rows[rev][book].extend(rows)
                    marks[rev][book][name] = account

    result = {
        "methodology": METHODOLOGY,
        "qualification_config": cfg,
        "replay": replay_path.name,
        "provenance": {
            k: replay[k]
            for k in (
                "identity",
                "study",
                "preflight_sha256",
                "snapshot_sha256",
                "supplementary_inputs_sha256",
                "code_hash",
                "code_commit",
                "checkout_commit",
                "configs",
            )
            if k in replay
        },
        "revisions": {},
    }
    for rev in revisions:
        books = {}
        for book in BOOKS:
            observations = all_rows[rev][book]
            accounts = marks[rev][book]
            overall_accounts = {w: by_strategy["portfolio"] for w, by_strategy in accounts.items()}
            strategies = {
                strategy: describe_strategy(
                    [r for r in observations if r["strategy"] == strategy],
                    {w: by_strategy[strategy] for w, by_strategy in accounts.items()},
                    complete[rev][book],
                    years,
                    cfg,
                    qualify=book != "excluded_shadow",
                )
                for strategy in STRATEGIES
            }
            books[book] = {
                "overall": summary(observations),
                "coverage_by_window": coverage[rev][book],
                "account_by_window": overall_accounts,
                "marked_account_summary": summarize_windows(overall_accounts),
                "strategies": strategies,
            }
        result["revisions"][rev] = {
            "rule_revision": rules(revision=rev)["revision"],
            "books": books,
        }
    return result
