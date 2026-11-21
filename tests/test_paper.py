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
