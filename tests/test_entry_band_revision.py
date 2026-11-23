import json
from contextlib import closing

import pytest
from test_quarantine_timing import get_call
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import conventions
from thesistally.journal import advance, journal, process


@pytest.mark.parametrize(
    ("upper", "volume", "flagged", "blocked"),
    [
        (None, 1000, True, None),
        (98, 1000, True, None),
        (120, 1000, False, None),
        (99, 1000, False, "Verified upper band locked at open"),
        (None, 0, False, "No traded volume at open"),
    ],
)
def test_opening_evidence_and_flag_reconcile_to_executed_buy(
    tmp_path, upper, volume, flagged, blocked
):
    cfg = {**CFG, "entry_band_convention": conventions.ENTRY_BAND_VERSION}
    with closing(journal(tmp_path / "band.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick()], calendar(DAY), cfg)
        quote = prices(open_=99)
        quote.volume = volume
        advance(db, get_call(db), "2026-11-23", quote, cfg, {**MARKET, "ceiling": upper})
        flags = list(db.execute("SELECT detail FROM events WHERE kind='band_unknown'"))
        buys = list(db.execute("SELECT id,price FROM fills WHERE side='buy'"))
        assert len(flags) == int(flagged)
        if blocked:
            assert not buys
            assert (
                db.execute("SELECT detail FROM events WHERE kind='missed'").fetchone()[0] == blocked
            )
        else:
            assert buys[0]["price"] == pytest.approx(99 * (1 + cfg["cost"]["slippage_bps"] / 10000))
            if flags:
                detail = json.loads(flags[0][0])
                assert detail["fill_id"] == buys[0]["id"] and detail["side"] == "buy"
                assert detail["fill"] == buys[0]["price"] and detail["open"] == 99


def test_invalid_or_untraded_quote_is_not_evidence_of_an_opening_fill():
    for price, volume in [(0, 1), (float("nan"), 10), (None, 1), (99, float("nan")), (99, None)]:
        assert conventions.opening_purchase(price, volume, None, 5, allow_unknown=True) is None
    assert conventions.opening_purchase(99, 1, None, 5) is None


def test_nse_bajaj_finserv_cash_dividend_resolves_only_the_exact_notice():
    label = " Annual General Meeting/Div-Rs 1.75 Per Share"
    other = "Annual General Meeting/Div-Rs 5 Per Share and Bonus 1:2"
    raw = {"dividend": 0, "other_actions": [label, label, other]}
    revised = conventions.cash_dividend_notices(raw)
    assert revised == {"dividend": 1.75, "other_actions": [other]}
    assert conventions.cash_dividend_notices(revised) == revised
    assert raw["dividend"] == 0
