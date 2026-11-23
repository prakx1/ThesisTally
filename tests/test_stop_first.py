from contextlib import closing

import pytest
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally.journal import advance, cost, journal, process


@pytest.mark.parametrize("quantity,age", [(1, 0), (9, 0), (9, 19)])
def test_both_levels_queue_full_exit_at_first_tradable_open(tmp_path, quantity, age):
    with closing(journal(tmp_path / "ledger.sqlite")) as db:
        process(db, "pilot-test", DAY, REGIME, [pick()], calendar(DAY), CFG)

        def call():
            return db.execute("SELECT * FROM calls").fetchone()

        with db:
            db.execute("UPDATE calls SET size=?,age=?", (quantity, age))
            # Equal levels count even with a strong close. On day 20 the
            # collision still takes priority over the scheduled maximum close.
            advance(db, call(), "2026-10-05", prices(high=120, low=90, close=119), CFG, MARKET)
            first = call()
            assert first["status"] == "active" and first["partial"] == 0
            assert (
                first["remaining"] == quantity and first["pending_exit"] == "Stop-first collision"
            )
            assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1

            advance(db, call(), "2026-10-06", prices(open_=50, low=50, close=62), CFG, MARKET)
            assert call()["remaining"] == quantity
            advance(db, call(), "2026-10-07", prices(open_=80, low=75), CFG, MARKET)
        sale = db.execute("SELECT * FROM fills WHERE side='sell'").fetchone()
        assert sale["day"] == "2026-10-07" and sale["qty"] == quantity
        assert sale["price"] == pytest.approx(80 * (1 - CFG["cost"]["slippage_bps"] / 10000))
        assert sale["fees"] == cost("sell", sale["price"] * quantity, CFG)
        assert call()["status"] == "closed"
        assert [
            r[0]
            for r in db.execute(
                "SELECT kind FROM events WHERE kind IN ('exit_due','exit_deferred','closed') ORDER BY id"
            )
        ] == ["exit_due", "exit_deferred", "closed"]


@pytest.mark.parametrize("close,pending", [(91, None), (90, "Closing stop")])
def test_stop_only_retests_the_close(tmp_path, close, pending):
    with closing(journal(tmp_path / "ledger.sqlite")) as db:
        process(db, "pilot-test", DAY, REGIME, [pick()], calendar(DAY), CFG)
        c = db.execute("SELECT * FROM calls").fetchone()
        with db:
            advance(db, c, "2026-10-05", prices(high=102, low=87, close=close), CFG, MARKET)
        assert db.execute("SELECT pending_exit FROM calls").fetchone()[0] == pending
