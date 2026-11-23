import json
from contextlib import closing
from datetime import date

import pandas as pd
import pytest
from test_dip import listing
from test_research import MARKET, REGIME, calendar, prices

from thesistally import corporate, dip, steady_actions
from thesistally.journal import advance, advance_from_frame, journal, process
from thesistally.signals import rules


def test_persistent_holder_sells_sixteen_shares_after_frozen_time_exit(tmp_path):
    cfg = rules(revision="dip-rsi10")
    original = "INE262H01013"
    successor = "INE262H01021"
    term = next(
        t
        for t in json.loads(steady_actions.HOLDERS.read_bytes())["actions"]
        if t["predecessor_isin"] == original
    )
    assert (term["effective"], term["credited"], term["tradable"]) == (
        "2024-03-28",
        "2024-04-02",
        "2024-04-02",
    )
    context = {
        **MARKET,
        "floor": None,
        "ceiling": 10000,
        "surveillance": False,
        "earnings_in_days": None,
    }
    rank = listing(
        symbol="PERSISTENT",
        isin=original,
        close=8248.35,
        atr14=201.9909723915482,
        prior_high10=8744,
        rsi2=5.160866,
        ema100=7447.5,
    )
    setup = dip.candidates(rank, {"PERSISTENT": context}, cfg)
    day = date(2024, 3, 5)
    with closing(journal(tmp_path / "journal.sqlite")) as db, db:
        process(db, "P", day, REGIME, setup, calendar(day), cfg, unconstrained=True)

        def row():
            return db.execute("SELECT * FROM calls").fetchone()

        advance(db, row(), "2024-03-06", prices(8246, 8300, 8170, 8200), cfg, context)
        before = dict(row())
        assert before["size"] == 8
        db.execute(
            "UPDATE calls SET age=14,mae=-0.64,mfe=0.33"
        )  # Already observed through March 27.
        tracker = {**context, "compulsory_action": term, "last_traded_price": 8099.65}
        for day, bar in [
            ("2024-03-28", prices(4069, 4096.65, 3947.45, 3984.55)),
            ("2024-04-01", prices(4069.9, 4076.4, 3932, 3996.65)),
            ("2024-04-02", prices(3989, 3989, 3951.1, 3970.25)),
        ]:
            f = pd.DataFrame([vars(bar)], index=[date.fromisoformat(day)])
            f.attrs["exchange"] = tracker
            advance_from_frame(db, row(), day, {"PERSISTENT": f, original: f}, cfg)
            if day < term["tradable"]:
                assert (
                    row()["pending_exit"] == "Maximum holding period during compulsory suspension"
                )
                assert not db.execute("SELECT 1 FROM fills WHERE side='sell'").fetchone()
        sale = db.execute(
            "SELECT f.*, s.isin FROM fills f JOIN fill_securities s ON s.fill_id=f.id WHERE f.side='sell'"
        ).fetchone()
        assert (sale["day"], sale["isin"], sale["qty"]) == ("2024-04-02", successor, 16)
        assert sale["price"] == pytest.approx(3989 * 0.9995)
        c = row()
        assert c["net"] == pytest.approx(
            (sale["price"] * 16 - before["buy"] * 8)
            - db.execute("SELECT SUM(fees) FROM fills").fetchone()[0]
        )
        assert c["outcome_r"] == pytest.approx(c["net"] / (8 * (before["buy"] - before["stop"])))
        assert (c["stop"], c["target"], c["mae"], c["mfe"]) == (before["stop"], 8744, -0.64, 0.33)
        post = corporate.effective_call(c, corporate.state(db, c["id"]))
        assert (post["stop"], post["target"]) == (before["stop"] / 2, 4372)
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE kind='exit_unknown_price_band'"
            ).fetchone()[0]
            == 1
        )


def test_bonus_origin_can_exit_before_listing_with_fraction_settled_at_first_close(tmp_path):
    from thesistally import conventions, dip_bonus, paper

    cfg = {**rules(revision="dip-rsi10"), "entry_band_convention": conventions.ENTRY_BAND_VERSION}
    ctx = {
        **MARKET,
        "floor": None,
        "ceiling": None,
        "surveillance": False,
        "earnings_in_days": None,
    }
    term = next(
        t
        for t in json.loads(steady_actions.REVISED_HOLDERS.read_bytes())["actions"]
        if t["predecessor_isin"] == "INE129A01019"
    )
    leg = term["bonus"]
    with closing(journal(tmp_path / "journal.sqlite")) as db, db:
        day = date(2017, 3, 7)
        plan = dip.candidates(
            listing(symbol="GAIL", isin=term["predecessor_isin"]), {"GAIL": ctx}, cfg
        )
        process(db, "B", day, REGIME, plan, calendar(day), cfg, unconstrained=True)

        def call():
            return db.execute("SELECT * FROM calls").fetchone()

        advance(db, call(), "2017-03-08", prices(100, 101, 99, 100), cfg, ctx)
        initial = dict(call())
        qty = initial["size"]
        whole, rem = divmod(qty, 3)
        assert rem > 0
        db.execute("UPDATE calls SET pending_exit='Closing stop'")
        ex = {**ctx, "dip_bonus_accounting": True, "other_actions": [" Bonus 1:3"]}
        dip_bonus.attach(ex, term, {"close": 100})

        def session(day, bar, context):
            f = pd.DataFrame([vars(bar)], index=[date.fromisoformat(day)])
            f.attrs["exchange"] = {**context, "dip_bonus_accounting": True}
            frames = {"GAIL": f, term["predecessor_isin"]: f}
            advance_from_frame(db, call(), day, frames, cfg)
            return frames

        frames = session("2017-03-09", prices(75, 78, 74, 76), ex)
        first = db.execute("SELECT * FROM fills WHERE side='sell'").fetchone()
        assert (first["qty"], first["price"]) == (qty, 75 * 0.9995)
        assert call()["status"] == "active" and call()["remaining"] == 0
        position, _, mark = paper.valuation(db, call(), frames)
        assert position["remaining"] == whole and mark == pytest.approx(qty / 3 * 75)
        assert not db.execute("SELECT 1 FROM corporate_cash").fetchone()
        session(leg["credited"], prices(78, 80, 75, 77), ctx)
        assert db.execute("SELECT count(*) FROM fills WHERE side='sell'").fetchone()[0] == 1
        session(leg["tradable"], prices(80, 83, 79, 82), ctx)
        assert call()["status"] == "closed"
        second = db.execute("SELECT * FROM fills WHERE side='sell' ORDER BY id DESC").fetchone()
        assert (second["day"], second["qty"], second["price"]) == (
            leg["tradable"],
            whole,
            80 * 0.9995,
        )
        paid = db.execute("SELECT * FROM corporate_cash").fetchone()
        assert paid["amount"] == corporate.cash(rem / 3, leg.get("fraction_price", 82))
        assert call()["outcome_r"] == pytest.approx(
            call()["net"] / ((initial["buy"] - initial["stop"]) * qty)
        )


def test_bonus_pending_open_has_no_entitlement_and_preserves_planned_rupee_risk(tmp_path):
    from thesistally import conventions, dip_bonus

    cfg = {**rules(revision="dip-rsi10"), "entry_band_convention": conventions.ENTRY_BAND_VERSION}
    ctx = {
        **MARKET,
        "floor": None,
        "ceiling": None,
        "surveillance": False,
        "earnings_in_days": None,
    }
    term = next(
        t
        for t in json.loads(steady_actions.REVISED_HOLDERS.read_bytes())["actions"]
        if t["predecessor_isin"] == "INE129A01019"
    )
    with closing(journal(tmp_path / "journal.sqlite")) as db, db:
        day = date(2017, 3, 8)
        plan = dip.candidates(
            listing(symbol="GAIL", isin=term["predecessor_isin"]), {"GAIL": ctx}, cfg
        )
        process(db, "B", day, REGIME, plan, calendar(day), cfg, unconstrained=True)
        original = db.execute("SELECT * FROM calls").fetchone()
        dip_bonus.attach(ctx, term, {"close": 100})
        frame = pd.DataFrame([vars(prices(75, 76, 74, 75))], index=[date(2017, 3, 9)])
        frame.attrs["exchange"] = ctx
        advance_from_frame(db, original, "2017-03-09", {"GAIL": frame}, cfg)
        filled = db.execute("SELECT * FROM calls").fetchone()
        assert filled["status"] == "active"
        assert filled["target"] == original["target"] * 0.75
        assert filled["buy"] - filled["stop"] == pytest.approx(4.5)
        assert not dip_bonus.tranches(db, filled["id"])


def test_combined_bonus_preserves_original_share_cash_dividend_exactly_once():
    from thesistally import dip_bonus

    subject = " Bonus 1:2/ Dividend - Rs 1.10 Per Share"
    term = {"bonus": {"dividend_per_original": 1.10, "subjects": [subject]}}
    context = {"dividend": 0, "other_actions": [subject, "Unrelated open issue"]}
    for _ in range(2):
        dip_bonus.attach(context, term, {"close": 100})
    assert context["dividend"] == pytest.approx(1.10)
    assert context["other_actions"] == ["Unrelated open issue"]
