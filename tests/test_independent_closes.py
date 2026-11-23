import json

import pytest

from thesistally.close_study import accepted_closes
from thesistally.market import MarketError


def test_only_verified_large_move_decisions_restore_a_close(tmp_path):
    move = {
        "day": "2018-02-02",
        "isin": "A",
        "symbol": "ABC",
        "close": 40,
        "prior_close": 60,
        "reason": "unexplained_move_over_20pct",
    }
    second = {**move, "isin": "B"}
    path = tmp_path / "receipt.json"

    def decisions(*items):
        path.write_text(json.dumps({"cases": items}))
        return accepted_closes(path, [move, second])

    yes, no = {**move, "verified": True}, {**second, "verified": False}
    assert decisions(yes, no) == {(yes["day"], "A"): yes}
    for invalid in (
        [yes, yes],
        [{**yes, "close": 41}],
        [{**no, "verified": "unknown"}],
        [{**yes, "isin": "C"}],
    ):
        with pytest.raises(MarketError):
            decisions(*invalid)


def test_corroborated_book_recognizes_tcs_per_share_cash_without_releasing_other_actions():
    from thesistally.conventions import cash_dividend_notices

    label = " Int Div Rs 4 Per Sh"
    raw = {"dividend": 0, "other_actions": [label, label, "Bonus 1:1"]}
    assert cash_dividend_notices(raw) == raw
    current = cash_dividend_notices(raw, allow_short_dividend=True)
    assert current == {"dividend": 4, "other_actions": ["Bonus 1:1"]}
    assert cash_dividend_notices(current, allow_short_dividend=True) == current
