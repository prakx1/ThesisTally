import numpy as np
import pandas as pd
import pytest

from thesistally.entry_diagnostic import gap_bucket, gap_summary, observed_paths


def test_gap_boundaries_and_date_weighting():
    edges = pd.Series([-1.1, -1, -0.01, 0, 1, 1.01, np.nan])
    assert list(gap_bucket(edges)) == [
        "< -1%",
        "-1% to < 0%",
        "-1% to < 0%",
        "0% to +1%",
        "0% to +1%",
        "> +1%",
        "unavailable",
    ]
    g = pd.DataFrame(
        {
            "call_id": range(4),
            "gap_bucket": ["0% to +1%"] * 4,
            "signal_date": ["A", "A", "A", "B"],
            "same_bar_tie": False,
            "first_hit": ["stop", "stop", "target", "neither"],
            "excess_5_pp": [1, 2, 3, 10],
            "excess_10_pp": [1, 2, 3, np.nan],
        }
    )
    rows, daily = gap_summary(g)
    bucket = next(r for r in rows if r["plans"])
    assert len(daily) == 2
    assert (bucket["pooled_5_pp"], bucket["cluster_5_pp"]) == (2.5, 6)
    assert bucket["dates_10"] == 1 and bucket["stop_dates"] == 1


def test_raw_open_benchmark_clock_adjusted_barriers_and_right_censoring():
    sessions = pd.Index(pd.bdate_range("2024-01-01", periods=12).strftime("%Y-%m-%d"))
    # Split at entry. The original 90/120 barriers become 45/60, both touched on day 2.
    tape = pd.DataFrame(
        {
            "day": sessions,
            "chain": "A",
            "isin": "A",
            "open": 50.0,
            "close": 55.0,
            "adjusted_open": 100.0,
            "adjusted_close": 110.0,
            "adjusted_low": 92.0,
            "adjusted_high": 118.0,
            "price_factor": 0.5,
            "volume": 10,
            "unexplained_jump": False,
            "unresolved": False,
        }
    )
    tape.loc[0, ["open", "close", "price_factor"]] = [100, 100, 1]
    tape.loc[2, ["adjusted_low", "adjusted_high"]] = [89, 121]
    idx = pd.DataFrame({"open": 200.0, "close": 204.0}, index=sessions)
    p = pd.DataFrame(
        {
            "call_id": [1, 2],
            "signal_date": [sessions[0], sessions[-1]],
            "isin": "A",
            "symbol": "A",
            "strategy": "trend_pullback",
            "signal_close": [100, 55],
            "stop": [90, 50],
            "target": [120, 65],
        }
    )
    paths, plans = observed_paths(p, sessions, tape, idx, 0.0005, 2, 20)
    a, b = paths.iloc[0], paths.iloc[1]
    assert a.gap_pct == -50 and a.open_stop == 45
    assert a.hit_session == sessions[2] and a.first_hit == "stop" and a.same_bar_tie
    assert a.excess_5_pp == pytest.approx(8) and a.excess_10_pp == pytest.approx(8)
    assert bool(plans.loc[0, "price_rr_miss"])
    assert b.first_hit == "no_entry" and b.issue_10 == "development_end"
    assert pd.isna(b.excess_5_pp) and pd.isna(b.next_open)
    tape.loc[1, "unexplained_jump"] = True
    flagged, _ = observed_paths(p, sessions, tape, idx, 0.0005, 2, 20)
    assert (
        flagged.loc[0, "first_hit"] == "censored"
        and flagged.loc[0, "issue_5"] == "quarantined_jump"
    )
    assert flagged.loc[0, "gap_pct"] == -50


def test_cost_bridge_keeps_per_trade_risk_and_cash_receipts(tmp_path):
    from thesistally.entry_diagnostic import holdout_costs
    from thesistally.journal import journal

    db = journal(tmp_path / "journal.sqlite")
    # Two equal-quantity trades with different initial risks, and one cash receipt.
    with db:
        db.execute("INSERT INTO runs VALUES('sealed','fixture',1000,1000)")
        for i, (stop, sell, cash) in enumerate([(90, 121, 2), (80, 92, 0)], 1):
            buy = 100 * 1.001
            net = sell * 0.999 - buy - 1.5 + cash
            db.execute(
                """INSERT INTO calls(id,run,day,symbol,isin,sector,strategy,revision,
                regime,features,entry,stop,target,size,allocation,status,buy,net,outcome_r)
                VALUES(?,'sealed','d','S',?,'s','trend_pullback','fixture','{}','{}',100,?,130,
                1,'portfolio','closed',?,?,?)""",
                (i, str(i), stop, buy, net, net / (buy - stop)),
            )
            db.executemany(
                "INSERT INTO fills(call_id,day,side,qty,price,fees) VALUES(?,'d',?,1,?,?)",
                [(i, "buy", buy, 0.5), (i, "sell", sell * 0.999, 1)],
            )
            if cash:
                db.execute("INSERT INTO entitlements VALUES(?,'d',?)", (i, cash))
    count, mean, total = db.execute("SELECT count(*),avg(outcome_r),sum(net) FROM calls").fetchone()
    costs, _ = holdout_costs(
        db,
        {"run": "sealed", "portfolio": {"closed": count, "expectancy_r": mean, "net_inr": total}},
        0.001,
    )
    db.close()
    assert costs["gross_inr"] == pytest.approx(15)
    assert costs["gross_r"] == pytest.approx((23 / 10.1 - 8 / 20.1) / 2)
    assert costs["fees_inr"] == pytest.approx(3)
    assert costs["slippage_inr"] == pytest.approx(0.413)
    assert costs["net_r"] == pytest.approx(costs["gross_r"] - costs["cost_r"])
