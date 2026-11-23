import json
from contextlib import closing

import pytest
from test_quarantine_timing import BAD_CLOSE, get_call
from test_research import CFG, DAY, MARKET, REGIME, calendar, pick, prices

from thesistally import conventions
from thesistally.convention_market import rights_terms
from thesistally.journal import advance, journal, process

NEW = {**CFG, "execution_convention": conventions.VERSION}


def test_indhotel_rights_leave_holder_with_original_shares_and_no_cash(tmp_path):
    legs = rights_terms(" Rights 1:9 @ Premium Rs 149/-", "1")
    assert legs == [{"new": 1, "old": 9, "price": 150.0}]
    prior = 215.30
    factor = (prior * 9 + 150) / (10 * prior)
    receipt = {
        "isin": "INE053A01029",
        "day": "2021-11-11",
        "factor": factor,
        "terms": legs,
        "subjects": [" Rights 1:9 @ Premium Rs 149/-"],
        "source_url": "https://nsearchives.nseindia.com/corporate/INDHOTEL_09112021210452_RIGHTSISSUE.pdf",
    }
    with closing(journal(tmp_path / "rights.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick("INDHOTEL")], calendar(DAY), NEW)
        advance(db, get_call(db), "2021-11-10", prices(), NEW, MARKET)
        before = dict(get_call(db))
        ctx = conventions.apply(
            {**MARKET, "other_actions": receipt["subjects"]}, {"rights_issue": receipt}
        )
        advance(db, get_call(db), "2021-11-11", prices(), NEW, ctx)
        after = dict(get_call(db))
        assert (after["buy"], after["size"], after["remaining"], after["stop"]) == (
            before["buy"],
            before["size"],
            before["remaining"],
            before["stop"],
        )
        assert after["trail"] == pytest.approx(before["trail"] * factor)
        assert after["target"] == pytest.approx(before["target"] * factor)
        assert after["status"] == "active"
        assert not db.execute("SELECT * FROM cash_receipts").fetchall()
        detail = json.loads(
            db.execute("SELECT detail FROM events WHERE kind='corporate_action'").fetchone()[0]
        )
        assert detail["entitlement_cash"] == 0 and detail["subscribed"] is False
        assert detail["source_url"] == receipt["source_url"]


def test_unknown_exit_floor_fills_and_flags_open_but_real_lower_lock_stays(tmp_path):
    with closing(journal(tmp_path / "bands.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick()], calendar(DAY), NEW)
        advance(db, get_call(db), "2021-07-21", prices(low=81, close=86), NEW, MARKET)
        advance(db, get_call(db), "2021-07-22", prices(open_=82), NEW, {**MARKET, "floor": 82})
        assert get_call(db)["status"] == "active"
        advance(db, get_call(db), "2021-07-23", prices(open_=83), NEW, {**MARKET, "floor": None})
        assert get_call(db)["status"] == "closed"
        sale = db.execute("SELECT price FROM fills WHERE side='sell'").fetchone()[0]
        assert sale == pytest.approx(83 * (1 - NEW["cost"]["slippage_bps"] / 10000))
        assert (
            json.loads(
                db.execute(
                    "SELECT detail FROM events WHERE kind='exit_unknown_price_band'"
                ).fetchone()[0]
            )["fill"]
            == sale
        )
    assert conventions.opening_sale(80, 0, None, 5) == (None, False)
    assert conventions.opening_purchase(80, 2000, None, 5) is None


def test_adaniports_genuine_close_releases_only_the_jump(tmp_path):
    receipt = {
        "isin": "INE742F01042",
        "day": "2024-06-04",
        "close": 1248.95,
        "prior_close": 1583.95,
        "opening_band": {"floor": 1425.55, "ceiling": 1742.30},
        "eod_master": {"floor": 1124.05, "ceiling": 1373.8},
    }
    ctx = conventions.apply(dict(BAD_CLOSE), {"quarantine_release": receipt})
    assert not ctx["quarantined"]
    assert conventions.apply(
        {**BAD_CLOSE, "quarantine_reason": "invalid_candle"}, {"quarantine_release": receipt}
    )["quarantined"]
    with closing(journal(tmp_path / "release.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick("ADANIPORTS")], calendar(DAY), NEW)
        advance(db, get_call(db), "2024-06-03", prices(), NEW, MARKET)
        advance(db, get_call(db), "2024-06-04", prices(low=83, close=85), NEW, ctx)
        assert get_call(db)["pending_exit"] == "Closing stop"
        assert (
            json.loads(
                db.execute("SELECT detail FROM events WHERE kind='quarantine_release'").fetchone()[
                    0
                ]
            )
            == receipt
        )


def test_published_adaniports_quotes_and_band_cannot_release_an_unverified_move(tmp_path):
    from datetime import date
    from pathlib import Path
    from types import SimpleNamespace

    from thesistally.convention_market import verified_close

    artifact = Path(__file__).parents[1] / "data/stage3/market/sources/stage3-events.json"
    event = json.loads(artifact.read_bytes())["days"]["2024-06-04"]["INE742F01042"][
        "quarantine_release"
    ]
    op, hi, lo, close = event["ohlc"]
    row = SimpleNamespace(
        OpnPric=op,
        HghPric=hi,
        LwPric=lo,
        ClsPric=close,
        PrvsClsgPric=1583.95,
        TtlTradgVol=1,
        TckrSymb="ADANIPORTS",
    )
    label = tmp_path / "sec_list_03062024.csv"
    label.write_text("Symbol,Series,Band\nADANIPORTS,EQ,No Band\n")
    args = (
        event,
        date(2024, 6, 3),
        row,
        event["source"],
        event["opening_band"],
        event["eod_master"],
        tmp_path,
    )
    assert verified_close(*args)["method"] == event["method"]
    label.write_text("Symbol,Series,Band\nADANIPORTS,EQ,10\n")
    assert verified_close(*args) is None
    label.write_text("Symbol,Series,Band\nADANIPORTS,EQ,No Band\n")
    assert verified_close(*args[:5], {"floor": 500, "ceiling": 1500}, tmp_path) is None
    row.PrvsClsgPric = 1584
    assert verified_close(*args) is None


def test_forward_closes_old_revision_at_unknown_floor_using_dated_override(tmp_path):
    from datetime import date

    from thesistally.journal import advance_from_frame

    with closing(journal(tmp_path / "forward.sqlite")) as db, db:
        process(db, "study", DAY, REGIME, [pick()], calendar(DAY), CFG)
        advance(db, get_call(db), "2026-11-20", prices(low=81, close=86), CFG, MARKET)
        assert get_call(db)["pending_exit"]
        day = date(2026, 11, 23)
        frames = calendar(day)
        call = get_call(db)
        import pandas as pd

        frames[call["symbol"]] = pd.DataFrame([vars(prices(open_=83))], index=[day])
        frames[call["symbol"]].attrs["exchange"] = {**MARKET, "floor": None}
        advance_from_frame(
            db,
            call,
            str(day),
            frames,
            {
                **CFG,
                "revision": "forward-new-revision",
                "forward_execution_convention": conventions.VERSION,
            },
        )
        assert get_call(db)["status"] == "closed"
        assert db.execute("SELECT 1 FROM events WHERE kind='exit_unknown_price_band'").fetchone()


def test_forward_archived_rights_and_missing_jump_evidence(tmp_path):
    from datetime import date

    import pandas as pd

    from thesistally import forward_conventions, market

    day = date(2026, 11, 23)
    folder = tmp_path / "sessions" / str(day)
    context = tmp_path / "context" / str(day)
    folder.mkdir(parents=True)
    context.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"prior_day": "2026-11-20"}))
    notices = [
        {
            "isin": "RIGHT",
            "series": "EQ",
            "exDate": "23-Nov-2026",
            "subject": "Rights 1:9 at Rs. 150",
            "faceVal": "1",
        }
    ]
    raw = json.dumps(notices).encode()
    (context / "actions.json").write_bytes(raw)
    (context / "context.json").write_text(
        json.dumps(
            {
                "sources": {
                    "actions.json": {
                        "url": "https://www.nseindia.in/api/corporates-corporateActions",
                        "sha256": market.sha(raw),
                    }
                }
            }
        )
    )
    columns = "ISIN,TckrSymb,SctySrs,ClsPric\n"
    (folder / "bhavcopy.csv").write_text(columns + "RIGHT,R,EQ,210\nFLAG,F,EQ,155\n")
    (folder / "prior-bhavcopy.csv").write_text(columns + "RIGHT,R,EQ,215.3\nFLAG,F,EQ,100\n")
    members = pd.DataFrame({"isin": ["RIGHT"]})
    forward_conventions.prepare(tmp_path, day, members, [{"isin": "FLAG"}])
    events = conventions.dated(tmp_path, day)
    assert events["RIGHT"]["rights_issue"]["terp"] == pytest.approx(208.77)
    assert conventions.apply({}, events["FLAG"])["quarantine_reason"] == "unexplained_close_move"
    forward_conventions.prepare(tmp_path, day, members, [])
    assert conventions.dated(tmp_path, day) == events


def test_ledger_audits_pending_and_held_rights_with_distinct_price_bases(tmp_path):
    from datetime import date

    from thesistally import pilot
    from thesistally.signals import rules

    cfg = {**rules(revision="r2"), "execution_convention": conventions.VERSION}
    first, second, third = [date(2026, 10, d) for d in (1, 2, 3)]

    def rights(day, factor):
        return {
            "isin": "A",
            "day": str(day),
            "factor": factor,
            "subjects": ["Rights 1:10 at Rs. 50"],
            "source_url": "https://www.nseindia.in/api/corporates-corporateActions",
            "terms": [{"new": 1, "old": 10, "price": 50}],
        }

    pending, active = rights(second, 0.97), rights(third, 0.98)
    registry = tmp_path / "sources/stage3-events.json"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps({"days": {r["day"]: {"A": {"rights_issue": r}} for r in [pending, active]}})
    )
    with closing(journal(tmp_path / "rights.sqlite")) as db, db:
        process(db, "study", first, REGIME, [pick()], calendar(first), cfg, unconstrained=True)
        for day, terms in [(second, pending), (third, active)]:
            bar = prices(96, 100, 94, 98)
            advance(db, get_call(db), str(day), bar, cfg, {**MARKET, "rights_issue": terms})
            tape = tmp_path / "sessions" / str(day) / "bhavcopy.csv"
            tape.parent.mkdir(parents=True)
            tape.write_text("ISIN,SctySrs,OpnPric,HghPric,LwPric,ClsPric\nA,EQ,96,100,94,98\n")
        audit = pilot.audit_ledger(db, "study", [first, second, third], cfg, tmp_path)
        assert audit["discrepancies"] == 0, audit["examples"]
        stages = [
            json.loads(r[0])["stage"]
            for r in db.execute(
                "SELECT detail FROM events WHERE kind='corporate_action' ORDER BY id"
            )
        ]
        assert stages == ["pending_entry_rebased", "rights_lapsed"]
