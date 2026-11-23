import json
from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import exchange
from thesistally.journal import UnpricedPosition, advance, journal, process

BAD_CLOSE = {**MARKET, "quarantined": True, "quarantine_reason": "unexplained_close_move"}


def get_call(db):
    return db.execute("SELECT * FROM calls WHERE run='study'").fetchone()


def test_close_discovered_jump_cannot_cancel_prior_stop_sale(tmp_path):
    with closing(journal(tmp_path / "ledger.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick()], calendar(DAY), CFG)
        advance(db, get_call(db), "2026-10-05", prices(low=87, close=88), CFG, MARKET)
        advance(
            db,
            get_call(db),
            "2026-10-06",
            prices(open_=89, low=69, high=90, close=70),
            CFG,
            BAD_CLOSE,
        )
        sale = db.execute("SELECT * FROM fills WHERE side='sell'").fetchone()
        assert sale["price"] == pytest.approx(89 * 0.9995)
        assert get_call(db)["status"] == "closed"


@pytest.mark.parametrize("funded", [True, False])
def test_close_quarantine_is_a_valuation_gap_after_opening_decision(tmp_path, funded):
    with closing(journal(tmp_path / "ledger.sqlite")) as db, db:
        c = pick()
        if not funded:
            c.features["surveillance"] = "unknown"
        process(db, "study", DAY, REGIME, [c], calendar(DAY), CFG)
        if funded:
            # A daily process rolls back and returns this gap for the entire funded book.
            with pytest.raises(UnpricedPosition, match="closing valuation") as err:
                advance(db, get_call(db), "2026-10-05", prices(low=69, close=70), CFG, BAD_CLOSE)
            assert err.value.detail["day"] == "2026-10-05"
        else:
            advance(db, get_call(db), "2026-10-05", prices(low=69, close=70), CFG, BAD_CLOSE)
            assert get_call(db)["status"] == "unmeasurable"
        assert db.execute("SELECT price FROM fills WHERE side='buy'").fetchone()[
            0
        ] == pytest.approx(99 * 1.0005)
        assert not db.execute("SELECT 1 FROM fills WHERE side='sell'").fetchone()


def test_unpriceable_funded_close_rolls_back_session_then_retries_once(tmp_path):
    next_day = DAY + timedelta(days=1)
    book = "study"
    frame = pd.DataFrame([vars(prices(low=69, close=70))], index=[next_day])
    frames = {**calendar(next_day), "A": frame}
    with closing(journal(tmp_path / "ledger.sqlite")) as db:
        process(db, book, DAY, REGIME, [pick()], calendar(DAY), CFG)
        frame.attrs["exchange"] = BAD_CLOSE
        with pytest.raises(UnpricedPosition, match="closing valuation"):
            process(db, book, next_day, REGIME, [], frames, CFG)
        assert get_call(db)["status"] == "confirmed"
        assert not db.execute("SELECT 1 FROM fills").fetchone()
        assert not db.execute("SELECT 1 FROM scans WHERE day=?", (str(next_day),)).fetchone()
        frame.attrs["exchange"] = MARKET
        for _ in range(2):
            process(db, book, next_day, REGIME, [], frames, CFG)
        assert db.execute("SELECT count(*) FROM fills WHERE side='buy'").fetchone()[0] == 1


def test_legacy_rounded_jump_clears_only_disproved_quarantine(tmp_path):
    day, isin = "2024-02-02", "INE982J01020"
    issue = {
        "day": day,
        "isin": isin,
        "reason": "unexplained_move_over_20pct",
        "prior_close": 609.0,
        "close": 487.2,
    }
    path = tmp_path / "sources" / "adjusted" / "quarantine.json"
    path.parent.mkdir(parents=True)
    for rows, expected in [
        ([issue], False),
        ([issue, {**issue, "reason": "unresolved_action"}], True),
        ([{**issue, "reason": "unresolved_action"}, issue], True),
        ([{**issue, "close": 487.19}], True),
    ]:
        path.write_text(json.dumps(rows))
        context = {"quarantined": True}
        exchange.apply_quarantine(context, exchange.price_quarantines(tmp_path, day), isin)
        assert context["quarantined"] is expected
