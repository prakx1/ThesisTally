import json

import pytest

from thesistally import market, range_cash


def test_verified_dividend_typo_and_share_identity(tmp_path):
    subject = "Interim Dividned - Rs 135 Per Share"
    row = {
        "day": "2020-10-29",
        "isin": "INE239A01016",
        "subjects": [subject.lower()],
        "cash_per_share": 135,
    }
    path = tmp_path / "cash-events.json"
    path.write_text(json.dumps({"events": [row]}))
    events = range_cash.load_events(path, market.sha(path.read_bytes()))
    ctx = {
        "dividend": 0,
        "other_actions": [subject],
        "quarantined": True,
        "quarantine_reason": "unresolved_action",
    }
    result = range_cash.reconcile(ctx, row["day"], row["isin"], events)
    assert result == {"dividend": 135, "other_actions": [], "quarantined": False}
    assert range_cash.reconcile(result, row["day"], row["isin"], events) == result
    assert range_cash.reconcile(ctx, "2020-10-28", row["isin"], events) == ctx
    assert range_cash.reconcile(ctx, row["day"], "OTHER", events) == ctx
    with pytest.raises(market.MarketError):
        range_cash.load_events(path, "wrong")


def test_cash_reconciliation_preserves_other_actions_and_unknowns():
    ctx = {
        "dividend": 4,
        "other_actions": ["merger", "int div"],
        "quarantined": True,
        "quarantine_reason": "unexplained_close_move",
    }
    row = {"subjects": ["int div"], "cash_per_share": None}
    result = range_cash.reconcile(ctx, "D", "I", {("D", "I"): row})
    assert result["dividend"] == 0
    assert result["other_actions"] == ["merger", "Unverified ex-date cash per share for I on D"]
    assert result["quarantined"] and result["quarantine_reason"] == "unexplained_close_move"
    row["cash_per_share"] = 10
    result = range_cash.reconcile(ctx, "D", "I", {("D", "I"): row})
    assert result["dividend"] == 10 and result["other_actions"] == ["merger"]
    assert result["quarantined"]
