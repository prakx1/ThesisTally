import math
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from test_evaluation import inputs
from test_research import DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import corporate, signals
from thesistally.journal import UnpricedPosition, cost, journal, process
from thesistally.market import MarketError


def config():
    cfg = signals.rules()
    cfg["entries"] = {"max_chase_atr": 0.5}
    return cfg


def test_pre_liquidity_detection_counts_actual_detector_hits():
    cfg = config()
    raw = pd.read_csv(Path(__file__).parent / "fixtures" / "coforge-2026-11-20.csv")
    bars, members, context = inputs("LIQUID", raw)
    illiquid = raw.assign(symbol="ILLIQUID", volume=1)
    bars = pd.concat([bars, illiquid], ignore_index=True)
    members = pd.concat([members, members.assign(symbol="ILLIQUID", isin="IN0000000002")])
    context["ILLIQUID"] = context["LIQUID"].copy()
    regime, plans, _ = signals.scan_frame(bars, members, cfg, context, as_of="2026-11-20")
    assert regime["detector_funnel"]["trend_pullback"] == {
        "detected": 2,
        "liquid": 1,
        "invalid_geometry": 0,
        "valid_stop": 1,
    }
    assert [(c.symbol, c.strategy) for c in plans] == [("LIQUID", "trend_pullback")]
    assert sum(item["liquid"] for item in regime["detector_funnel"].values()) == len(plans)
    cfg["filters"]["min_stop_fraction"] = 0.08
    tight_regime, tight_plans, _ = signals.scan_frame(bars, members, cfg, context)
    assert (
        tight_plans == plans
        and tight_regime["detector_funnel"]["trend_pullback"]["valid_stop"] == 0
    )
    del cfg["entries"]
    original_regime, original_plans, _ = signals.scan_frame(bars, members, cfg, context)
    assert "detector_funnel" not in original_regime
    assert original_plans == plans


def test_unconstrained_book_sizes_overlaps_at_fixed_filled_risk_and_charges_each_call(tmp_path):
    db = journal(tmp_path / "ledger.sqlite")
    cfg = config()
    cfg["risk"].update(max_positions=0, max_per_sector=0, max_open_risk=0)
    run = "edge"
    candidates = [pick(strategy=s) for s in ("trend_pullback", "vcp_breakout")]
    for c in candidates:
        c.features["atr"] = 5
    blocked = pick("B")
    blocked.features["surveillance"] = "unknown"
    assert (
        process(
            db, run, DAY, REGIME, [*candidates, blocked], calendar(DAY), cfg, unconstrained=True
        )
        == 2
    )

    def session(i, ohlc):
        d = DAY + timedelta(days=i)
        bar = pd.DataFrame([vars(ohlc)], index=[d])
        bar.attrs["exchange"] = MARKET
        return d, {**calendar(d), "A": bar}

    # Both independent plans fill at a favorable opening gap, then touch both levels.
    d, frames = session(1, prices(open_=98.5, low=89, high=125, close=103))
    process(db, run, d, REGIME, [candidates[0]], frames, cfg, unconstrained=True)
    d, frames = session(2, prices(open_=99.0, low=97.0, high=101, close=99))
    process(db, run, d, {"enabled": False}, candidates, frames, cfg, unconstrained=True)
    process(db, run, d, {"enabled": False}, candidates, frames, cfg, unconstrained=True)
    rows = list(db.execute("SELECT * FROM calls ORDER BY id"))
    assert [c["status"] for c in rows] == ["closed", "closed", "active"]
    assert all(c["allocation"] == "unconstrained" for c in rows)
    for c in rows:
        assert c["size"] == math.floor(5000 / (c["buy"] - c["stop"]))
    sells = list(db.execute("SELECT * FROM fills WHERE side='sell' ORDER BY id"))
    assert len(sells) == 2 and all(f["day"] == str(d) for f in sells)
    for f in sells:
        assert f["fees"] == cost("sell", f["price"] * f["qty"], cfg, charge_dp=True)
    assert not list(db.execute("SELECT * FROM account_marks"))
    assert [
        (r["day"], r["qty"], r["value"])
        for r in db.execute("SELECT * FROM paper_position_marks ORDER BY day,call_id")
    ] == [(str(DAY + timedelta(days=1)), c["size"], c["size"] * 103) for c in rows[:2]] + [
        (str(d), rows[2]["size"], rows[2]["size"] * 99)
    ]
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 3
    with pytest.raises(MarketError, match="separate run identifiers"):
        process(db, run, d, REGIME, [], frames, cfg)


def test_independent_leg_gap_is_censored_without_blocking_siblings(tmp_path, monkeypatch):
    db = journal(tmp_path / "ledger.sqlite")
    cfg = config()
    run = "edge"
    candidates = [pick("A"), pick("B")]
    process(db, run, DAY, REGIME, candidates, calendar(DAY), cfg, unconstrained=True)
    d = DAY + timedelta(days=1)
    frame = pd.DataFrame([vars(prices())], index=[d])
    frame.attrs["exchange"] = MARKET
    process(
        db, run, d, REGIME, [], {**calendar(d), "A": frame, "B": frame}, cfg, unconstrained=True
    )
    d += timedelta(days=1)
    frame.index = [d]
    lost = frame.copy()
    lost.attrs["exchange"] = {**MARKET, "compulsory_action": {}}

    def no_credit(db, call, day, bar, cfg, context, previous):
        db.execute("UPDATE calls SET remaining=0 WHERE id=?", (call["id"],))
        raise UnpricedPosition(call, day, "Unverified successor credit")

    monkeypatch.setattr(corporate, "settle", no_credit)
    process(db, run, d, REGIME, [], {**calendar(d), "A": lost, "B": frame}, cfg, unconstrained=True)
    a, b = db.execute("SELECT * FROM calls ORDER BY symbol")
    assert a["status"] == "unmeasurable" and a["remaining"] == a["size"]
    assert b["status"] == "active" and b["age"] == 2
    assert not list(db.execute("SELECT * FROM fills WHERE side='sell'"))


def test_r1_equal_risk_uses_its_fixed_target_and_sizes_from_the_slipped_fill(tmp_path):
    db = journal(tmp_path / "ledger.sqlite")
    cfg = signals.rules(revision="r1")
    candidates = [pick("A"), pick("B")]
    for c in candidates:
        c.features["atr"] = 5
    process(db, "r1-edge", DAY, REGIME, candidates, calendar(DAY), cfg, unconstrained=True)
    day = DAY + timedelta(days=1)
    frames = calendar(day)
    for symbol, op in (("A", 98), ("B", 100)):
        frame = pd.DataFrame([vars(prices(open_=op, low=97, high=101, close=99))], index=[day])
        frame.attrs["exchange"] = MARKET
        frames[symbol] = frame
    process(db, "r1-edge", day, REGIME, [], frames, cfg, unconstrained=True)
    accepted, missed = db.execute("SELECT * FROM calls ORDER BY symbol")
    assert accepted["status"] == "active"
    assert accepted["target"] == accepted["planned_target"] == 120
    assert accepted["size"] == math.floor(5000 / (98 * 1.0005 - 90)) > accepted["planned_size"]
    assert missed["status"] == "missed" and missed["buy"] is None
    assert (
        db.execute(
            "SELECT detail FROM events WHERE call_id=? AND kind='missed'", (missed["id"],)
        ).fetchone()[0]
        == "Opening gap or slippage reduced reward/risk"
    )


def test_off_index_overlaps_share_earliest_signal_history(tmp_path, monkeypatch):
    anchors = []

    def removed(root, day, cfg, call):
        anchors.append(call["day"])
        frame = pd.DataFrame(index=[day])
        frame.attrs["exchange"] = {}
        return frame

    monkeypatch.setattr(signals, "follow_removed", removed)
    calls = [
        {"isin": "one", "symbol": "RENAMED", "day": "2025-07-02"},
        {"isin": "one", "symbol": "ORIGINAL", "day": "2025-06-01"},
    ]
    frames = {}
    signals.attach_following(tmp_path, DAY, config(), calls, frames, {})
    assert anchors == ["2025-06-01"]
    assert frames["one"] is frames["ORIGINAL"] is frames["RENAMED"]
