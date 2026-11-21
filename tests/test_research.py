from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from typer.testing import CliRunner

from thesistally.cli import app
from thesistally.journal import advance, journal, metrics, process
from thesistally.market import MarketError, check_bars, membership, reconcile, save_members
from thesistally.signals import Candidate, rules, setups

DAY = date(2026, 10, 1)
MARKET = {"other_actions": [], "dividend": 0.0, "ceiling": 200, "floor": 50}
REGIME = {"enabled": True}
CFG = rules()


def pick(symbol="A", sector="Banks", strategy="trend_pullback"):
    return Candidate(symbol, symbol, sector, strategy, 100.0, 90.0, 120.0, {"rs_percentile": 1.0, "atr": 4.0})


def prices(open_=99, high=104, low=97, close=100):
    return SimpleNamespace(
        open=open_, high=high, low=low, close=close, volume=1000, fast=95, trail=94
    )


def calendar(d):
    return {"NIFTY 50": pd.DataFrame(index=[d - timedelta(days=1), d])}


def test_no_lookahead_membership_or_silent_collision(tmp_path):
    members = "Symbol,Industry,Series,ISIN Code\n" + "".join(
        f"STK{i},Banks,EQ,IN{i:010d}\n" for i in range(200)
    )
    save_members(tmp_path, members.encode(), DAY, DAY, "dated fixture")
    assert len(membership(tmp_path, DAY)[0]) == 200
    with pytest.raises(MarketError, match="No dated"):
        membership(tmp_path, DAY - timedelta(days=1))
    with pytest.raises(MarketError, match="conflict"):
        save_members(tmp_path, members.replace("STK99,", "CHANGE,").encode(), DAY, DAY, "other")


def test_bad_market_data_fails_closed():
    frame = pd.DataFrame(
        [[str(DAY), 100, 99, 90, 98, 100]],
        columns=["day", "open", "high", "low", "close", "volume"],
    )
    with pytest.raises(MarketError, match="Invalid OHLCV"):
        check_bars(frame, "BAD", DAY, DAY)
    ref = pd.DataFrame({"ClsPric": ["100"]}, index=["A"])
    with pytest.raises(MarketError, match="A"):
        reconcile(pd.Series({"A": 99.99}), ref)


def test_calendar_gap_and_scan_retry_are_atomic(tmp_path):
    db = journal(tmp_path / "journal.db")
    for _ in range(2):
        assert process(db, "research", DAY, REGIME, [pick()], calendar(DAY), CFG) == 1
    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    with pytest.raises(MarketError, match="consecutive"):
        process(
            db,
            "research",
            DAY + timedelta(days=3),
            REGIME,
            [],
            calendar(DAY + timedelta(days=3)),
            CFG,
        )
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1


def test_sector_limit_and_surveillance_shadow_tracking(tmp_path):
    db = journal(tmp_path / "journal.db")
    candidates = [pick(x) for x in "ABCD"]
    candidates[-1].features["surveillance"] = True
    process(db, "risk", DAY, REGIME, candidates, calendar(DAY), CFG)
    assert dict(db.execute("SELECT allocation,count(*) FROM calls GROUP BY allocation")) == {
        "portfolio": 2,
        "position_limit": 1,
        "surveillance": 1,
    }


def test_next_open_gap_partial_exit_costs_and_dividend(tmp_path):
    db = journal(tmp_path / "journal.db")
    process(db, "test", DAY, REGIME, [pick()], calendar(DAY), CFG)
    row = lambda: db.execute("SELECT * FROM calls").fetchone()
    with db:
        advance(db, row(), "2026-10-05", prices(high=125), CFG, MARKET)
        assert row()["partial"] == 1 and row()["age"] == 1
        advance(db, row(), "2026-10-06", prices(low=89, close=93), CFG, {**MARKET, "dividend": 2})
        assert row()["pending_exit"] == "Closing stop"
        advance(db, row(), "2026-10-07", prices(open_=50), CFG, MARKET)
        assert row()["status"] == "active"
        advance(db, row(), "2026-10-08", prices(open_=85), CFG, MARKET)
    fills = db.execute("SELECT * FROM fills ORDER BY id").fetchall()
    assert [f["side"] for f in fills] == ["buy", "sell", "sell"]
    assert fills[-1]["price"] < 85
    entitled = db.execute("SELECT amount FROM entitlements").fetchone()[0]
    assert entitled == 2 * fills[-1]["qty"]
    pnl = sum((1 if f["side"] == "sell" else -1) * f["qty"] * f["price"] - f["fees"] for f in fills)
    assert row()["net"] == pytest.approx(pnl + entitled)
    assert row()["status"] == "closed"


@pytest.mark.parametrize(
    ("open_", "status"), [(89, "invalidated"), (110, "missed"), (200, "missed")]
)
def test_gap_or_limit_never_invents_a_fill(tmp_path, open_, status):
    db = journal(tmp_path / "journal.db")
    process(db, "test", DAY, REGIME, [pick()], calendar(DAY), CFG)
    with db:
        advance(
            db,
            db.execute("SELECT * FROM calls").fetchone(),
            str(DAY),
            prices(open_=open_),
            CFG,
            MARKET,
        )
    assert db.execute("SELECT status FROM calls").fetchone()[0] == status
    assert not db.execute("SELECT 1 FROM fills").fetchone()


def test_cost_weighted_profit_factor():
    rows = [
        {"status": "closed", "outcome_r": 1, "net": 1000},
        {"status": "closed", "outcome_r": -1, "net": -800},
    ]
    assert metrics(rows)["profit_factor"] == 1.25
    assert metrics(rows)["expectancy_r"] == 0


def test_each_detector_reacts_to_its_completed_bar():
    f = pd.DataFrame(
        {
            "open": 99.0,
            "high": 100.0,
            "low": 97.0,
            "close": 99.0,
            "fast": 98.0,
            "medium": 95.0,
            "slow": 90.0,
            "atr": 2.0,
            "volume": 100.0,
        },
        index=range(253),
    )
    f.loc[252, ["high", "low", "close", "volume"]] = [102, 98, 101, 200]
    names = {name for name, _ in setups(f, 0.9, CFG)}
    assert names == {"trend_pullback", "high_momentum"}
    assert "high_momentum" not in {name for name, _ in setups(f, 0.5, CFG)}
    f.loc[242:246, ["low", "volume"]] = [93, 110]
    f.loc[247:251, ["low", "volume"]] = [97, 80]
    assert "vcp_breakout" in {name for name, _ in setups(f, 0.9, CFG)}
    f.loc[252, "volume"] = 90
    assert "vcp_breakout" not in {name for name, _ in setups(f, 0.9, CFG)}


def test_cli_empty_store(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    result = CliRunner().invoke(app, ["scan"])
    assert result.exit_code == 1
    assert "No market sessions" in result.output


def test_earnings_cutoff_and_exchange_actions():
    import gzip

    from thesistally.exchange import decode

    m = pd.DataFrame([{"symbol": "A", "isin": "INE123", "series": "EQ"}])
    flags = (
        b"Symbol,Series,Status,GSM,Long (Long Term ASM),Short (Short Term ASM)\nA,EQ,A,100,2,100\n"
    )
    master = gzip.compress(b"TckrSymb,SctySrs,ISIN,PricRg\nA,EQ,INE123,85.0-115.0\n")
    notices = [
        {
            "bm_desc": "Approval of quarterly results",
            "bm_purpose": "Meeting",
            "bm_timestamp": "01-Oct-2026 15:46:00",
            "bm_date": "02-Oct-2026",
            "bm_symbol": "A",
        },
        {
            "bm_desc": "Financial results",
            "bm_purpose": "Meeting",
            "bm_timestamp": "30-Sep-2026 18:00:00",
            "bm_date": "05-Oct-2026",
            "bm_symbol": "A",
        },
    ]
    actions = [
        {
            "series": "EQ",
            "exDate": "01-Oct-2026",
            "isin": "INE123",
            "subject": "Interim Dividend - Rs 2.50 per Share",
        }
    ]
    ctx = decode(DAY, m, flags, master, notices, actions)["A"]
    assert ctx == {
        "surveillance": True,
        "earnings_in_days": 4,
        "floor": 85.0,
        "ceiling": 115.0,
        "dividend": 2.50,
        "other_actions": [],
    }


def test_cli_taken_skip_is_append_only_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_DATA", str(tmp_path))
    db = journal(tmp_path / "journal.sqlite")
    process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), CFG)
    runner = CliRunner()
    for action in ("taken", "skip", "skip"):
        assert runner.invoke(app, ["action", "1", action]).exit_code == 0
    record = db.execute("SELECT action,status FROM calls").fetchone()
    assert tuple(record) == ("skip", "confirmed")
    assert [
        r[0] for r in db.execute("SELECT detail FROM events WHERE kind='action' ORDER BY id")
    ] == ["taken", "skip"]
