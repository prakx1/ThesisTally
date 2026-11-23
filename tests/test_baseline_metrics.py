import json
import sqlite3
from contextlib import closing
from datetime import timedelta

import pandas as pd
import pytest
from test_research import DAY, MARKET, calendar, pick, prices

from thesistally.baseline_metrics import analyze
from thesistally.journal import journal, process
from thesistally.signals import rules


@pytest.fixture()
def replay(tmp_path):
    dates = [DAY + timedelta(days=i) for i in range(21)]
    path = tmp_path / "walk-1" / "journal.sqlite"
    path.parent.mkdir()
    books = {}
    with closing(journal(path)) as db:
        for rev in ["r1", "r2"]:
            for book in ["portfolio", "equal-risk"]:
                run = f"{rev}-{book}"
                cfg = rules(revision=rev)
                for i, d in enumerate(dates):
                    frames = calendar(d)
                    frames["NIFTY 200"] = pd.DataFrame([{"close": 100 + i}], index=[d])
                    if i == 1:
                        bar = prices(open_=98, low=97, high=109, close=100)
                    elif i == 2:
                        bar = prices(open_=98, low=88, high=104, close=89)
                    elif i == 3:
                        bar = prices(open_=89, low=88, high=160, close=100)
                    else:
                        bar = prices()
                    f = pd.DataFrame([vars(bar)], index=[d])
                    f.attrs["exchange"] = {**MARKET, "dividend": 1 if i == 2 else 0}
                    frames.update(A=f, B=f)
                    candidates = []
                    if i == 0:
                        setup = pick()
                        setup.features["atr"] = 5
                        blocked = pick("B", strategy="high_momentum")
                        blocked.features["surveillance"] = "unknown"
                        candidates = [setup, blocked]
                    process(
                        db,
                        run,
                        d,
                        {"enabled": i not in (2, 3)},
                        candidates,
                        frames,
                        cfg,
                        unconstrained=book == "equal-risk",
                    )
                books[run] = {"complete": True, "scanned": len(dates)}
    manifest = {
        "identity": "test-data",
        "windows": [
            {
                "name": "walk-1",
                "journal": "walk-1/journal.sqlite",
                "sessions": list(map(str, dates)),
                "books": books,
            }
        ],
    }
    out = tmp_path / "replay.json"
    out.write_text(json.dumps(manifest))
    return out


def test_entry_regime_and_exit_regime_loss_diagnosis(replay):
    report = analyze(replay)
    assert report["provenance"]["identity"] == "test-data"
    for revision in ("r1", "r2"):
        for book in ("funded", "equal_risk"):
            strategy = report["revisions"][revision]["books"][book]["strategies"]["trend_pullback"]
            loss = strategy["overall"]
            assert loss["plans"] == loss["bought"] == loss["closed"] == loss["mature_closed"] == 1
            assert loss["mean_net_r"] < 0
            assert loss["mean_net_r"] + loss["mean_cost_r"] == pytest.approx(loss["mean_gross_r"])
            assert loss["mean_fee_r"] + loss["mean_slippage_r"] == pytest.approx(
                loss["mean_cost_r"]
            )
            gross = (89 + 1 - 98) / (98 * 1.0005 - 90)
            assert loss["mean_gross_r"] == pytest.approx(gross)
            assert loss["exit_reasons"]["Closing stop"]["mean_net_r"] == pytest.approx(
                loss["mean_net_r"]
            )
            assert loss["losers_reached_1r"]["share"] == 1
            assert loss["losers_reached_1r"]["on_exit_session"] == 0
            assert loss["mfe_r"]["max"] == pytest.approx((109 - 98 * 1.0005) / (98 * 1.0005 - 90))
            assert loss["days_to_stop_losers"]["trigger_sessions"]["median"] == 2
            assert loss["days_to_stop_losers"]["exit_sessions"]["median"] == 3
            assert strategy["by_regime"]["off"]["closed"] == 0
            assert strategy["by_exit_regime"]["off"]["closed"] == 1
            assert strategy["corporate_actions"]["with"]["closed"] == 1
            assert strategy["corporate_actions"]["without"]["closed"] == 0
            acct = strategy["by_window"]["walk-1"]["account"]
            assert acct["net_pnl_inr"] == pytest.approx(loss["net_inr"])
            assert acct["nifty200_buy_hold_return_pct"] == pytest.approx(20)
            assert acct["regime_pnl"]["off"]["marked_net_pnl_inr"] < 0
            assert sum(
                g["marked_net_pnl_inr"] for g in acct["regime_pnl"].values()
            ) == pytest.approx(loss["net_inr"])
            if book == "equal_risk":
                assert (
                    acct["return_pct"] is None
                    and acct["max_peak_to_trough_loss_pct_of_reference"] > 0
                )
            else:
                assert acct["max_drawdown_pct"] > 0
            assert strategy["verdict"] == "not enough data"
        shadow = report["revisions"][revision]["books"]["excluded_shadow"]["strategies"][
            "high_momentum"
        ]
        assert shadow["allocation_reasons"] == {"surveillance_unknown": 1}
        assert "qualification" not in shadow


def test_missing_independent_position_mark_fails_audit(replay):
    with closing(sqlite3.connect(replay.parent / "walk-1" / "journal.sqlite")) as db, db:
        db.execute("""DELETE FROM paper_position_marks WHERE rowid IN (
               SELECT m.rowid FROM paper_position_marks m JOIN calls c ON c.id=m.call_id
               WHERE c.run='r2-equal-risk' LIMIT 1)""")
    with pytest.raises(ValueError, match="independent daily marks"):
        analyze(replay)


def test_mismatched_saved_rules_fail_metric_extraction(replay):
    with closing(sqlite3.connect(replay.parent / "walk-1" / "journal.sqlite")) as db, db:
        db.execute("UPDATE rule_configs SET config='{}' WHERE revision LIKE 'r2-%'")
    with pytest.raises(ValueError, match="saved rules"):
        analyze(replay)
