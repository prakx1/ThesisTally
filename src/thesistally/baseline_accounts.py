"""Marked funded accounts and independently sized call exposure over each window."""

from collections import Counter, defaultdict

from .journal import STRATEGIES


def benchmark_return(sessions, benchmark):
    if not sessions or sessions[0] not in benchmark or sessions[-1] not in benchmark:
        return None
    return 100 * (benchmark[sessions[-1]] / benchmark[sessions[0]] - 1)


def describe_marks(marks, capital, gate, benchmark, *, independent=False):
    days = [m["day"] for m in marks]
    comparator = benchmark_return(days, benchmark)
    by_regime = {
        g: {"sessions": 0, "sessions_invested_at_close": 0, "marked_net_pnl_inr": 0.0}
        for g in ("on", "off")
    }
    previous = peak = capital
    max_loss = max_dd = exposure = 0.0
    for m in marks:
        equity, invested = m["equity"], m["invested"]
        regime = by_regime[gate[m["day"]]]
        regime["sessions"] += 1
        regime["sessions_invested_at_close"] += int(invested > 0)
        regime["marked_net_pnl_inr"] += equity - previous
        peak = max(peak, equity)
        max_loss = max(max_loss, peak - equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        denominator = capital if independent else equity
        if denominator <= 0 and not independent:
            raise ValueError("Funded account has nonpositive marked equity")
        exposure += invested / denominator
        previous = equity
    net = previous - capital if marks else None
    result = {
        "kind": "independent_reference" if independent else "funded",
        "sessions": len(days),
        "start": days[0] if days else None,
        "end": days[-1] if days else None,
        "nifty200_buy_hold_return_pct": comparator,
        "regime_pnl": by_regime,
    }
    if independent:
        result.update(
            return_pct=None,
            max_drawdown_pct=None,
            mean_exposure_pct=None,
            fixed_reference_inr=capital,
            net_pnl_inr=net,
            pnl_pct_of_reference=100 * net / capital if marks else None,
            max_peak_to_trough_loss_pct_of_reference=100 * max_loss / capital if marks else None,
            mean_gross_exposure_pct_of_reference=100 * exposure / len(days) if days else None,
        )
    else:
        pct = 100 * net / capital if marks else None
        result.update(
            initial_capital_inr=capital,
            ending_equity_inr=previous if marks else None,
            ending_cash_including_receivables_inr=previous - marks[-1]["invested"]
            if marks
            else None,
            net_pnl_inr=net,
            return_pct=pct,
            max_drawdown_pct=100 * max_dd if marks else None,
            mean_exposure_pct=100 * exposure / len(days) if days else None,
            excess_return_pp=pct - comparator
            if pct is not None and comparator is not None
            else None,
        )
    return result


def funded_marks(db, run, sessions, gate, benchmark, capital, *, strategies=STRATEGIES):
    by_strategy = defaultdict(list)
    for row in db.execute(
        "SELECT day,book,equity,invested FROM account_marks WHERE run=? ORDER BY day", (run,)
    ):
        by_strategy[row["book"]].append(row)
    result = {}
    for key in ("portfolio", *strategies):
        marks = by_strategy[key]
        if [m["day"] for m in marks] != sessions:
            raise ValueError(f"{run} {key}: missing or duplicate funded account marks")
        result[key] = describe_marks(marks, capital, gate, benchmark)
    return result


def independent_marks(db, run, sessions, gate, benchmark, capital, rows, *, strategies=STRATEGIES):
    """Exclude censored calls throughout; retain marked end-of-window open positions."""
    where = "c.run=? AND c.buy IS NOT NULL AND c.status!='unmeasurable'"
    indices = {d: i for i, d in enumerate(sessions)}
    observed_marks = {
        cid: (first, last, count)
        for cid, first, last, count in db.execute(
            f"""SELECT c.id,min(m.day),max(m.day),count(*) FROM paper_position_marks m
             JOIN calls c ON c.id=m.call_id WHERE {where} GROUP BY c.id""",
            (run,),
        )
    }
    for r in rows:
        if not r["buy_day"] or r["status"] == "unmeasurable":
            continue
        first = indices[r["buy_day"]]
        after_end = indices[r["exit_day"]] + int(r["status"] == "active")
        expected = (
            (sessions[first], sessions[after_end - 1], after_end - first)
            if first < after_end
            else None
        )
        if observed_marks.get(r["id"]) != expected:
            raise ValueError(
                f"{run} call {r['id']}: independent daily marks do not cover the holding period"
            )
    flows, invested = defaultdict(Counter), defaultdict(Counter)
    for query in (
        f"""SELECT c.strategy,f.day,sum((CASE f.side WHEN 'buy' THEN -1 ELSE 1 END)*f.qty*f.price-f.fees)
              FROM fills f JOIN calls c ON c.id=f.call_id WHERE {where} GROUP BY c.strategy,f.day""",
        f"""SELECT c.strategy,cr.day,sum(cr.amount) FROM cash_receipts cr JOIN calls c ON c.id=cr.call_id
              WHERE {where} GROUP BY c.strategy,cr.day""",
        f"""SELECT c.strategy,q.issued,sum(q.estimate) FROM corporate_receivables q JOIN calls c ON c.id=q.call_id
              WHERE {where} GROUP BY c.strategy,q.issued""",
        f"""SELECT c.strategy,q.paid,-sum(q.estimate) FROM corporate_receivables q JOIN calls c ON c.id=q.call_id
              WHERE {where} AND q.paid IS NOT NULL GROUP BY c.strategy,q.paid""",
    ):
        for strategy, day, flow in db.execute(query, (run,)):
            flows[strategy][day] += flow
            flows["portfolio"][day] += flow
    for strategy, day, value in db.execute(
        f"""SELECT c.strategy,m.day,sum(m.value) FROM paper_position_marks m JOIN calls c ON c.id=m.call_id
               WHERE {where} GROUP BY c.strategy,m.day""",
        (run,),
    ):
        invested[strategy][day] = value
        invested["portfolio"][day] += value
    omitted = dict(
        db.execute(
            """SELECT strategy,count(*) FROM calls WHERE run=? AND buy IS NOT NULL AND status='unmeasurable'
        GROUP BY strategy""",
            (run,),
        )
    )
    omitted["portfolio"] = sum(omitted.values())
    result = {}
    for key in ("portfolio", *strategies):
        marks = []
        cash = capital
        for day in sessions:
            cash += flows[key][day]
            value = invested[key][day]
            marks.append({"day": day, "equity": cash + value, "invested": value})
        result[key] = describe_marks(marks, capital, gate, benchmark, independent=True)
        result[key]["excluded_unmeasurable_entered_calls"] = omitted.get(key, 0)
    return result


def summarize_windows(accounts):
    """Each window restarts. Return a worst single-window drawdown and weighted exposure."""
    if not accounts:
        return {}
    independent = next(iter(accounts.values()))["kind"] == "independent_reference"
    drawdown_key = "max_peak_to_trough_loss_pct_of_reference" if independent else "max_drawdown_pct"
    exposure_key = "mean_gross_exposure_pct_of_reference" if independent else "mean_exposure_pct"
    actual = [a for a in accounts.values() if a["sessions"]]
    samples = sum(a["sessions"] for a in actual)
    all_regimes = {}
    for gate in ("on", "off"):
        summed = Counter()
        for a in actual:
            summed.update(a["regime_pnl"][gate])
        all_regimes[gate] = dict(summed)
    return {
        "account_windows": len(actual),
        "sessions": samples,
        "worst_window_" + drawdown_key: max((a[drawdown_key] for a in actual), default=None),
        exposure_key: sum(a[exposure_key] * a["sessions"] for a in actual) / samples
        if samples
        else None,
        "regime_pnl": all_regimes,
    }
