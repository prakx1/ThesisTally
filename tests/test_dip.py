from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import DAY, MARKET, REGIME, calendar, prices

from thesistally import dip
from thesistally.journal import advance, journal, process
from thesistally.signals import rules


def listing(**value):
    row = {
        "symbol": "DIP",
        "isin": "I1",
        "sector": "Banks",
        "close": 100.0,
        "score": 0.19,
        "rank": 1,
        "ema100": 93.0,
        "rsi2": 4.0,
        "prior_high10": 106.0,
        "atr14": 2.0,
        "turnover": 80000000.0,
    }
    return pd.DataFrame([{**row, **value}])


def setup(cfg, **kwargs):
    return dip.candidates(
        listing(**kwargs), {"DIP": {**MARKET, "surveillance": False, "earnings_in_days": None}}, cfg
    )


def test_dip_thresholds_rank_and_frozen_levels():
    base, variant = rules(revision="dip-rsi10"), rules(revision="dip-rsi5")
    assert setup(base, rsi2=10.0) == [] and setup(variant, rsi2=5.0) == []
    assert setup(base, rank=41) == [] and setup(base, ema100=100) == []
    assert setup(base, rsi2=5.0, rank=40)[0].target == 106
    assert setup(variant)[0].features["planned_stop"] == 94
    assert base["config_hash"] != variant["config_hash"]


def test_dip_fill_based_stop_and_close_exits_next_open(tmp_path):
    cfg = rules(revision="dip-rsi10")
    with closing(journal(tmp_path / "db")) as db:
        process(db, "B", DAY, REGIME, setup(cfg), calendar(DAY), cfg)
        get = lambda: db.execute("SELECT * FROM calls").fetchone()
        with db:
            # Gap below signal estimate still defines a new 3 ATR stop at the actual fill.
            advance(db, get(), str(DAY + timedelta(days=1)), prices(92, 110, 91, 106), cfg, MARKET)
            c = get()
            assert c["buy"] == pytest.approx(92 * 1.0005)
            assert c["stop"] == pytest.approx(c["buy"] - 6) and c["target"] == 106
            assert c["partial"] == 0 and c["pending_exit"] is None
            advance(db, c, str(DAY + timedelta(days=2)), prices(104, 112, 101, 106.1), cfg, MARKET)
            assert get()["pending_exit"] == "Close above pre-signal 10-session high"
            assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
            advance(db, get(), str(DAY + timedelta(days=3)), prices(103, 125, 91, 110), cfg, MARKET)
        assert get()["status"] == "closed"
        assert db.execute("SELECT price FROM fills WHERE side='sell'").fetchone()[
            0
        ] == pytest.approx(103 * 0.9995)


def test_dip_complete_time_limit_and_no_partial_target(tmp_path):
    cfg = rules(revision="dip-rsi10")
    with closing(journal(tmp_path / "db")) as db:
        process(db, "B", DAY, REGIME, setup(cfg), calendar(DAY), cfg)
        for n in range(1, 16):
            c = db.execute("SELECT * FROM calls").fetchone()
            with db:
                advance(db, c, str(DAY + timedelta(days=n)), prices(100, 120, 99, 102), cfg, MARKET)
            fresh = db.execute("SELECT * FROM calls").fetchone()
            assert fresh["status"] == ("closed" if n == 15 else "active")
            assert fresh["partial"] == 0
        assert (
            db.execute("SELECT detail FROM events WHERE kind='closed'").fetchone()[0]
            == "Scheduled maximum holding close"
        )


def test_stop_takes_priority_on_final_session(tmp_path):
    cfg = rules(revision="dip-rsi10")
    with closing(journal(tmp_path / "db")) as db:
        process(db, "B", DAY, REGIME, setup(cfg), calendar(DAY), cfg)
        with db:
            c = db.execute("SELECT * FROM calls").fetchone()
            advance(db, c, str(DAY + timedelta(days=1)), prices(100, 101, 99, 100), cfg, MARKET)
            db.execute("UPDATE calls SET age=14")
            c = db.execute("SELECT * FROM calls").fetchone()
            advance(db, c, str(DAY + timedelta(days=15)), prices(100, 108, 92, 93), cfg, MARKET)
        assert db.execute("SELECT pending_exit FROM calls").fetchone()[0] == "Closing stop"


def test_bounds_checked_at_fill_only(tmp_path):
    cfg = rules(revision="dip-rsi10")
    with closing(journal(tmp_path / "db")) as db:
        process(db, "B", DAY, REGIME, setup(cfg, atr14=1.0), calendar(DAY), cfg)
        with db:
            c = db.execute("SELECT * FROM calls").fetchone()
            assert c["allocation"] == "portfolio"
            advance(db, c, str(DAY + timedelta(days=1)), prices(100.2, 104, 98, 103), cfg, MARKET)
        assert db.execute("SELECT status FROM calls").fetchone()[0] == "missed"
        assert (
            db.execute("SELECT detail FROM events WHERE kind='missed'").fetchone()[0]
            == "Fill stop distance out of bounds"
        )


def test_paired_replay_reconciles_each_rsi_book_and_prior_close_benchmark(tmp_path, monkeypatch):
    from thesistally import baseline_replay, evaluation, stage3_dip

    cfg = rules(revision="dip-rsi10")
    books = stage3_dip.registered_books()
    root = tmp_path / "market"
    folder = tmp_path / "runs"
    folder.mkdir()
    dates = [DAY + timedelta(days=i) for i in range(18)]
    written = []

    def prepare(args):
        _, day, _ = args
        written.append(day)
        q = prices(
            open_=100.2 if day != dates[3] else 95,
            high=111,
            low=88,
            close=93 if day == dates[2] else 101,
        )
        f = pd.DataFrame([vars(q)], index=[day])
        f.attrs["exchange"] = {**MARKET, "floor": None}
        path = root / "sessions" / str(day) / "bhavcopy.csv"
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(
            f"ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\nI1,EQ,{q.open},{q.high},{q.low},{q.close}\n"
        )
        calls = setup(cfg, rsi2=7) if day == DAY else []
        return (
            {"enabled": day != dates[3]},
            calls,
            {**calendar(day), "DIP": f, "NIFTY 200": f},
            {"I1": "DIP"},
        )

    monkeypatch.setattr(evaluation, "prepared_session", prepare)
    anchor = str(DAY - timedelta(days=1))
    args = root, folder, "walk-1", dates, ["2023-10-01", anchor], books, []
    w = baseline_replay.run_window(*args, candidate_subset=stage3_dip.subset)
    assert written == dates
    assert baseline_replay.run_window(*args, candidate_subset=stage3_dip.subset) == w
    assert written == dates
    assert all(r["complete"] for r in w["books"].values()), w["books"]
    benchmark = {**{str(d): 101.0 for d in dates}, anchor: 100}
    results = stage3_dip.analyze(folder, [w], benchmark, {"walk-1": False})
    for label in ("equal_risk", "funded"):
        main = results["dip-rsi10"][label]
        variant = results["dip-rsi5"][label]
        assert (
            main["overall"]["mature_closed"] == main["overall"]["losers_reached_1r"]["reached"] == 1
        )
        assert main["overall"]["exit_reasons"]["Closing stop"]["closed"] == 1
        assert (
            main["coverage_by_window"]["walk-1"]["events"]["exit_unknown_price_band"]["events"] == 1
        )
        assert main["by_window"]["walk-1"]["account"][
            "nifty200_buy_hold_return_pct"
        ] == pytest.approx(1)
        assert variant["overall"]["plans"] == 0
        assert main["qualification"]["total_planned_windows"] == 1


def test_forward_signal_dispatch_uses_full_gate_and_point_in_time_rank():
    from test_stage3_market import fixture

    from thesistally.signals import scan_frame

    bars, members = fixture()
    fast = bars[bars.symbol == "FAST"].copy()
    final = fast.index[-1]
    bars.loc[final, ["close", "low", "high"]] = [
        fast.iloc[-2].close * 0.975,
        fast.iloc[-2].close * 0.965,
        fast.iloc[-2].close,
    ]
    bars = pd.concat(
        [bars, fast.assign(symbol="NIFTY 50"), fast.assign(symbol="INDIA VIX", close=12.0)],
        ignore_index=True,
    )
    ctx = {s: {**MARKET, "earnings_in_days": None, "surveillance": False} for s in members.symbol}
    cfg = rules(revision="dip-rsi10")
    reg, plans, _ = scan_frame(bars, members, cfg, ctx)
    assert reg["enabled"] and reg["breadth_count"] == len(members)
    assert [c.symbol for c in plans] == ["FAST"]
    assert plans[0].target == fast.iloc[-11:-1].high.max()
    assert (
        scan_frame(bars, members, cfg, {**ctx, "FAST": {**ctx["FAST"], "quarantined": True}})[1]
        == []
    )
