from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally.dashboard import Dashboard
from thesistally.journal import journal, process, record_action


def test_forward_paper_account_follows_calls_through_daily_marks_and_exit(tmp_path):
    dashboard = Dashboard(tmp_path)
    assert dashboard.paper_account()["history"] == []

    def scan(db, day, candidates, bar, dividend=0):
        frames = calendar(day)
        frame = pd.DataFrame([vars(bar)], index=[day])
        frame.attrs["exchange"] = {**MARKET, "dividend": dividend}
        frames.update({"A": frame, "Z": frame, "NIFTY 200": frame})
        process(db, "observations", day, REGIME, candidates, frames, CFG)

    with closing(journal(tmp_path / "journal.sqlite")) as db:
        shadow = pick("Z")
        shadow.features["surveillance"] = True
        scan(db, DAY, [pick(), shadow], prices())
        # Simulate a journal created before performance marks were recorded.
        with db:
            db.execute("DELETE FROM account_marks WHERE run='observations'")
        book = dashboard.paper_account()
        assert book["history"][0]["cash"] == book["history"][0]["equity"] == CFG["risk"]["capital"]
        assert [c["symbol"] for c in book["trades"]] == ["A"]
        cid = book["trades"][0]["id"]
        record_action(db, cid, "skip")

        d1, d2, d3 = (DAY + timedelta(days=i) for i in (1, 2, 3))
        scan(db, d1, [], prices(high=125, close=115))
        scan(db, d1, [], prices(high=125, close=115))
        book = dashboard.paper_account()
        trade, today = book["trades"][0], book["history"][0]
        fills = dashboard.paper_fills(str(d1))
        assert [f["side"] for f in fills] == ["buy", "sell"]
        assert all(f["call_id"] == cid for f in fills)
        cash = CFG["risk"]["capital"] + sum(
            (1 if f["side"] == "sell" else -1) * f["qty"] * f["price"] - f["fees"] for f in fills
        )
        assert today["cash"] == pytest.approx(cash)
        assert today["equity"] == pytest.approx(cash + 115 * trade["remaining"])
        assert today["daily_pnl"] == pytest.approx(trade["marked_pnl"])
        assert len(dashboard.paper_trade(cid)["marks"]) == 1
        scan(db, d2, [], prices(low=89, close=93), dividend=2)
        second = dashboard.paper_account()
        assert second["history"][0]["cash"] == pytest.approx(cash + 2 * trade["remaining"])
        assert second["trades"][0]["pending_exit"] == "Closing stop"
        assert second["history"][0]["total_pnl"] == pytest.approx(second["trades"][0]["marked_pnl"])
        scan(db, d3, [], prices(open_=85))

    final = Dashboard(tmp_path).paper_account()
    trade, last = final["trades"][0], final["history"][0]
    assert trade["status"] == "closed" and trade["remaining"] == 0
    assert last["cash"] == last["equity"] and last["invested"] == 0
    assert last["total_pnl"] == pytest.approx(trade["net"])
    assert len(final["history"]) == 4
    assert [m["day"] for m in dashboard.paper_trade(cid)["marks"]] == [str(d2), str(d1)]


def test_shadow_fills_at_gap_cap_and_keeps_tracking_after_original_miss(tmp_path):
    from thesistally import paper
    from thesistally.cli import open_calls

    path = tmp_path / "journal.sqlite"
    a, b = pick(), pick("B")
    b.features["surveillance"] = True
    with closing(journal(path)) as db:
        process(db, "observations", DAY, REGIME, [a, b], calendar(DAY), CFG)
        assert len(paper.comparisons(db)) == 2
        assert paper.pair_signals(db, str(DAY)) == 0
    with closing(journal(path)) as db:
        for i in (1, 1, 2, 3):
            d = DAY + timedelta(days=i)
            frames = calendar(d)
            for sym, op in (("A", 102), ("B", 102.01)):
                bar = prices(
                    open_=op if i == 1 else 88,
                    high=108,
                    low=95 if i == 1 else 87,
                    close=103 if i == 1 else 88,
                )
                frame = pd.DataFrame([vars(bar)], index=[d])
                frame.attrs["exchange"] = MARKET
                frames[sym] = frame
            frames["NIFTY 200"] = frames["A"]
            process(db, "observations", d, REGIME, [], frames, CFG)
            if i == 1:
                cmp = {r["symbol"]: r for r in paper.comparisons(db)}
                assert cmp["A"]["status"] == "missed" and cmp["A"]["shadow_status"] == "active"
                assert cmp["B"]["status"] == cmp["B"]["shadow_status"] == "missed"
                alt = cmp["A"]["shadow_id"]
                leg = db.execute("SELECT * FROM calls WHERE id=?", (alt,)).fetchone()
                assert leg["buy"] == pytest.approx(102 * 1.0005)
                assert leg["target"] == pytest.approx(leg["buy"] + 2 * (leg["buy"] - 90))
                assert [
                    r["isin"] for r in open_calls(db, "observations", d + timedelta(days=1))
                ] == ["A"]
        cmp = {r["symbol"]: r for r in paper.comparisons(db)}
        assert cmp["A"]["shadow_status"] == "closed" and cmp["A"]["shadow_r"] < 0
        assert cmp["A"]["outcome_r"] is None
        assert db.execute("SELECT count(*) FROM fills WHERE call_id=?", (alt,)).fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT count(*) FROM paper_position_marks WHERE call_id=?", (alt,)
            ).fetchone()[0]
            == 2
        )
    book = Dashboard(tmp_path).paper_account()
    assert book["history"][0]["total_pnl"] == 0
    assert len(book["trades"]) == 1 and len(book["comparisons"]) == 2
    assert not Dashboard(tmp_path).paper_fills(str(d))
