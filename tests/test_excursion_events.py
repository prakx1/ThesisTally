import json

import pytest
from test_research import DAY, MARKET, REGIME, calendar, pick, prices

from thesistally.journal import advance, journal, process
from thesistally.signals import rules


@pytest.mark.parametrize("exit_open", [103, 130])
def test_single_share_exit_excursions_end_at_the_open_or_target(tmp_path, exit_open):
    db = journal(tmp_path / "journal.sqlite")
    cfg = rules(revision="r1")
    process(db, "r1", DAY, REGIME, [pick()], calendar(DAY), cfg)
    db.execute("UPDATE calls SET size=1,planned_size=1")
    get = lambda: db.execute("SELECT * FROM calls").fetchone()
    with db:
        advance(db, get(), "2026-10-05", prices(open_=98, high=100, low=97), cfg, MARKET)
        entry = get()
        advance(
            db,
            entry,
            "2026-10-06",
            prices(open_=exit_open, high=160, low=92, close=100),
            cfg,
            MARKET,
        )
    final = get()
    risk = entry["buy"] - entry["stop"]
    exit_quote = max(exit_open, entry["target"])
    assert final["mfe"] == pytest.approx((exit_quote - entry["buy"]) / risk)
    assert final["status"] == "closed"
    first = db.execute("SELECT day FROM events WHERE kind='first_1r'").fetchall()
    assert [row[0] for row in first] == ["2026-10-06"]
    bounds = json.loads(
        db.execute("SELECT detail FROM events WHERE kind='target_exit_excursion'").fetchone()[0]
    )
    assert bounds["mae_upper_r"] == entry["mae"]
    assert bounds["mae_lower_r"] == min(
        entry["mae"], ((92 if exit_open == 103 else exit_open) - entry["buy"]) / risk
    )
    assert final["mae"] == bounds["mae_lower_r"]


def test_after_open_sale_cannot_generate_a_favourable_excursion(tmp_path):
    db = journal(tmp_path / "journal.sqlite")
    cfg = rules(revision="r1")
    process(db, "r1", DAY, REGIME, [pick()], calendar(DAY), cfg)
    get = lambda: db.execute("SELECT * FROM calls").fetchone()
    with db:
        advance(db, get(), "2026-10-05", prices(open_=98, high=99, low=88, close=89), cfg, MARKET)
        assert get()["pending_exit"] == "Closing stop"
        advance(db, get(), "2026-10-06", prices(open_=89, high=180, low=40), cfg, MARKET)
    assert get()["status"] == "closed" and get()["mfe"] < 1
    assert db.execute("SELECT COUNT(*) FROM events WHERE kind='first_1r'").fetchone()[0] == 0
