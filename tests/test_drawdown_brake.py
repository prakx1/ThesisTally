import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from test_research import CFG, DAY, calendar, pick

from thesistally import brake
from thesistally.journal import journal, process


def brake_rules():
    return {**CFG, "risk": {**CFG["risk"], "resume_regime_sessions": 10}}


def test_gate_streak_survives_restart_and_failed_final_session(tmp_path):
    path = tmp_path / "journal.sqlite"
    cfg = brake_rules()
    capital = cfg["risk"]["capital"]
    days = [DAY + timedelta(days=i) for i in range(21)]

    def scan(db, i, gate, candidates=()):
        return process(db, "pilot", days[i], {"enabled": gate}, candidates, calendar(days[i]), cfg)

    with closing(journal(path)) as db:
        # Carry a prior high-water mark into an otherwise cash-only account.
        with db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)",
                ("pilot", cfg["revision"], capital, capital / 0.89),
            )
        scan(db, 0, True)
        for i in range(1, 10):
            scan(db, i, True)
        assert db.execute("SELECT gate_passes FROM drawdown_brake").fetchone()[0] == 9

    with closing(journal(path)) as db:
        scan(db, 10, False)
        for i in range(11, 20):
            scan(db, i, True)
        assert db.execute("SELECT gate_passes FROM drawdown_brake").fetchone()[0] == 9
        with pytest.raises(sqlite3.IntegrityError):
            scan(db, 20, True, [pick(), pick()])
        assert db.execute("SELECT count(*) FROM account_events").fetchone()[0] == 1
        assert db.execute("SELECT gate_passes FROM drawdown_brake").fetchone()[0] == 9
        for _ in range(2):
            assert scan(db, 20, True, [pick()]) == 1
        assert [
            tuple(row)
            for row in db.execute(
                "SELECT day,kind,gate_passes,risk_fraction FROM account_events ORDER BY day"
            )
        ] == [
            (str(days[0]), "drawdown_pause", 0, 0.0),
            (str(days[20]), "drawdown_resume", 10, 0.0025),
        ]
        assert tuple(db.execute("SELECT allocation,size FROM calls").fetchone()) == (
            "portfolio",
            250,
        )


def test_recovery_needs_a_fresh_crossing_or_new_high(tmp_path):
    cfg = brake_rules()
    with closing(journal(tmp_path / "journal.sqlite")) as db:
        db.execute("INSERT INTO runs VALUES('pilot',?,100,100)", (cfg["revision"],))
        peak, last = 100.0, None

        def mark(equity):
            nonlocal peak, last
            day = DAY if last is None else last + timedelta(days=1)
            decision = brake.funding(
                db, "pilot", str(day), str(last) if last else None, True, equity, peak, cfg
            )
            peak, last = max(peak, equity), day
            return decision

        assert mark(94) == (False, 0.0025)
        assert mark(90) == (True, 0.0025)
        for _ in range(9):
            assert mark(89) == (True, 0.0025)
        assert mark(89) == (False, 0.0025)
        assert mark(88) == (False, 0.0025)
        assert mark(91) == (False, 0.0025)
        assert mark(90) == (True, 0.0025)  # separate downward crossing
        for _ in range(10):
            decision = mark(95)
        assert decision == (False, 0.0025)
        assert mark(100) == (False, 0.0025)  # regaining the old high is insufficient
        assert mark(100.01) == (False, 0.005)
        assert [row[0] for row in db.execute("SELECT kind FROM account_events ORDER BY day")] == [
            "drawdown_pause",
            "drawdown_resume",
            "drawdown_pause",
            "drawdown_resume",
            "full_risk_restored",
        ]


def test_r1_keeps_instantaneous_brake(tmp_path):
    cfg = brake_rules()
    del cfg["risk"]["resume_regime_sessions"]
    with closing(journal(tmp_path / "journal.sqlite")) as db:
        for day, equity, paused, fraction in [
            ("2024-01-01", 89, True, 0.0025),
            ("2024-01-02", 91, False, 0.0025),
            ("2024-01-03", 95, False, 0.005),
        ]:
            assert brake.funding(db, "r1", day, None, False, equity, 100, cfg) == (paused, fraction)
        assert not db.execute("SELECT 1 FROM account_events").fetchone()
        assert not db.execute("SELECT 1 FROM drawdown_brake").fetchone()
