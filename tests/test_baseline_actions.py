from datetime import date, timedelta

import pandas as pd
import pytest

from thesistally.baseline_actions import apply_to_following, restore_action_aliases
from thesistally.historical_sessions import FIELDS


def test_dated_ticker_cash_and_bonus_reconciliation(tmp_path):
    root = tmp_path
    view = root / "baseline" / "market"
    (view / "sources/adjusted").mkdir(parents=True)
    first = date(2014, 1, 30)
    bonus = first + timedelta(days=1)
    rows = []
    for n, price in enumerate([300, 200, 205]):
        day = first + timedelta(days=n)
        row = {
            "day": day,
            "isin": "I1",
            "symbol": "OLD",
            "chain": "I1",
            "prior_close": [290, 300, 200][n],
            "price_factor": 1.0,
            "share_factor": 1.0,
            "unexplained_jump": n == 1,
        }
        for field in FIELDS:
            row[field] = row["adjusted_" + field] = 2000.0 if field == "volume" else float(price)
        rows.append(row)
    tape = pd.DataFrame(rows)
    cash = {"isin": "I1", "symbol": "NEW", "subject": "Third Interim Dividend - Rs 7/- Per Share"}
    action = {"isin": "I1", "symbol": "NEW", "subject": "Bonus 1:2"}
    unrelated = {"isin": "I2", "symbol": "NEW", "subject": "Dividend - Rs 44/- Per Share"}
    actions = {(first, "NEW"): [cash, cash, unrelated], (bonus, "NEW"): [action]}
    ledger = [{"day": str(bonus), "isin": "I1", "reason": "unexplained_move_over_20pct"}]
    proof, cleared = restore_action_aliases(root, tape, view, ledger, actions)
    assert proof["traded_security_dates"] == 2
    assert cleared == {(str(bonus), "I1")}
    assert not ledger
    assert list(tape.price_factor) == pytest.approx([1, 2 / 3, 2 / 3])
    assert list(tape.share_factor) == pytest.approx([1, 1.5, 1.5])
    assert tape.adjusted_close.iloc[0] * tape.price_factor.iloc[1] == pytest.approx(200)
    member = {"symbol": "OLD", "isin": "I1"}
    assert apply_to_following(view, first, member, {"dividend": 7})["dividend"] == 7
    assert apply_to_following(view, bonus, member, {})["dividend"] == 0
    assert apply_to_following(view, first, {"symbol": "OLD", "isin": "I2"}, {}) == {}
    assert apply_to_following(view, first - timedelta(days=1), member, {}) == {}
