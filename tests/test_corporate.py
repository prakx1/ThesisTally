import json
from datetime import date
from types import SimpleNamespace

import pytest
from test_research import MARKET, REGIME, calendar, pick, prices

from thesistally import corporate
from thesistally.journal import advance, journal, process
from thesistally.signals import rules

CFG = rules()


def purchased(tmp_path):
    db = journal(tmp_path / "ledger.sqlite")
    process(db, "research", date(2024, 8, 9), REGIME, [pick()], calendar(date(2024, 8, 9)), CFG)
    db.execute("UPDATE calls SET size=209")
    row = lambda: db.execute("SELECT * FROM calls").fetchone()
    advance(db, row(), "2024-08-12", prices(), CFG, MARKET)
    return db, row


@pytest.mark.parametrize("published,amount", [(None, 327.80), (970.0, 291.0)])
def test_compulsory_freeze_fractional_cash_and_original_risk(tmp_path, published, amount):
    db, row = purchased(tmp_path)
    terms = next(iter(corporate.actions(tmp_path).values())).copy()
    terms["fraction_price"] = published
    ctx = {**MARKET, "compulsory_action": terms, "last_traded_price": 100}
    before = dict(row())
    bar = SimpleNamespace(**{**vars(prices()), "symbol": "TATAMOTORS", "close": 1092.65})
    for d in ("2024-08-30", "2024-09-02", "2024-09-25"):
        assert corporate.settle(db, row(), d, bar, CFG, ctx, corporate.state(db, 1))
        assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    p = corporate.state(db, 1)
    assert row()["remaining"] == 146
    assert p["fraction"] == pytest.approx(0.3)
    assert p["frozen_value"] + amount == pytest.approx(20900)
    assert db.execute("SELECT amount FROM corporate_cash").fetchone()[0] == amount
    assert before["stop"] == row()["stop"] and before["buy"] == row()["buy"]
    assert corporate.effective_call(row(), p)["stop"] == pytest.approx(before["stop"] * 10 / 7)
    db.execute("UPDATE calls SET pending_exit='Time stop during compulsory suspension'")
    assert not corporate.settle(db, row(), "2024-09-26", bar, CFG, ctx, p)
    advance(
        db,
        corporate.effective_call(row(), corporate.state(db, 1)),
        "2024-09-26",
        prices(open_=140),
        CFG,
        ctx,
    )
    assert row()["outcome_r"] == pytest.approx(
        row()["net"] / ((before["buy"] - before["stop"]) * 209)
    )
    event = [
        json.loads(e[0])
        for e in db.execute("SELECT detail FROM events WHERE kind='corporate_action'")
    ]
    credited = next(e for e in event if e["stage"] == "credited")
    assert credited["flagged"] == (published is None)
    assert credited["sensitivity_zero_fraction_cash_delta"] == -amount
    assert credited["source_url"] and credited["convention_version"] == corporate.VERSION
    if published is None:
        assert "20240902" in credited["fractional_cash_source"]


@pytest.mark.parametrize("offer,price", [(122, 122), (None, 94)])
def test_delisting_uses_documented_price_or_flags_last_trade(tmp_path, offer, price):
    db, row = purchased(tmp_path)
    t = {
        "type": "delisting",
        "predecessor_isin": "A",
        "suspended": "2024-08-20",
        "last_trade": "2024-08-19",
        "effective": "2024-08-20",
        "offer_price": offer,
        "source_url": "https://example.test/offer",
    }
    assert corporate.settle(
        db,
        row(),
        t["effective"],
        prices(),
        CFG,
        {"compulsory_action": t, "last_traded_price": 94},
        None,
    )
    assert row()["status"] == "closed"
    assert db.execute("SELECT amount FROM corporate_cash").fetchone()[0] == price * 209
    e = json.loads(
        db.execute(
            "SELECT detail FROM events WHERE kind='corporate_action' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert e["flagged"] and e["price_basis"] == (
        "published_offer" if offer else "last_trade_fallback"
    )


@pytest.mark.parametrize("published,expected", [(120, 36), (None, 30)])
def test_shares_can_exit_before_later_fractional_cash_settlement(tmp_path, published, expected):
    db, row = purchased(tmp_path)
    t = {
        "type": "merger",
        "predecessor_isin": "A",
        "successor_isin": "B",
        "suspended": "2024-08-20",
        "last_trade": "2024-08-19",
        "effective": "2024-08-20",
        "credited": "2024-08-21",
        "tradable": "2024-08-22",
        "cash_date": "2024-08-29",
        "new_shares": 7,
        "old_shares": 10,
        "fraction_price": published,
        "source_url": "https://example.test/scheme",
        "credit_source": "https://example.test/credit",
    }
    (tmp_path / "corporate_actions.json").write_text(json.dumps([t]))
    ctx = {**MARKET, "compulsory_action": t, "last_traded_price": 100}
    for d in ("2024-08-20", "2024-08-21"):
        assert corporate.settle(db, row(), d, prices(), CFG, ctx, corporate.state(db, 1))
    assert not db.execute("SELECT 1 FROM corporate_cash").fetchone()
    assert db.execute("SELECT estimate FROM corporate_receivables").fetchone()[0] == 30
    db.execute("UPDATE calls SET pending_exit='Time stop' WHERE id=1")
    assert not corporate.settle(db, row(), "2024-08-22", prices(), CFG, ctx, corporate.state(db, 1))
    advance(
        db,
        corporate.effective_call(row(), corporate.state(db, 1)),
        "2024-08-22",
        prices(open_=140),
        CFG,
        ctx,
    )
    assert row()["status"] == "awaiting_cash" and row()["outcome_r"] is None
    corporate.pay_receivables(db, "research", "2024-08-28")
    assert row()["status"] == "awaiting_cash"
    corporate.pay_receivables(db, "research", "2024-08-29")
    assert (
        row()["status"] == "closed"
        and db.execute("SELECT amount FROM corporate_cash").fetchone()[0] == expected
    )
    paid = json.loads(
        db.execute(
            "SELECT detail FROM events WHERE kind='corporate_action' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert paid["fractional_cash_source"] == (
        t["source_url"]
        if published
        else "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240820_F_0000.csv.zip"
    )


def test_credit_before_first_listing_waits_for_observed_close(tmp_path):
    db, row = purchased(tmp_path)
    terms = next(iter(corporate.actions(tmp_path).values())).copy()
    ctx = {**MARKET, "compulsory_action": terms, "last_traded_price": 100}
    suspended = SimpleNamespace(volume=0)
    for day in ("2024-08-30", "2024-09-02", terms["credited"]):
        assert corporate.settle(db, row(), day, suspended, CFG, ctx, corporate.state(db, 1))
    assert not db.execute("SELECT 1 FROM corporate_cash").fetchone()
    assert not corporate.settle(
        db, row(), terms["tradable"], prices(), CFG, ctx, corporate.state(db, 1)
    )
    assert db.execute("SELECT amount FROM corporate_cash").fetchone()[0] == 30


def test_off_index_quote_tracks_traded_equity_series_and_rejects_ambiguity(tmp_path, monkeypatch):
    monkeypatch.setattr(corporate.market, "verify", lambda *args: None)
    tape = tmp_path / "sessions" / "2024-10-01" / "bhavcopy.csv"
    tape.parent.mkdir(parents=True)
    header = "ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
    tape.write_text(
        header + "A,NEW,EQ,1,1,1,1,0\nA,NEW,BE,9,12,8,11,100\n"
        "B,ONE,BZ,11,12,8,11,100\nB,TWO,EQ,12,12,8,11,100\n"
        "C,THREE,BZ,15,16,12,13,10\n"
    )
    assert corporate.quote(tmp_path, "2024-10-01", "A")["series"] == "BE"
    assert corporate.quote(tmp_path, "2024-10-01", "A")["open"] == 9
    assert corporate.quote(tmp_path, "2024-10-01", "B") is None
    assert corporate.quote(tmp_path, "2024-10-01", "C")["series"] == "BZ"


def test_quarantined_successor_close_cannot_set_fraction_cash(tmp_path):
    from thesistally.journal import UnpricedPosition

    db, row = purchased(tmp_path)
    terms = next(iter(corporate.actions(tmp_path).values()))
    ctx = {**MARKET, "compulsory_action": terms, "last_traded_price": 100, "quarantined": True}
    with pytest.raises(UnpricedPosition, match="Quarantined successor close"):
        corporate.settle(db, row(), "2024-09-02", prices(), CFG, ctx, None)
    assert not db.execute("SELECT 1 FROM corporate_cash").fetchone()


def test_frozen_two_level_touch_waits_for_first_tradable_open(tmp_path):
    db, row = purchased(tmp_path)
    terms = next(iter(corporate.actions(tmp_path).values()))
    ctx = {**MARKET, "compulsory_action": terms, "last_traded_price": 100}
    bar = prices(open_=145, high=175, low=127, close=150)
    assert corporate.settle(db, row(), "2024-09-02", bar, CFG, ctx, None)
    assert row()["pending_exit"] == "Stop-first collision during compulsory suspension"
    assert not db.execute("SELECT 1 FROM fills WHERE side='sell'").fetchone()
    assert corporate.settle(db, row(), "2024-09-03", bar, CFG, ctx, corporate.state(db, 1))
    assert [
        tuple(e) for e in db.execute("SELECT day,detail FROM events WHERE kind='exit_due'")
    ] == [("2024-09-02", "Stop-first collision during compulsory suspension")]
    p = corporate.state(db, 1)
    bar = prices(open_=140, low=135, high=147, close=145)
    assert not corporate.settle(db, row(), terms["tradable"], bar, CFG, ctx, p)
    advance(
        db,
        corporate.effective_call(row(), corporate.state(db, 1)),
        terms["tradable"],
        bar,
        CFG,
        ctx,
    )
    (sale,) = db.execute("SELECT * FROM fills WHERE side='sell'").fetchall()
    assert sale["qty"] == 146 and sale["price"] == pytest.approx(140 * 0.9995)


def test_split_after_partial_sale_counts_only_residual_shares_and_waits_for_holder_credit(tmp_path):
    import pandas as pd

    from thesistally.journal import advance_from_frame, record
    from thesistally.paper import valuation
    from thesistally.signals import Candidate

    # This seeded call reconstructs the r1 Stage 1 COFORGE audit.
    cfg = rules(revision="r1")
    db = journal(tmp_path / "ledger.sqlite")
    predecessor, successor = "INE591G01017", "INE591G01025"
    symbol = "COFORGE"
    terms = {
        "type": "stock_split",
        "predecessor_isin": predecessor,
        "successor_isin": successor,
        "new_shares": 5,
        "old_shares": 1,
        "last_trade": "2025-06-03",
        "suspended": "2025-06-04",
        "effective": "2025-06-04",
        "credited": "2025-06-05",
        "tradable": "2025-06-06",
        "source_url": "https://nsearchives.nseindia.com/content/circulars/CML68318.pdf",
        "credit_source": "https://www.coforge.com/hubfs/Annual-Report-2025.pdf",
    }
    db.execute("INSERT INTO runs VALUES(?,?,?,?)", ("audit", cfg["revision"], 1e6, 1e6))
    record(
        db,
        "audit",
        "2025-05-08",
        REGIME,
        Candidate(symbol, predecessor, "IT", "trend_pullback", 7754.5, 7336, 8591.5, {}),
        cfg,
        5,
        "portfolio",
    )

    def row():
        return db.execute("SELECT * FROM calls").fetchone()

    def successor_session(day, bar):
        frame = pd.DataFrame([vars(bar)], index=[day])
        frame.attrs["exchange"] = {
            **MARKET,
            "floor": 1500,
            "ceiling": 2000,
            "compulsory_action": terms,
            "last_traded_price": 8499,
        }
        frames = {predecessor: frame, symbol: frame}
        advance_from_frame(db, row(), day, frames, cfg)
        return frames

    band = {**MARKET, "ceiling": 9500}
    advance(db, row(), "2025-05-09", prices(open_=7555, high=7745, low=7555, close=7681), cfg, band)
    advance(
        db, row(), "2025-05-29", prices(open_=8544, high=8699, low=8515.5, close=8630.5), cfg, band
    )
    assert row()["remaining"] == 3 and row()["partial"] == 1
    db.execute("UPDATE calls SET age=18, trail=8246.79970690922")
    first = successor_session("2025-06-04", prices(open_=1719, high=1730.5, low=1694, close=1724.5))
    assert row()["remaining"] == 15 and row()["size"] == 5 and row()["age"] == 19
    assert row()["trail"] == pytest.approx(1649.359941381844)
    _, frozen_close, frozen_value = valuation(db, row(), first)
    assert frozen_close is None and frozen_value == 3 * 8499
    successor_session("2025-06-05", prices(open_=1724.5, low=1713, high=1792, close=1766))
    assert row()["pending_exit"] == "Maximum holding period during compulsory suspension"
    assert not db.execute(
        "SELECT 1 FROM fills WHERE day BETWEEN '2025-06-04' AND '2025-06-05'"
    ).fetchone()
    successor_session("2025-06-06", prices(open_=1779.5, low=1744, high=1783.5, close=1775.5))
    fill = db.execute(
        "SELECT * FROM fills JOIN fill_securities ON fills.id=fill_securities.fill_id WHERE day='2025-06-06'"
    ).fetchone()
    assert fill["isin"] == successor and fill["qty"] == 15
    assert fill["price"] == pytest.approx(1779.5 * 0.9995)
    assert row()["outcome_r"] == pytest.approx(row()["net"] / ((row()["buy"] - 7336) * 5))


@pytest.mark.parametrize(
    "age,close,reason",
    [
        (10, 110, None),
        (10, 90, None),
        (10, 89, "Closing stop breached during compulsory suspension"),
        (10, 120, "Range target close"),
        (19, 120, "Maximum holding period during compulsory suspension"),
    ],
)
def test_range_freeze_uses_translated_closing_levels_and_registered_horizon(
    tmp_path, age, close, reason
):
    db, row = purchased(tmp_path)
    db.execute("UPDATE calls SET trail=180, target=240, age=?", (age,))
    cfg = rules(corporate.REGISTRY.with_name("range-c50.yaml"))
    term = dict(next(iter(corporate.actions(tmp_path).values())), old_shares=1, new_shares=2)
    ctx = {**MARKET, "compulsory_action": term, "last_traded_price": 220}
    bar = prices(open_=105, high=130, low=70, close=close)
    assert corporate.settle(db, row(), term["effective"], bar, cfg, ctx, None)
    assert row()["pending_exit"] == reason
    assert not db.execute("SELECT 1 FROM fills WHERE side='sell'").fetchone()
