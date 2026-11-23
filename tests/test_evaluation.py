from contextlib import closing
from copy import deepcopy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from thesistally import evaluation, market
from thesistally.journal import exclusion, journal, process, run_report
from thesistally.signals import Candidate, feature_frames, features, rules, scan_frame
from thesistally.trading_calendar import holding_horizon

CFG = rules()


def inputs(symbol, prices):
    bars = pd.concat([prices.assign(symbol=s) for s in [symbol, "NIFTY 50", "INDIA VIX"]])
    members = pd.DataFrame([{"symbol": symbol, "isin": "IN0000000001", "sector": "IT"}])
    ctx = {symbol: {"surveillance": False, "earnings_in_days": None}}
    return bars, members, ctx


def test_coforge_golden_and_future_append():
    prices = pd.read_csv(Path(__file__).parent / "fixtures" / "coforge-2026-11-20.csv")
    bars, members, ctx = inputs("COFORGE", prices)
    members["isin"] = "INE591G01025"
    day = "2026-11-20"
    r, c, f = scan_frame(bars, members, CFG, ctx, as_of=day)
    assert [(x.strategy, x.entry, x.stop, x.target) for x in c] == [
        ("trend_pullback", 2086.7, 2011.2, pytest.approx(2237.7))
    ]
    assert c[0].isin == "INE591G01025"
    assert c[0].features["atr"] == pytest.approx(68.79332654191587)
    assert c[0].features["volume"] == 9_300_662
    extra = bars[bars.day == day].assign(
        day="2026-11-23", close=1, low=0.1, high=10000, volume=999999999
    )
    full = pd.concat([bars, extra])
    rr, cc, ff = scan_frame(full, members, CFG, ctx, as_of=day)
    assert r == rr and c == cc
    pd.testing.assert_frame_equal(f["COFORGE"], ff["COFORGE"])
    all_features = features(full[full.symbol == "COFORGE"], CFG)
    pd.testing.assert_frame_equal(f["COFORGE"], all_features.loc[:day])


@pytest.mark.parametrize("setup,stop", [("high_momentum", 97.0), ("vcp_breakout", 98.0)])
def test_breakout_golden(setup, stop):
    p = pd.DataFrame(
        {
            "day": pd.bdate_range("2022-01-01", periods=253).date,
            "open": 99.0,
            "high": 100.0,
            "low": 97.0,
            "close": 99.0,
            "volume": 1000000,
        }
    )
    if setup == "vcp_breakout":
        p.loc[242:246, ["low", "volume"]] = [94.0, 1500000]
        p.loc[247:251, ["low", "volume"]] = [98.0, 800000]
    p.loc[252, ["high", "close", "volume"]] = [104.0, 103.0, 2500000]
    bars, m, ctx = inputs("EXAMPLE", p)
    # A later-listed peer contributes to cross-sectional strength and breadth.
    peer = p.iloc[25:].assign(symbol="PEER", close=99.0)
    bars = pd.concat([bars, peer], ignore_index=True)
    m = pd.concat([m, m.assign(symbol="PEER", isin="IN0000000002")], ignore_index=True)
    ctx["PEER"] = ctx["EXAMPLE"].copy()
    day = p.day.iloc[-1]
    regime, candidates, frames = scan_frame(bars, m, CFG, ctx, as_of=day)
    actual = [(c.symbol, c.strategy, c.entry, c.stop, c.target) for c in candidates]
    assert actual == [
        ("EXAMPLE", "trend_pullback", 103.0, 97.0, 115.0),
        ("EXAMPLE", setup, 103.0, stop, 103 + 2 * (103 - stop)),
    ]
    assert all(c.features["rs_percentile"] == 1 for c in candidates)

    # Push the peer above the breakout’s rank on the following session.
    future = bars.loc[bars.day == day].copy()
    future["day"] = (pd.Timestamp(day) + pd.offsets.BDay()).date()
    future.loc[future.symbol == "PEER", ["open", "low", "high", "close", "volume"]] = [
        150.0,
        150.0,
        200.0,
        200.0,
        9_000_000,
    ]
    extended = pd.concat([bars, future], ignore_index=True)
    past_regime, past_candidates, past_frames = scan_frame(extended, m, CFG, ctx, as_of=day)
    assert past_regime == regime and past_candidates == candidates
    full_frames = feature_frames(extended, CFG)
    for symbol, frame in frames.items():
        pd.testing.assert_frame_equal(frame, past_frames[symbol])
        pd.testing.assert_frame_equal(frame, full_frames[symbol].loc[:day])


def test_bounds_blackout_and_announced_holidays(tmp_path):
    ctx = {"atr": 5.0, "earnings_in_days": 6, "holding_blackout_days": 29}

    def call(stop):
        return Candidate("A", "IN1", "IT", "vcp_breakout", 100, stop, 120, ctx)

    cfg = deepcopy(CFG)
    regime = {"enabled": True}
    assert exclusion(call(97), cfg, regime) is None
    assert exclusion(call(97.01), cfg, regime) == "stop_out_of_bounds"
    assert exclusion(call(89.99), cfg, regime) == "stop_out_of_bounds"
    ctx["atr"] = 2
    assert exclusion(call(92), cfg, regime) == "stop_out_of_bounds"
    assert exclusion(call(95), cfg, regime) is None
    cfg["filters"]["earnings_blackout_mode"] = "holding_period"
    assert exclusion(call(95), cfg, regime) == "upcoming_results"
    assert holding_horizon(tmp_path, date(2026, 11, 6), 1) == 2
    assert holding_horizon(tmp_path, date(2026, 11, 20), 2) == 5


@pytest.mark.parametrize(
    "entry,stop,atr14,allocation",
    [
        (55.00, 53.35, 4, "portfolio"),  # exactly 3%
        (55.00, 53.36, 4, "stop_out_of_bounds"),  # less than 3%
        (10.50, 9.45, 1, "portfolio"),  # exactly 10%
        (10.50, 9.44, 1, "stop_out_of_bounds"),  # more than 10%
        (80.80, 78.35, 0.98, "portfolio"),  # exactly 2.5 ATR
        (80.80, 78.34, 0.98, "stop_out_of_bounds"),
    ],
)
def test_stop_bounds_record_funded_or_shadow_at_the_signal_close(
    tmp_path, entry, stop, atr14, allocation
):
    day = date(2024, 1, 1)
    c = Candidate(
        "A",
        "IN1",
        "IT",
        "trend_pullback",
        entry,
        stop,
        entry + 2 * (entry - stop),
        {"atr": atr14, "rs_percentile": 1.0},
    )
    frames = {"NIFTY 50": pd.DataFrame(index=[day])}
    with closing(journal(tmp_path / "journal.sqlite")) as db:
        process(db, "pilot-test", day, {"enabled": True}, [c], frames, CFG)
        call = db.execute("SELECT id, allocation, status FROM calls").fetchone()
        assert (call["allocation"], call["status"]) == (allocation, "confirmed")
        events = db.execute(
            "SELECT kind, detail FROM events WHERE call_id=? ORDER BY id", (call["id"],)
        )
        assert [tuple(e) for e in events] == [
            ("detected", ""),
            ("watching", allocation),
            ("confirmed", "Completed daily candle"),
        ]
        report = run_report(db, "pilot-test")
        assert report["portfolio"]["calls"] == int(allocation == "portfolio")
        assert report["shadow"]["calls"] == int(allocation != "portfolio")
        assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 0


def test_stop_distance_is_bound_to_fourteen_session_atr():
    cfg = deepcopy(CFG)
    cfg["trend"]["atr"] = 10
    features = {"atr": 10.0, "atr14": 1.0}
    candidate = Candidate("A", "IN1", "IT", "trend_pullback", 100, 95, 110, features)
    assert exclusion(candidate, cfg, {"enabled": True}) == "stop_out_of_bounds"


def test_gate_sample_weak_window_and_bootstrap():
    cfg = CFG["validation"]
    rows = [
        {"status": "closed", "outcome_r": v, "net": 100 * v, "age": 2, "mae": -0.2, "mfe": 1.5}
        for v in [1, 1, 1, -1] * 10
    ]
    assert evaluation.criteria([(rows, 0.7), (rows, 0.4), (rows, 0.2)], cfg)["qualified"]
    assert not evaluation.criteria([(rows, 0.4)] * 3, cfg)["qualified"]
    assert not evaluation.criteria([(rows[:10], 0.7), (rows, 0.3), (rows, 0.4)], cfg)["qualified"]
    assert evaluation.bootstrap([0.5] * 20, cfg) == [0.5, 0.5]


def test_holdout_once_and_rolling_train_test(tmp_path):
    days = list(pd.bdate_range("2014-01-01", "2026-11-20").date)
    parts = evaluation.walk_windows(days, 3, 1, 18)
    assert parts[0][1][0].year == 2017
    assert parts[-1][1][0] == date(2025, 5, 21)
    assert parts[-2][1][-1] < parts[-1][1][0]
    assert [d for _, window, _ in parts for d in window] == [d for d in days if d >= parts[0][1][0]]
    db = journal(tmp_path / "test.db")
    evaluation.reserve_holdout(db, "r1", "same", parts[-1][1])
    evaluation.reserve_holdout(db, "r1", "same", parts[-1][1])
    with pytest.raises(market.MarketError, match="already"):
        evaluation.reserve_holdout(db, "r1", "different", parts[-1][1])


def test_replay_preserves_unpriced_position_and_runs_other_windows(tmp_path, monkeypatch):
    days = [date(2024, 8, n) for n in (27, 28, 29, 30)] + [date(2024, 9, 2)]
    parts = [("walk-1", days[:3], None), ("walk-2", days[3:4], None), ("holdout", days[4:], None)]
    monkeypatch.setattr(evaluation, "provenance", lambda *args: {"snapshot_hash": "fixture"})
    monkeypatch.setattr(evaluation, "prepared_session", lambda _: ({}, [], {}, {}))

    def process(db, run, day, *args):
        with db:
            db.execute("INSERT INTO scans VALUES(?,?,?,0)", (run, str(day), '{"enabled":false}'))
            if day == days[1] and run.endswith("walk-1"):
                call = {"id": 1, "symbol": "TATAMTRDVR", "isin": "IN9155A01020"}
                raise evaluation.UnpricedPosition(call, day, "unsettled entitlement")

    monkeypatch.setattr(evaluation, "process", process)
    report = evaluation.evaluate(tmp_path, parts, days, CFG, {}, True, tests_only=True)
    first, second = report["folds"]
    assert first["evaluated_through"] == str(days[0])
    assert first["valuation_gap"]["isin"] == "IN9155A01020"
    assert second["complete"] and report["coverage_status"] == "incomplete"
    assert not report["holdout_used"]
    gate = report["qualification"]["high_momentum"]
    assert not gate["qualified"] and gate["criteria"]["incomplete_windows"]["value"] == ["walk-1"]
    db = journal(tmp_path / "journal.sqlite")
    assert db.execute("SELECT count(*) FROM holdout_uses").fetchone()[0] == 0
    assert (
        db.execute("SELECT count(*) FROM scans WHERE run LIKE '%walk-1-blackout'").fetchone()[0]
        == 3
    )
    db.close()
