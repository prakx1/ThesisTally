from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest
from test_research import DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import paper
from thesistally.journal import (
    advance,
    advance_from_frame,
    journal,
    process,
    saved_rules,
    stop_bound_reason,
)
from thesistally.signals import rules


def opened(db, cfg, op, *, stop=94, atr=5, book="portfolio", slip_high=110):
    c = replace(pick(), stop=stop, target=100 + 2 * (100 - stop))
    c.features["atr"] = atr
    process(db, "unit", DAY, REGIME, [c], calendar(DAY), cfg)
    cid = db.execute("SELECT id FROM calls").fetchone()[0]
    if book != "portfolio":
        db.execute("UPDATE calls SET allocation=? WHERE id=?", (book, cid))
    row = db.execute("SELECT * FROM calls WHERE id=?", (cid,)).fetchone()
    advance(
        db,
        row,
        str(DAY + timedelta(days=1)),
        prices(open_=op, low=op - 0.001, high=slip_high, close=op),
        cfg,
        MARKET,
    )
    return db.execute("SELECT * FROM calls WHERE id=?", (cid,)).fetchone()


def test_r1_archive_and_saved_pending_calls_keep_the_original_opening_rule(tmp_path):
    old = rules(revision="r1")
    assert old["revision"] == "research-4-c8007c0a59b4"
    assert rules()["version"] == rules(revision="r2")["version"] == "r2"
    with closing(journal(tmp_path / "old.sqlite")) as db:
        c = pick()
        process(db, "observations", DAY, REGIME, [c], calendar(DAY), old)
        prior = db.execute("SELECT * FROM calls WHERE run='observations'").fetchone()
        assert saved_rules(db, prior["revision"]) == old
        d = DAY + timedelta(days=1)
        f = pd.DataFrame([vars(prices(open_=100, low=99.5, high=105, close=100))], index=[d])
        f.attrs["exchange"] = MARKET
        advance_from_frame(db, prior, str(d), {c.symbol: f}, rules())
        assert (
            db.execute("SELECT status FROM calls WHERE id=?", (prior["id"],)).fetchone()[0]
            == "missed"
        )


def test_gap_up_at_exact_raw_chase_cap_uses_slipped_risk_and_preserves_plan(tmp_path):
    cfg = rules()
    with closing(journal(tmp_path / "ledger.sqlite")) as db:
        c = opened(db, cfg, 102.5)
        assert c["status"] == "active"
        assert (c["entry"], c["stop"], c["planned_target"]) == (100, 94, 112)
        assert c["buy"] == pytest.approx(102.5 * 1.0005)
        assert c["target"] == pytest.approx(c["buy"] + 2 * (c["buy"] - 94))
        assert c["size"] < c["planned_size"]
        assert c["size"] * (c["buy"] - 94) <= c["planned_size"] * 6
        assert [(e[0], e[1]) for e in db.execute("SELECT side,qty FROM fills")] == [
            ("buy", c["size"])
        ]


@pytest.mark.parametrize(
    "op,stop,atr,status,reason",
    [
        (102.50001, 94, 5, "missed", "Open above signal close + 0.5 ATR(14)"),
        (100, 90, 5, "missed", "Fill stop distance out of bounds"),
        (97, 95, 5, "missed", "Fill stop distance out of bounds"),
        (100, 90, 4, "missed", "Fill stop distance out of bounds"),
        (93.99, 94, 5, "invalidated", "Opening price at or below the planned stop"),
        (94, 94, 5, "invalidated", "Opening price at or below the planned stop"),
    ],
)
def test_opening_veto_creates_no_buy_or_target_mutation(tmp_path, op, stop, atr, status, reason):
    with closing(journal(tmp_path / "db")) as db:
        c = opened(db, rules(), op, stop=stop, atr=atr)
        assert (c["status"], c["buy"], c["planned_target"]) == (status, None, c["target"])
        assert (
            db.execute("SELECT detail FROM events ORDER BY id DESC LIMIT 1").fetchone()[0] == reason
        )
        assert not db.execute("SELECT 1 FROM fills").fetchone()


@pytest.mark.parametrize("price,stop,atr", [(55, 53.35, 1), (100, 90, 4), (100, 96, 1.6)])
def test_r2_fill_and_signal_bounds_include_exact_values(price, stop, atr):
    assert stop_bound_reason(price, stop, atr, rules()) is None


def test_equal_risk_counterfactual_uses_fill_distance_without_cash_or_signal_quantity_cap(tmp_path):
    cfg = rules()
    with closing(journal(tmp_path / "db")) as db:
        c = opened(db, cfg, 98, stop=94, book="unconstrained")
        assert c["size"] == int(5000 / (c["buy"] - c["stop"])) > c["planned_size"]


def test_r2_forward_primary_and_original_shadow_store_their_own_levels(tmp_path):
    cfg = rules()
    with closing(journal(tmp_path / "db")) as db:
        c = pick()
        process(db, "observations", DAY, REGIME, [c], calendar(DAY), cfg)
        d = DAY + timedelta(days=1)
        f = pd.DataFrame([vars(prices(open_=102, high=104, low=100, close=101))], index=[d])
        f.attrs["exchange"] = MARKET
        process(db, "observations", d, REGIME, [], {**calendar(d), c.symbol: f}, cfg)
        rows = {x["run"]: x for x in db.execute("SELECT * FROM calls")}
        assert rows["observations"]["status"] == "missed"  # fill stop-distance check
        shadow = rows[paper.ENTRY_SHADOW]
        assert shadow["status"] == "active" and shadow["planned_target"] == 120
        assert shadow["target"] == pytest.approx(shadow["buy"] + 2 * (shadow["buy"] - 90))


def test_old_journal_migration_preserves_signal_levels_and_append_only_events(tmp_path):
    ledger = tmp_path / "legacy.sqlite"
    cfg = rules(revision="r1")
    with closing(journal(ledger)) as db:
        process(db, "observations", DAY, REGIME, [pick()], calendar(DAY), cfg)
        d = DAY + timedelta(days=1)
        frame = pd.DataFrame([vars(prices(open_=101, high=105, low=99, close=102))], index=[d])
        frame.attrs["exchange"] = MARKET
        process(db, "observations", d, REGIME, [], {**calendar(d), "A": frame}, cfg)
        with db:
            for name in ("planned_target", "planned_size"):
                db.execute(f"ALTER TABLE calls DROP COLUMN {name}")
        before = [tuple(x) for x in db.execute("SELECT * FROM events ORDER BY id")]
    for _ in range(2):
        with closing(journal(ledger)) as db:
            rows = {r["run"]: r for r in db.execute("SELECT * FROM calls")}
            assert rows[paper.ENTRY_SHADOW]["target"] > 120
            assert (
                rows[paper.ENTRY_SHADOW]["planned_target"]
                == rows["observations"]["planned_target"]
                == 120
            )
            assert all(r["planned_size"] == r["size"] for r in rows.values())
            assert [tuple(x) for x in db.execute("SELECT * FROM events ORDER BY id")] == before
