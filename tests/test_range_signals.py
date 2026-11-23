from datetime import date

import numpy as np
import pandas as pd
import pytest

from thesistally.range_history import dividend_amount
from thesistally.range_signals import MIN_TURNOVER, classify, close_features, count_traversals


def sample():
    dates = pd.Index(pd.bdate_range("2014-01-01", periods=500).date, name="day")
    close = 100 + 8 * np.sin(np.arange(500) * np.pi / 12)
    frame = pd.DataFrame(
        {
            "adj_close": close,
            "close": close,
            "adj_low": close - 0.1,
            "adj_high": close + 0.1,
            "cash_yield": 0.0,
            "price_factor": 1.0,
            "share_factor": 1.0,
        },
        index=dates,
    )
    return frame, dates


def test_distinct_range_crossings_and_zero_width():
    assert count_traversals([0.8, 0.2, 0.1, 0.4, 0.8, 0.85, 0.9, 0.2, np.nan, 0.8, 0.15, 0.81]) == 3
    f, d = sample()
    assert classify(f, d, MIN_TURNOVER)["cohort"] == "range"
    f[["adj_close", "adj_low", "adj_high"]] = 100.0
    assert classify(f, d, MIN_TURNOVER)["traversals"] == 0


def test_registered_collapse_reference_is_before_the_evaluation():
    f, d = sample()
    f.iloc[:250, f.columns.get_loc("adj_low")] = 120.0
    result = classify(f, d, MIN_TURNOVER)
    assert "collapse" in result["reason"] and result["previous_low"] == 120.0
    assert classify(f.iloc[1:], d, MIN_TURNOVER)["cohort"] == "unknown"
    assert classify(f, d, MIN_TURNOVER - 1)["cohort"] == "ineligible"


def test_reinvest_dividends_and_preserve_non_subscribing_rights_dilution():
    f, d = sample()
    f["close"] = 100.0
    f.loc[d[-200] :, "close"] = 80.0
    f.loc[d[-200] :, "price_factor"] = 0.8
    f.loc[d[-100], "cash_yield"] = 1 / 80
    f.loc[d[-70], "cash_yield"] = 2 / 80
    assert classify(f, d, MIN_TURNOVER)["total_return"] == pytest.approx(
        0.8 * (1 + 1 / 80) * (1 + 2 / 80) - 1
    )
    f.loc[d[-40] :, "close"] = 40.0
    f.loc[d[-40] :, "share_factor"] = 2.0
    assert classify(f, d, MIN_TURNOVER)["total_return"] == pytest.approx(
        0.8 * (1 + 1 / 80) * (1 + 2 / 80) - 1
    )
    f.loc[d[-60], "cash_yield"] = np.nan
    assert classify(f, d, MIN_TURNOVER)["cohort"] == "unknown"


def test_official_dividend_amounts_and_unknown_terms():
    assert dividend_amount("Final dividend Rs 6 per share and special dividend Rs 2 per share") == 8
    assert dividend_amount("Dividend - Rs 6 including special dividend Rs 2 per share") == 6
    assert dividend_amount("Dividend ₹6 per share of nominal face value Rs 2") is None
    assert dividend_amount("Dividend 100% on face value Rs 2") is None
    assert dividend_amount("Dividend ₹6 per share and bonus 2:1") is None
    assert dividend_amount("Dividend to be declared") is None


def test_past_daily_setup_independent_of_appended_price():
    names = pd.DataFrame([{"symbol": "ABC", "isin": "ID-A", "sector": "Metals"}])
    dates = pd.bdate_range("2014-01-01", periods=71).date
    close = np.linspace(130, 100, 71)
    bars = pd.DataFrame(
        {
            "day": dates,
            "symbol": "ABC",
            "close": close,
            "low": close - 0.2,
            "high": close + 0.2,
            "volume": 1000000.0,
        }
    )
    cutoff = dates[-2]
    short = bars[bars.day <= cutoff]
    first, _ = close_features(short, names)
    assert len(first) == 1
    bars.loc[bars.day > cutoff, ["high", "close"]] = 1e6
    second, _ = close_features(bars, names, as_of=cutoff)
    pd.testing.assert_frame_equal(first, second)
    assert first.iloc[0].rsi2 == 0
    assert not first.iloc[0].low60 > first.iloc[0].close


def test_incomplete_daily_price_range_is_unscored():
    names = pd.DataFrame([{"symbol": "ABC", "isin": "IS", "sector": "Metals"}])
    dates = pd.bdate_range("2020-01-01", periods=60).date
    bars = pd.DataFrame(
        {
            "day": dates,
            "symbol": "ABC",
            "close": 100.0,
            "low": 99.0,
            "high": 101.0,
            "volume": 1000000.0,
        }
    )
    bar_index = bars.copy()
    bar_index["symbol"] = "NIFTY 200"
    complete = pd.concat([bar_index, bars[bars.day != date(2020, 1, 3)]], ignore_index=True)
    found, missing = close_features(complete, names)
    assert found.empty and missing == 1


def test_midmonth_only_carries_a_proven_continuing_split_from_the_first_new_print():
    from thesistally.range_signals import assign_monthly

    days = ["2019-02-28", "2019-03-01", "2019-03-04"]
    raw = [{"day": d, "isin": "NEW", "turnover": MIN_TURNOVER} for d in days]
    raw.append({"day": days[-1], "isin": "NEW_MEMBER", "turnover": MIN_TURNOVER})
    monthly = pd.DataFrame([{"day": days[0], "isin": "OLD", "cohort": "range", "reason": ""}])
    transitions = [
        {
            "mode": "verified_shared",
            "first_printed_new_isin": days[-1],
            "old_isin": "OLD",
            "new_isin": "NEW",
        }
    ]
    entries, _ = assign_monthly(raw, [{"day": d} for d in days], monthly, days, transitions)
    assert entries.cohort.tolist() == ["unknown", "unknown", "range", "unknown"]


def test_dated_dividend_aliases_and_nonfungible_issuer_classes():
    from thesistally.range_history import DIV, matching_issue

    assert DIV.search("Interim Dividned - Rs 135 Per Share")
    assert DIV.search("Annual General Meetingdividend Re 0.50")
    assert not DIV.search("Face value split (sub-division)")
    assert dividend_amount("Interim Dividned - Rs 135 Per Share") == 135
    quoted, old = "INE494B01023", "INE494B01015"
    assert matching_issue(quoted, old, "TVSMOTOR", {"INE494B": {("TVSMOTOR", quoted)}})
    assert not matching_issue(
        quoted, old, "TVSMOTOR", {"INE494B": {("TVSMOTOR", quoted), ("OTHER", old)}}
    )
    assert not matching_issue(
        quoted, "INE999X01015", "TVSMOTOR", {"INE494B": {("TVSMOTOR", quoted)}}
    )


def test_two_cash_notices_must_describe_separate_legs():
    from thesistally.range_history import standalone_cash

    assert np.isnan(
        standalone_cash(
            ["Interim dividend Rs 0.80 per share", "Interim dividend Rs 1.50 per share"]
        )
    )
    assert np.isnan(
        standalone_cash(
            ["Annual general meeting/dividend Rs 1", "Annual general meeting/dividend Rs 2"]
        )
    )
    assert standalone_cash(["Final dividend Rs 2", "Interim dividend Rs 2"]) == 4
    assert standalone_cash(["Dividend Rs 2", "Special dividend Rs 6"]) == 8
