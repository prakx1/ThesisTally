import json
from contextlib import closing
from datetime import date, datetime

import httpx
import pandas as pd
import pytest

from thesistally import frozen_forward as f
from thesistally import frozen_inputs, market, steady_replay
from thesistally.steady_momentum import advance, open_journal


def config(monkeypatch):
    cfg = json.loads(f.REGISTRATION.read_bytes())
    monkeypatch.setattr(f, "registration", lambda: cfg)
    monkeypatch.setattr(f, "tagged", lambda _: True)
    return cfg


def test_prospective_paper_book_starts_at_cash_and_catches_up_without_double_fills(
    tmp_path, monkeypatch
):
    cfg = config(monkeypatch)
    dirpath = f.folder(tmp_path, cfg)
    first, second = date(2026, 11, 23), date(2026, 11, 25)
    now = datetime(2026, 11, 25, 17, tzinfo=market.IST)
    dates = [first, second]
    assert f.report(tmp_path)["sessions"] == 0

    def observations(_, db, cfg, day, prior, terms):
        rank = pd.DataFrame([{"isin": "A", "rank": 1}]) if day == first else None
        quotes = {
            "A": {
                "isin": "A",
                "symbol": "A",
                "open": 100.0,
                "close": 110.0,
                "volume": 100,
                "ceiling": None,
            }
        }
        advance(db, f.RUN, day, prior, rank, True, quotes, steady_replay.execution_config(), terms)

    monkeypatch.setattr(f, "observe", observations)
    monkeypatch.setattr(
        frozen_inputs,
        "session_dates",
        lambda root, prev, until: [d for d in dates if prev < d <= until],
    )
    monkeypatch.setattr(f.steady_actions, "terms", lambda *a, **kw: {})
    levels = {first: 110.0, second: 99.0}

    def tri(directory, day):
        (directory / "tri" / str(day)).mkdir(parents=True, exist_ok=True)
        return levels[day]

    monkeypatch.setattr(f, "benchmark_close", tri)
    cfg["anchor_tri"] = {"close": 100.0}
    initial = f.catch_up(tmp_path, first, now=now)
    assert initial["first"] == str(first) and initial["return_pct"] == 0
    assert initial["benchmark_return_pct"] == pytest.approx(10)
    res = f.catch_up(tmp_path, second, now=now)
    again = f.catch_up(tmp_path, second, now=now)
    assert res == again and res["sessions"] == 2
    assert res["benchmark_return_pct"] == pytest.approx(-1)
    assert res["benchmark_max_drawdown_pct"] == pytest.approx(10)
    assert res["one_way_turnover_pct"] > 0 and res["average_exposure_pct"] > 0
    with closing(open_journal(dirpath / "journal.sqlite")) as db:
        assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM events WHERE kind='band_unknown'").fetchone()[0] == 1
        )
        assert [r[0] for r in db.execute("SELECT run FROM books")] == [f.RUN]
    with pytest.raises(market.MarketError, match="completed exchange"):
        f.catch_up(tmp_path, date(2026, 11, 26), now=now)


def test_unpublished_total_return_print_retries_without_caching_empty_response(
    tmp_path, monkeypatch
):
    d = date(2026, 11, 23)
    calls = []
    actual = httpx.Client

    def source(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {"Index Name": "NIFTY 200", "Date": "23 Nov 2026", "TotalReturnsIndex": "1,000.00"}
            ],
        )

    monkeypatch.setattr(
        f.httpx, "Client", lambda **kw: actual(transport=httpx.MockTransport(source))
    )
    with pytest.raises(market.MarketError, match="Awaiting"):
        f.benchmark_close(tmp_path, d)
    assert not (tmp_path / "tri" / str(d)).exists()
    assert f.benchmark_close(tmp_path, d) == 1000.0 and f.benchmark_close(tmp_path, d) == 1000.0
    assert len(calls) == 2


def test_unverified_available_date_cannot_skip_a_missing_session(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    with closing(open_journal(tmp_path / "journal.sqlite")) as db:
        f.initialize(db, cfg)
        monkeypatch.setattr(market, "verify", lambda *a: {"prior_day": "2026-11-23"})
        with pytest.raises(market.MarketError, match="Missing official predecessor"):
            f.observe(tmp_path, db, cfg, date(2026, 11, 25), date(2026, 11, 20), {})
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        with pytest.raises(market.MarketError, match="follow the freeze"):
            f.observe(tmp_path, db, cfg, date(2026, 11, 20), date(2026, 11, 19), {})


def test_frozen_runtime_rejects_changed_engine_before_creating_account(tmp_path, monkeypatch):
    raw = json.loads(f.REGISTRATION.read_bytes())
    raw["engine_sha256"] = "wrong"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(f, "REGISTRATION", path)
    with pytest.raises(market.MarketError, match="changed"):
        f.catch_up(tmp_path)
    assert not (tmp_path / "forward").exists()


def test_forward_calendar_uses_next_nse_session_and_includes_archived_special_sessions(tmp_path):
    weekend = tmp_path / "sessions" / "2026-11-22"
    weekend.mkdir(parents=True)
    (weekend / "manifest.json").write_text("{}")
    assert list(frozen_inputs.session_dates(tmp_path, date(2026, 11, 20), date(2026, 11, 25))) == [
        date(2026, 11, 22),
        date(2026, 11, 23),
        date(2026, 11, 25),
    ]


def test_unknown_opening_master_leaves_band_unknown_and_retains_other_dated_controls():
    cols = ["Symbol", "Series", "Status", "GSM", "Long Term ASM)", "Short Term ASM)"]
    flags = (",".join(cols) + "\nA,EQ,A,100,100,100\n").encode()
    members = pd.DataFrame([{"symbol": "A", "isin": "IN123", "series": "EQ"}])
    item = f.exchange.decode(date(2026, 11, 23), members, flags, None, [], [])["A"]
    assert item["ceiling"] is None and item["floor"] is None
    assert item["surveillance"] is False


def test_renamed_and_new_isins_get_their_own_price_histories_and_split_basis():
    from thesistally import frozen_prices

    prior = date(2026, 11, 20)
    day = date(2026, 11, 23)

    def bar(name, value, d):
        return {
            "day": d,
            "symbol": name,
            "open": value,
            "close": value,
            "low": value,
            "high": value,
            "volume": 100.0,
        }

    old = pd.DataFrame(
        [
            bar("NEW", 100.0, prior),
            bar("OLD", 60.0, prior),
            bar("SUB", 100.0, prior),
            bar("NIFTY 50", 500.0, prior),
        ]
    )
    members = pd.DataFrame(
        [
            {"isin": "X", "symbol": "NEW"},
            {"isin": "Y", "symbol": "OLD"},
            {"isin": "Z", "symbol": "SUB"},
        ]
    )
    current = pd.DataFrame(
        [
            {**bar("NEW", 20.0, day), "isin": "I"},
            {**bar("RENAMED", 66.0, day), "isin": "Y"},
            {**bar("SUB", 51.0, day), "isin": "Z2"},
            {**bar("NIFTY 50", 550.0, day), "isin": "NIFTY 50"},
        ]
    )
    requests = []

    def historical(i, s, days):
        requests.append((i, s))
        return pd.DataFrame([bar(s, 22.0, prior)])

    frame, _ = frozen_prices.append_prices(
        old,
        members,
        current,
        {("Z2", "SUB"): {("Face Value Split From Rs 10 To Rs 5", "10")}},
        {},
        [],
        historical,
        {"Z2": "Z"},
    )
    prior_rows = frame[frame.day.eq(prior)].set_index("symbol")
    assert requests == [("I", "NEW")]
    assert prior_rows.loc["RENAMED", "close"] == 60.0
    assert prior_rows.loc["NEW", "close"] == 22.0
    assert prior_rows.loc["SUB", "close"] == 50.0
    assert prior_rows.loc["SUB", "volume"] == 200.0


def test_public_only_collector_matches_archived_gate_and_complete_rank_order(tmp_path, monkeypatch):
    """Rehydrate a real certified session without Kite. The full exchange archive is optional in CI."""
    import shutil
    from pathlib import Path

    from thesistally import stage3_market

    root = Path(__file__).resolve().parents[1] / "data"
    prior, day = date(2026, 11, 19), date(2026, 11, 20)
    base = root / "stage3/market"
    if not (base / "sessions" / str(prior) / "bars.parquet").exists():
        pytest.skip("Certified local NSE history is not installed")
    cfg = json.loads(f.REGISTRATION.read_bytes())
    cfg.update(
        anchor_session=str(prior),
        anchor_manifest_sha256=market.sha(
            (base / "sessions" / str(prior) / "manifest.json").read_bytes()
        ),
    )
    view = tmp_path / "market"
    for sub in ("sessions", "sources", "context"):
        (view / sub).mkdir(parents=True)
    (view / "sessions" / str(prior)).symlink_to(base / "sessions" / str(prior))
    (view / "sources/nse-daily").symlink_to(root / "sources/nse-daily")
    shutil.copytree(root / "context" / str(day), view / "context" / str(day))

    def no_kite(*args):
        raise market.MarketError("Kite session unavailable")

    monkeypatch.setattr(market.Feed, "history", no_kite)
    frozen_inputs.ensure_input(root, view, cfg, day, prior, [])
    expected, observed = [stage3_market.session(d, day, (126,)) for d in (base, view)]
    assert observed[0] == expected[0] and list(observed[1][126]["isin"]) == list(
        expected[1][126]["isin"]
    )
    actual = (
        pd.read_parquet(view / "sessions" / str(day) / "bars.parquet")
        .set_index(["day", "symbol"])
        .sort_index()
    )
    archive = (
        pd.read_parquet(base / "sessions" / str(day) / "bars.parquet")
        .set_index(["day", "symbol"])
        .sort_index()
    )
    pd.testing.assert_frame_equal(actual, archive, check_dtype=False, atol=1e-8, rtol=1e-12)
    assert (
        json.loads((view / "sessions" / str(day) / "kite-check.json").read_bytes())["status"]
        == "unavailable"
    )
