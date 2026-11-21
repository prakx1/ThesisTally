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
