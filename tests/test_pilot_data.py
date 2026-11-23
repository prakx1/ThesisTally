from datetime import date

import pandas as pd
import pytest

from thesistally import market, pilot_data


def test_large_move_requires_dated_action_or_quarantine():
    monday, tuesday = date(2025, 1, 6), date(2025, 1, 7)
    tape = pd.DataFrame(
        [
            (monday, "X", "XN1", 100.0, False),
            (tuesday, "X", "XN1", 75.0, False),
        ],
        columns=["day", "symbol", "isin", "close", "unexplained_jump"],
    )
    with pytest.raises(market.MarketError, match="Unquarantined raw move"):
        pilot_data.audit_moves(tape, {}, {}, monday)
    documented = {(tuesday, "X"): [{"subject": "Demerger"}]}
    assert pilot_data.audit_moves(tape, documented, {}, monday)["moves"][0]["return"] == -0.25
    tape.loc[1, "unexplained_jump"] = True
    quarantine = {(str(tuesday), "XN1"): "unexplained_move_over_20pct"}
    assert len(pilot_data.audit_moves(tape, {}, quarantine, monday)["moves"]) == 1


def test_snapshot_hash_changes_if_runtime_quarantine_changes(tmp_path):
    ledger = tmp_path / "sources" / "adjusted" / "quarantine.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("[]")
    initial = pilot_data.snapshot_hash(tmp_path, [])
    ledger.write_text('[{"isin":"X","day":"2025-01-07"}]')
    assert initial != pilot_data.snapshot_hash(tmp_path, [])


def test_publishing_corrected_intervals_retains_archive_and_is_idempotent(tmp_path):
    root = tmp_path / "data"
    view = root / "pilot" / "market"
    universe = root / "universes"
    universe.mkdir(parents=True)
    (view / "universes").mkdir(parents=True)
    header = "symbol,isin,from_date,to_date,source_url\n"
    canonical = universe / "nifty200_membership.csv"
    canonical.write_text(header + "X,XN1,2023-12-01,2024-01-04,old\n")
    original = canonical.read_bytes()
    corrected = view / "universes" / canonical.name
    corrected.write_text(header + "X,XN1,2024-01-01,2024-01-03,dated\n")
    start, end = date(2024, 1, 1), date(2024, 1, 3)
    first = pilot_data.publish_canonical(root, view, start, end)
    assert (root / first["source_archive"]).read_bytes() == original
    assert pd.read_csv(canonical).source_url.tolist() == ["old", "dated", "old"]
    assert pilot_data.publish_canonical(root, view, start, end) == first
    corrected.write_text(header + "X,XN1,2024-01-02,2024-01-03,dated\n")
    with pytest.raises(market.MarketError, match="change membership"):
        pilot_data.publish_canonical(root, view, start, end)


def test_exact_limit_down_is_not_over_twenty_percent():
    from thesistally.historical_sessions import large_moves

    prices = pd.Series([40.04, 40.03, 60.06, 60.07])
    previous = pd.Series([50.05] * len(prices))
    assert large_moves(prices, previous).tolist() == [False, True, False, True]


def test_late_bhavcopy_identifier_preserves_ex_date_adjustment_without_anticipation():
    from thesistally.historical_sessions import bridge_delayed_split_identifiers

    old, new = "INE171Z01018", "INE171Z01026"
    panel = pd.DataFrame(
        [
            (date(2024, 5, 23), old, old, 2810.55, 1.0, 1.0),
            (date(2024, 5, 24), old, old, 1523.05, 0.5, 2.0),
            (date(2024, 5, 27), new, new, 1544.85, 1.0, 1.0),
        ],
        columns=["day", "isin", "chain", "close", "price_factor", "share_factor"],
    ).assign(symbol="BDL")
    panel["adjusted_close"] = panel.close / panel.price_factor
    early = panel.iloc[:2].copy()
    bridge_delayed_split_identifiers(early)
    bridge_delayed_split_identifiers(panel)
    pd.testing.assert_frame_equal(early, panel.iloc[:2])
    assert panel.chain.tolist() == [old, old, old]
    assert panel["isin"].tolist() == [old, old, new]
    assert list(panel.adjusted_close * panel.price_factor.iloc[-1]) == [
        2810.55 / 2,
        1523.05,
        1544.85,
    ]
    corrected = panel.copy()
    bridge_delayed_split_identifiers(panel)
    pd.testing.assert_frame_equal(corrected, panel)
