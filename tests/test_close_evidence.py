import json
from pathlib import Path

import pytest

from thesistally import close_evidence as evidence
from thesistally.market import MarketError


def test_two_day_matching_uses_independently_documented_units_and_no_price_tolerance():
    assert evidence.exact_pair([2951.9, 2266.15], [295.19, 226.615], 10)
    assert not evidence.exact_pair([2951.9, 2266.15], [295.0, 226.5], 10)
    assert not evidence.exact_pair([2951.9, 2266.15], [295.19, 226.615])
    assert not evidence.exact_pair([2951.9, 2266.15], [295.195, 226.615], 10)
    assert not evidence.exact_pair([], [])


def test_conflicting_evidence_cannot_release_or_overwrite(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    original = {"cases": [{"isin": "example", "verified": False}]}
    path.write_text(json.dumps(original))
    monkeypatch.setattr(evidence, "assemble", lambda root: original)
    assert evidence.certify(tmp_path, path) == original
    path.write_text(json.dumps({"cases": [{"isin": "example", "verified": True}]}))
    with pytest.raises(MarketError, match="differs"):
        evidence.certify(tmp_path, path)


def test_units_cannot_bridge_the_intervening_split_itself(tmp_path):
    with pytest.raises(MarketError, match="crosses"):
        evidence.units(tmp_path, "BAJFINANCE", ["2025-06-13", "2025-06-16"])


def test_both_nse_finals_all_broker_observations_and_notice_receipts():
    root = Path("data")
    if not (root / evidence.KITE_ARCHIVE).exists():
        pytest.skip("Full raw historical archive is not materialized")
    certified = evidence.certify(root)["cases"]
    assert len(certified) == 5
    for case in certified:
        assert case["verified"] and not case["corporate_action_register"]["matching_ex_date_events"]
        assert case["independent_checks"]["Groww NSE cash chart"][
            "both_ohlc_exact_in_original_units"
        ]
    assert (
        sum(
            c["independent_checks"]["Zerodha Kite day archive"][
                "both_closes_exact_in_original_units"
            ]
            for c in certified
        )
        == 3
    )


def test_supplement_requires_actual_held_shares_and_preserves_parent(tmp_path):
    home = tmp_path / evidence.REVIEW
    home.mkdir(parents=True)
    previous = {"cases": []}
    raw = json.dumps(previous).encode()
    (home / "old.json").write_bytes(raw)
    state = json.dumps({"positions": {}})
    scope = {
        "previous_evidence": "old.json",
        "previous_sha256": evidence.market.sha(raw),
        "cases": [
            {
                "symbol": "AAA",
                "isin": "X",
                "day": "2020-01-02",
                "prior_day": "2020-01-01",
                "prior_close": 100,
                "last_account_state_json": state,
                "account_state_sha256": evidence.market.sha(state.encode()),
            }
        ],
    }
    (home / "scope.json").write_text(json.dumps(scope))
    with pytest.raises(MarketError, match="held stock"):
        evidence.with_held_cases(tmp_path, previous, "scope.json")
    (home / "old.json").write_text(json.dumps({"changed": True}))
    with pytest.raises(MarketError, match="Prior signed"):
        evidence.with_held_cases(tmp_path, previous, "scope.json")


def test_newly_encountered_march_prices_have_independent_cash_quotes():
    root = Path("data")
    if not (root / evidence.KITE_ARCHIVE).exists():
        pytest.skip("Full raw historical archive is not materialized")
    original = evidence.certify(root)
    extended = evidence.certify(root, root / evidence.REVIEW / "evidence-held-pair.json")
    assert extended["cases"][:5] == original["cases"]
    for row in extended["cases"][5:]:
        assert (
            row["symbol"] == "ICICIPRULI" and row["verified"] and row["discovery"]["shares"] == 136
        )
        assert set(row["matching_price_sources"]) == {
            "NSE equity final",
            "Zerodha Kite day archive",
            "Upstox NSE cash history",
        }
        assert len(row["independent_checks"]["Groww NSE cash chart"]["missing_dates"]) == 2
