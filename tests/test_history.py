from datetime import date, timedelta

import pandas as pd
import pytest

from thesistally import constituents, history, market
from thesistally.historical_sessions import adjustment

DAY = date(2014, 1, 1)


def test_legacy_bhav_and_bad_dates():
    raw = b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,PREVCLOSE,TOTTRDVAL,ISIN,TIMESTAMP\nABC,EQ,100,105,98,102,123,99,12500,IN0000000001,01-JAN-2014\n"
    f = history.normalize_bhav(raw, DAY)
    assert f.iloc[0].ISIN == "IN0000000001" and f.iloc[0].ClsPric == "102"
    with pytest.raises(market.MarketError):
        history.normalize_bhav(raw, DAY + timedelta(days=1))


def test_ex_date_adjustment_terms():
    assert adjustment("Bonus 1:1", 10, 100) == (0.5, 2)
    assert adjustment("Face Value Split From Rs 10 To Rs 2", 2, 100) == (0.2, 5)
    assert adjustment("Rights 1:1 @ Premium Rs 40", 10, 100) == (0.75, 1)
    assert adjustment("Rights issue pending pricing", 10, 100) is None


def test_identity_survives_rename_and_mismatches_leave_gap():
    f = pd.DataFrame(
        [{"symbol": "NEW", "isin": "IN0000000001", "series": "EQ", "volume": 35, "value": 20000000}]
    ).set_index("symbol")
    ids = {"OLD": "IN0000000001"}
    assert list(constituents.reconcile_members(f, 35, 2, {"OLD"}, ids).index) == ["NEW"]
    assert constituents.reconcile_members(f, 35, 2.02, {"OLD"}, ids) is None
    assert constituents.reconcile_members(f, 36, 2, {"OLD"}, ids) is None
    days = [DAY + timedelta(days=i) for i in range(4)]
    assert history.ranges([days[0], days[1], days[3]], days) == [
        {"from": str(days[0]), "to": str(days[1])},
        {"from": str(days[3]), "to": str(days[3])},
    ]


def test_abbreviated_dividends_and_voluntary_actions():
    from thesistally.exchange import settle_notices

    info = settle_notices(
        {
            "dividend": 0.0,
            "other_actions": ["Dividend - Rs 2.50 Per Sh", "Annual General Meeting", "Buy Back"],
        }
    )
    assert info == {"dividend": 2.50, "other_actions": []}
    assert settle_notices(info) == info
    assert settle_notices({"other_actions": ["Demerger"]})["other_actions"] == ["Demerger"]
