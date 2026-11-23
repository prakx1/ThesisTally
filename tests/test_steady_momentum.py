import pandas as pd
import pytest

from thesistally.signals import rules
from thesistally.steady_momentum import Account, advance, last_state, open_journal, plan, seed


def ranks(*names):
    return pd.DataFrame([(s, i + 1) for i, s in enumerate(names)], columns=["isin", "rank"])


def quote(s, opening, close=None, floor=None, ceiling=1000):
    return {
        "symbol": s,
        "open": opening,
        "volume": 100,
        "close": opening if close is None else close,
        "floor": floor,
        "ceiling": ceiling,
    }


def test_monthly_signal_fills_next_open_and_charges_real_fees(tmp_path):
    db = open_journal(tmp_path / "a.db")
    cfg = rules(revision="r2")
    seed(db, "A", "2024-01-31", ranks("A", "B"), True, {})
    advance(
        db,
        "A",
        "2024-02-01",
        "2024-01-31",
        None,
        True,
        {"A": quote("A", 100, 75), "B": quote("B", 5, 600, ceiling=None)},
        cfg,
        {},
    )
    d, a = last_state(db, "A")
    assert a.positions["A"].qty == 666
    assert a.positions["A"].mark == 75
    assert d == "2024-02-01" and "B" not in a.positions
    assert db.execute("SELECT price FROM fills").fetchone()[0] == pytest.approx(100.05)
    advance(db, "A", d, "2024-01-31", None, True, {}, cfg, {})
    assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    advance(db, "A", "2024-02-29", d, ranks("B"), False, {"A": quote("A", 90)}, cfg, {})
    advance(db, "A", "2024-03-01", "2024-02-29", None, True, {"A": quote("A", 91)}, cfg, {})
    assert db.execute("SELECT qty,unknown_band FROM fills WHERE side='sell'").fetchone()[:] == (
        666,
        1,
    )
    assert not last_state(db, "A")[1].positions


def test_lower_circuit_exit_preserves_slot_and_gate_off_blocks_topups(tmp_path):
    db = open_journal(tmp_path / "a.db")
    cfg = rules(revision="r2")
    names = [f"S{i:02}" for i in range(16)]
    seed(db, "A", "2024-01-31", ranks(*names), True, {})
    quotes = {s: quote(s, 100) for s in names}
    advance(db, "A", "2024-02-01", "2024-01-31", None, True, quotes, cfg, {})
    advance(db, "A", "2024-02-29", "2024-02-01", ranks(*names[1:]), True, quotes, cfg, {})
    quotes[names[0]] = quote(names[0], 90, floor=90)
    advance(db, "A", "2024-03-01", "2024-02-29", None, True, quotes, cfg, {})
    acc = last_state(db, "A")[1]
    assert len(acc.positions) == 15 and names[-1] not in acc.positions
    quotes[names[0]] = quote(names[0], 85)
    advance(db, "A", "2024-03-04", "2024-03-01", None, True, quotes, cfg, {})
    assert len(last_state(db, "A")[1].positions) == 14
    books = Account()
    plan(db, "unit", "2024-03-28", books, ranks(*names), False)
    assert not books.orders


def test_released_rights_and_registered_conversion_preserve_cash_and_quantity(tmp_path):
    db = open_journal(tmp_path / "a.db")
    cfg = rules(revision="r2")
    seed(db, "A", "2024-01-31", ranks("OLD"), True, {})
    advance(db, "A", "2024-02-01", "2024-01-31", None, True, {"OLD": quote("OLD", 100)}, cfg, {})
    q = quote("OLD", 95)
    q["rights_issue"] = {"factor": 0.95, "source_url": "https://example.org/rights"}
    cash = last_state(db, "A")[1].cash
    advance(db, "A", "2024-02-02", "2024-02-01", None, True, {"OLD": q}, cfg, {})
    assert last_state(db, "A")[1].cash == cash
    terms = {
        "OLD": {
            "predecessor_isin": "OLD",
            "successor_isin": "NEW",
            "suspended": "2024-02-05",
            "effective": "2024-02-05",
            "credited": "2024-02-06",
            "tradable": "2024-02-07",
            "new_shares": 7,
            "old_shares": 10,
            "source_url": "https://example.org/official",
        }
    }
    # Ex-date predecessor value is frozen. The final successor close pays fractional entitlements.
    advance(db, "A", "2024-02-05", "2024-02-02", None, True, {"NEW": quote("NEW", 133)}, cfg, terms)
    advance(db, "A", "2024-02-06", "2024-02-05", None, True, {"NEW": quote("NEW", 111)}, cfg, terms)
    advance(db, "A", "2024-02-07", "2024-02-06", None, True, {"NEW": quote("NEW", 123)}, cfg, terms)
    account = last_state(db, "A")[1]
    assert set(account.positions) == {"NEW"} and account.positions["NEW"].qty == 466
    assert account.cash - cash == pytest.approx(0.2 * 133)


def test_monthly_return_metrics_reconcile_index_cost_and_fixed_percentages():
    from thesistally.steady_metrics import summarize

    rows = [
        {
            "day": "2024-02-01",
            "prior": "2024-01-31",
            "gate": "on",
            "net": 0.1,
            "benchmark": 0.2,
            "gross": 0.101,
            "fees": 500,
            "slippage": 500,
            "exposure": 0.5,
            "turnover": 0.25,
        },
        {
            "day": "2024-02-02",
            "prior": "2024-02-01",
            "gate": "off",
            "net": -0.1,
            "benchmark": -0.25,
            "gross": -0.099,
            "fees": 550,
            "slippage": 550,
            "exposure": 0.4,
            "turnover": 0.2,
        },
    ]
    a = summarize(rows)
    assert a["net"]["return_pct"] == pytest.approx(-1)
    assert a["net"]["max_drawdown_pct"] == pytest.approx(10)
    assert a["benchmark"]["return_pct"] == pytest.approx(-10)
    assert a["benchmark"]["max_drawdown_pct"] == pytest.approx(25)
    assert a["one_way_turnover_pct"] == pytest.approx(45)
    assert a["slippage_inr"] == 1050
    assert a["loss_diagnosis"]["negative_months"] == 1


def test_signal_close_cannot_influence_previous_monthly_open(tmp_path):
    cfg = rules(revision="r2")

    def trace(folder, close, r):
        db = open_journal(tmp_path / folder / "account.sqlite")
        seed(db, "A", "2024-01-31", ranks("A", "B"), True, {})
        quotes = {s: quote(s, 100, close) for s in "AB"}
        advance(db, "A", "2024-02-01", "2024-01-31", ranks(*r), False, quotes, cfg, {})
        return [
            tuple(v)
            for v in db.execute("select isin,side,qty,raw_open,price,fee from fills order by id")
        ]

    assert trace("old", 25, "AB") == trace("new", 300, "BA")


def test_sale_quantity_and_turnover_use_actual_sale_fill(tmp_path):
    from thesistally.steady_momentum import Position, trade

    db = open_journal(tmp_path / "sales.db")
    a = Account(positions={"A": Position(qty=10, symbol="A", basis=1000, mark=100)})
    a.orders["A"] = {
        "day": "2024-01-31",
        "budget": 900,
        "reason": "rebalance",
        "rank": 1,
        "buy": True,
    }
    _, _, turnover = trade(db, "A", "2024-02-01", a, {"A": quote("A", 100)}, rules(revision="r2"))
    assert a.positions["A"].qty == 9
    assert turnover == pytest.approx(99.95)


def test_replay_and_forward_share_daily_ledger_and_consecutive_dates(tmp_path, monkeypatch):
    from datetime import date

    from thesistally import steady_replay as replay

    dates = [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)]
    monkeypatch.setattr(replay.market, "sessions", lambda root: dates)
    monkeypatch.setattr(replay, "is_month_end", lambda root, day: day == dates[0])
    monkeypatch.setattr(
        replay.stage3_market,
        "session",
        lambda root, day, lbs: (True, {lb: ranks("Q") for lb in lbs}),
    )
    monkeypatch.setattr(
        replay, "opening_data", lambda root, day, wanted: {"Q": quote("Q", 100, 101)}
    )
    monkeypatch.setattr(replay.steady_actions, "terms", lambda root, *, revised: {})
    with open_journal(tmp_path / "test.db") as db:
        assert replay.step(tmp_path, db, "walk-1", dates[1:], dates[0], {}) == {}
        expected = [
            tuple(x)
            for x in db.execute(
                "select day,equity,invested,cash from sessions where run='A126-walk-1' order by day"
            )
        ]
    for d in dates[1:]:
        dbpath = replay.forward(tmp_path, d, tmp_path)
    replay.forward(tmp_path, d, tmp_path)
    with open_journal(dbpath) as db:
        actual = [
            tuple(x)
            for x in db.execute(
                "select day,equity,invested,cash from sessions where run='A126-forward' order by day"
            )
        ]
        assert actual == expected
        assert db.execute("select count(*) from fills").fetchone()[0] == 2


def test_restricted_bonus_keeps_slot_and_only_credits_on_first_eligible_open(tmp_path):
    import json

    from thesistally import steady_actions
    from thesistally.steady_momentum import Position, prepare_holdings, trade

    db = open_journal(tmp_path / "bonus.sqlite")
    record = next(
        t for t in json.loads(steady_actions.HOLDERS.read_text())["actions"] if t["type"] == "bonus"
    )
    isin = record["predecessor_isin"]
    term = {isin: record}
    a = Account(positions={isin: Position(113, 150, "OIL", 16950)})
    q2 = quote("OIL", 100, 101)
    q2["other_actions"] = ["Bonus 1:2"]
    assert not prepare_holdings(db, "A", "2024-07-02", a, {isin: q2}, term)
    assert a.locked(isin) == 56 and a.positions[isin].qty == 113
    plan(db, "A", "2024-07-02", a, ranks("ABC"), False)
    trade(db, "A", "2024-07-03", a, {isin: quote("OIL", 105)}, rules(revision="r2"))
    assert not a.positions and a.slots == {isin} and isin in a.orders
    assert not prepare_holdings(db, "A", "2024-07-04", a, {isin: quote("OIL", 104)}, term)
    assert prepare_holdings(db, "A", "2024-07-15", a, {isin: quote("OIL", 110, 112)}, term) == 56
    assert a.positions[isin].qty == 56 and not a.bonuses
    trade(db, "A", "2024-07-15", a, {isin: quote("OIL", 110)}, rules(revision="r2"))
    assert not a.positions and not a.slots
    assert [r[0] for r in db.execute("select qty from fills order by id")] == [113, 56]


def test_bajaj_split_and_bonus_have_separate_holder_tradability(tmp_path):
    import json

    from thesistally import steady_actions
    from thesistally.steady_momentum import Position, prepare_holdings

    db = open_journal(tmp_path / "bajaj.sqlite")
    t = next(
        t
        for t in json.loads(steady_actions.HOLDERS.read_text())["actions"]
        if t["predecessor_isin"] == "INE296A01024"
    )
    old, new = t["predecessor_isin"], t["successor_isin"]
    a = Account(positions={old: Position(7, 9331, "BAJFINANCE", 65317)})
    for d, qty in [("2025-06-16", 0), ("2025-06-17", 14), ("2025-06-18", 70)]:
        assert not prepare_holdings(db, "A", d, a, {new: quote("BAJFINANCE", 950)}, {old: t})
        assert (a.positions[new].qty if new in a.positions else 0) == qty
        assert len(a.slots) == 1
    assert a.positions[new].basis == pytest.approx(65317)


def test_revised_entry_coverage_unknown_verified_and_missing_quotes(tmp_path):
    from thesistally.steady_metrics import purchase_coverage, read_days, summarize
    from thesistally.steady_replay import execution_config

    db = open_journal(tmp_path / "account.sqlite")
    cfg = execution_config()
    seed(
        db,
        "A",
        "2024-01-31",
        ranks("UNKNOWN", "BEYOND", "LOCKED", "MISSING", "NO_VOLUME"),
        True,
        {},
    )
    prices = {
        "UNKNOWN": quote("UNKNOWN", 100, 110, ceiling=None),
        "BEYOND": quote("BEYOND", 105, 110, ceiling=99),
        "LOCKED": quote("LOCKED", 110, ceiling=110),
        "NO_VOLUME": {**quote("NO_VOLUME", 100), "volume": 0},
    }
    advance(db, "A", "2024-02-01", "2024-01-31", None, True, prices, cfg, {})
    fills = list(db.execute("SELECT isin,raw_open,price,unknown_band FROM fills ORDER BY isin"))
    assert [(r[0], r[3]) for r in fills] == [("BEYOND", 1), ("UNKNOWN", 1)]
    assert all(r[2] == pytest.approx(r[1] * 1.0005) for r in fills)
    assert db.execute("SELECT count(*) FROM events WHERE kind='band_unknown'").fetchone()[0] == 2
    covered = purchase_coverage(db, "A")
    assert (
        covered["planned"],
        covered["filled"],
        covered["filled_band_unknown"],
        covered["data_blocked"],
    ) == (5, 2, 2, 1)
    assert covered["blocked_by_reason"] == {
        "verified_upper_lock": 1,
        "missing_open": 1,
        "zero_traded_volume": 1,
    }
    assert not covered["coverage_pass"]
    advance(db, "A", "2024-02-02", "2024-02-01", ranks(), False, prices, cfg, {})
    advance(db, "A", "2024-02-05", "2024-02-02", None, False, prices, cfg, {})
    advance(db, "A", "2024-02-06", "2024-02-05", None, False, {}, cfg, {})
    rows = read_days(
        db,
        "A",
        {
            "2024-01-31": 100,
            "2024-02-01": 110,
            "2024-02-02": 111,
            "2024-02-05": 121,
            "2024-02-06": 150,
        },
    )
    assert [r["at_risk"] for r in rows] == [True, True, True, False]
    assert summarize(rows)["while_invested"]["tri_return_pct"] == pytest.approx(21)


def test_coverage_exact_tenth_passes_and_valuation_gaps_always_excluded(tmp_path):
    import json
    from datetime import date

    from thesistally.steady_metrics import measure, purchase_coverage

    db = open_journal(tmp_path / "window.sqlite")
    db.execute(
        "INSERT INTO events(run,day,isin,kind,detail) VALUES(?,?,?,?,?)",
        (
            "A-w",
            "2024-01-02",
            "Q",
            "missed",
            json.dumps({"planned_purchase": True, "reason": "missing_open"}),
        ),
    )
    for i in range(9):
        db.execute(
            "INSERT INTO fills(run,day,signal,isin,side,qty,raw_open,price,fee,slippage,reason,unknown_band) VALUES('A-w','2024-01-02','2023-12-29',?,'buy',1,100,100,0,0,'new',0)",
            (str(i),),
        )
    assert purchase_coverage(db, "A-w")["coverage_pass"]
    assert purchase_coverage(db, "other")["data_blocked_pct"] is None
    output = measure(
        db, ["A"], [("w", [date(2024, 1, 2)], [])], {}, {"A-w": {"reason": "held stock missing"}}
    )["A"]
    assert output["qualification"]["complete_windows"] == 0
    assert output["qualification"]["verdict"] == "inconclusive"


def test_unpriced_annual_account_cannot_give_a_positive_study_verdict(tmp_path):
    from datetime import date

    from thesistally.steady_metrics import measure

    db = open_journal(tmp_path / "accounts.sqlite")
    db.execute(
        "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("A-good", "2024-01-02", "2023-12-29", 1, 1, 1_010_000, 100_000, 910_000, 0, 0, 0, 0, "{}"),
    )
    result = measure(
        db,
        ["A"],
        [("good", [date(2024, 1, 2)], []), ("gap", [date(2025, 1, 2)], [])],
        {"2024-01-02": 100, "2023-12-29": 100},
        {"A-gap": {"reason": "missing held mark"}},
    )["A"]
    q = result["qualification"]
    assert (
        q["complete_windows"] == 1 and q["breadth_pass"] and q["cagr_pass"] and q["drawdown_pass"]
    )
    assert q["verdict"] == "inconclusive" and not q["qualified"]
    assert q["optimistic_breadth"] == {
        "wins_if_all_unpriced_windows_win": 2,
        "windows_if_all_unpriced_windows_count": 2,
        "could_pass": True,
    }
    losing = measure(
        db,
        ["A"],
        [("good", [date(2024, 1, 2)], []), ("gap", [date(2025, 1, 2)], [])],
        {"2024-01-02": 110, "2023-12-29": 100},
        {"A-gap": {"reason": "missing held mark"}},
    )["A"]["qualification"]
    assert losing["optimistic_breadth"]["could_pass"] is False
