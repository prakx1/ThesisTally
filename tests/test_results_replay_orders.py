from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import DAY, MARKET, REGIME, calendar, prices

from thesistally import journal as j
from thesistally.ranking import order
from thesistally.results_replay_orders import candidates, settings


def signal(sue=2.0, isin="I1"):
    rows = pd.DataFrame(
        [
            {
                "day": str(DAY),
                "usable_session": str(DAY - timedelta(days=1)),
                "symbol": "S",
                "isin": isin,
                "sector": "Banks",
                "close": 100,
                "atr14": 2.0,
                "sue": sue,
                "reaction": 0.03,
                "filing_id": "nse:123",
            }
        ]
    )
    return candidates(rows, {isin: "S"}, {"S": {"surveillance": False}})[0]


def test_regime_liquidity_surveillance_and_priority():
    independent, funded = settings(40, "equal_risk"), settings(40, "funded")
    failed_gate = {"enabled": False}
    pick = signal()
    assert j.exclusion(pick, funded, failed_gate) == "regime"
    assert j.exclusion(pick, independent, failed_gate) is None
    pick.features.update(earnings_coverage="unknown", earnings_in_days=1)
    assert j.exclusion(pick, funded, REGIME) is None
    pick.features["surveillance"] = "unknown"
    assert j.exclusion(pick, independent, failed_gate) == "surveillance_unknown"
    picks = [signal(2, "Z"), signal(3, "B"), signal(3, "A")]
    assert [c.isin for c in order(picks, REGIME, funded)] == ["A", "B", "Z"]


def test_fill_based_stop_equality_then_next_tradable_open(tmp_path):
    cfg = settings(40, "equal_risk")
    with closing(j.journal(tmp_path / "ledger")) as db, db:
        j.process(db, "D", DAY, REGIME, [signal()], calendar(DAY), cfg, unconstrained=True)
        call = lambda: db.execute("SELECT * FROM calls").fetchone()
        for i, (o, h, lo, c) in enumerate(
            [
                (101, 200, 90, 101),
                (100, 125, 90, 101 * 1.0005 - 6),
                (94, 96, 90, 93),
                (90, 96, 90, 95),
            ],
            1,
        ):
            j.advance(
                db,
                call(),
                str(DAY + timedelta(days=i)),
                prices(o, h, lo, c),
                cfg,
                {**MARKET, "floor": None, "ceiling": None},
            )
            if i == 1:
                assert call()["buy"] == pytest.approx(101 * 1.0005)
                assert call()["size"] == int(5000 / 6)
                assert call()["stop"] == pytest.approx(101 * 1.0005 - 6)
                assert call()["pending_exit"] is None
            if i == 2:
                assert call()["pending_exit"] is None
            if i == 3:
                assert call()["pending_exit"] == "Closing stop"
        assert call()["status"] == "closed"
        assert db.execute("SELECT price FROM fills WHERE side='sell'").fetchone()[
            0
        ] == pytest.approx(90 * 0.9995)


@pytest.mark.parametrize("horizon", [20, 40])
def test_registered_time_sale_counts_purchase_session_and_no_target(tmp_path, horizon):
    cfg = settings(horizon, "funded")
    with closing(j.journal(tmp_path / "ledger")) as db, db:
        j.process(db, "D", DAY, REGIME, [signal()], calendar(DAY), cfg)
        for i in range(1, horizon + 1):
            row = db.execute("SELECT * FROM calls").fetchone()
            assert row["pending_exit"] is None and row["status"] != "closed"
            j.advance(db, row, str(DAY + timedelta(days=i)), prices(100, 200, 95, 106), cfg, MARKET)
        assert (
            db.execute("SELECT detail FROM events WHERE kind='closed'").fetchone()[0]
            == "Scheduled maximum holding close"
        )
