from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from test_research import DAY, MARKET, REGIME, calendar, prices

from thesistally import dip_coverage, range_orders
from thesistally.journal import advance, exclusion, journal, process
from thesistally.ranking import order
from thesistally.signals import Candidate, rules


def settings(target=50):
    return rules(Path(range_orders.__file__).with_name("revisions") / f"range-c{target}.yaml")


def candidate(isin="I1", position=0.1, surveillance=False):
    return Candidate(
        "RANGE",
        isin,
        "Banks",
        "range_bouncers",
        100.0,
        95.0,
        108.0,
        {
            "position60": position,
            "low60": 98.0,
            "high60": 118.0,
            "atr14": 3.0,
            "surveillance": surveillance,
            "earnings_in_days": None,
        },
    )


def test_registered_target_and_stop_on_signal_and_fill(tmp_path):
    cfg = settings()
    with closing(journal(tmp_path / "db")) as db:
        process(db, "C", DAY, REGIME, [candidate()], calendar(DAY), cfg, unconstrained=True)
        call = lambda: db.execute("SELECT * FROM calls").fetchone()
        with db:
            # Stop equality and intraday target touch do not create exits.
            advance(db, call(), str(DAY + timedelta(days=1)), prices(101, 120, 92, 95), cfg, MARKET)
            assert (
                call()["pending_exit"] is None and call()["stop"] == 95 and call()["target"] == 108
            )
            assert call()["size"] == int(5000 / (call()["buy"] - 95))
            advance(db, call(), str(DAY + timedelta(days=2)), prices(96, 112, 90, 108), cfg, MARKET)
            assert call()["pending_exit"] == range_orders.TARGET
            advance(
                db,
                call(),
                str(DAY + timedelta(days=3)),
                prices(104, 120, 90, 114),
                cfg,
                {**MARKET, "floor": None},
            )
            assert call()["status"] == "closed"
            assert db.execute("SELECT price FROM fills WHERE side='sell'").fetchone()[
                0
            ] == pytest.approx(104 * 0.9995)


@pytest.mark.parametrize(
    "last_close,expected", [(108, "Scheduled maximum holding close"), (94, "Closing stop")]
)
def test_scheduled_twentieth_and_strict_closing_stop(tmp_path, last_close, expected):
    cfg = settings(80)
    with closing(journal(tmp_path / "db")) as db:
        process(db, "C", DAY, REGIME, [candidate()], calendar(DAY), cfg)
        with db:
            for i in range(1, 21):
                row = db.execute("SELECT * FROM calls").fetchone()
                advance(
                    db,
                    row,
                    str(DAY + timedelta(days=i)),
                    prices(100, 115, 90, last_close if i == 20 else 100),
                    cfg,
                    MARKET,
                )
        assert (
            db.execute("SELECT detail FROM events WHERE kind IN ('closed','exit_due')").fetchone()[
                0
            ]
            == expected
        )


def test_control_census_and_rank_keep_actual_stop_probe():
    cfg = settings()
    c = candidate(surveillance="unknown")
    assert dip_coverage.known_veto(c, cfg, REGIME) is None
    assert exclusion(c, cfg, REGIME) == "surveillance_unknown"
    assert dip_coverage.opening_disposition("Opening price at or below the planned stop") == (
        "rule",
        "open_at_or_below_stop",
    )
    assert [
        c.isin for c in order([candidate("3"), candidate("2", 0.09), candidate("1")], REGIME, cfg)
    ] == ["2", "1", "3"]


def test_annual_replay_compares_other_members_and_keeps_overlapping_equal_risk(
    tmp_path, monkeypatch
):
    import pandas as pd

    from thesistally import range_replay

    days = [DAY + timedelta(days=i) for i in range(8)]
    root = tmp_path / "market"
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    signal_file = tmp_path / "signals.parquet"
    rows = [
        {
            "symbol": s,
            "isin": s,
            "sector": "Banks",
            "cohort": cohort,
            "day": str(day),
            "selection_day": str(days[0]),
            "turnover": 1e8,
            "close": 100.0,
            "rsi2": 1.0,
            "position60": 0.1,
            "low60": 98.0,
            "high60": 118.0,
            "atr14": 3.0,
        }
        for day in days[:2]
        for s, cohort in (("range", "range"), ("control", "other"))
    ]
    pd.DataFrame(rows).to_parquet(signal_file, index=False)

    def sample(root, day, previous, raw, following, cfg, cash_events):
        known = {"surveillance": False, "earnings_in_days": None, **MARKET}
        price = prices(100, 125, 97, 121 if day == days[3] else 100)
        frame = pd.DataFrame([vars(price)], index=[day])
        frame.attrs["exchange"] = known
        members = {"range": "range", "control": "control"}
        frames = {**calendar(day), "NIFTY 200": frame, **dict.fromkeys(members, frame)}
        p = root / "sessions" / str(day) / "bhavcopy.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\n"
            + "".join(f"{symbol},EQ,100,125,97,{price.close}\n" for symbol in members)
        )
        return REGIME, frames, members, dict.fromkeys(members, known)

    monkeypatch.setattr(range_replay, "session", sample)
    args = (
        root,
        run_dir,
        signal_file,
        None,
        "walk-1",
        days,
        [str(DAY - timedelta(days=2)), str(DAY - timedelta(days=1))],
    )
    w = range_replay.run_window(args)
    assert all(m["complete"] for m in w["books"].values()), w
    assert range_replay.run_window(args) == w
    with closing(journal(run_dir / "walk-1/journal.sqlite")) as db:
        for v in range_replay.TARGETS:
            for book, allocation, expected in (
                ("funded", "portfolio", 1),
                ("range_equal", "unconstrained", 2),
                ("other_equal", "unconstrained", 2),
            ):
                n = db.execute(
                    "SELECT COUNT(*) FROM calls WHERE run=? AND allocation=? AND status='closed'",
                    (f"{v}-{book}", allocation),
                ).fetchone()[0]
                assert n == expected
