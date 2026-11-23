import numpy as np
import pandas as pd
import pytest

from thesistally.stage3_market import ranked, regression_scores, wilder_rsi2


def fixture():
    days = pd.bdate_range("2018-01-01", periods=270).date
    frames = []
    for symbol, slope in (("SLOW", 0.0003), ("FAST", 0.001), ("TWIN", 0.001)):
        close = 100 * np.exp(slope * np.arange(len(days)))
        frames.append(
            pd.DataFrame(
                {
                    "day": days,
                    "symbol": symbol,
                    "close": close,
                    "high": close + 1,
                    "low": close - 1,
                    "volume": 2e6,
                }
            )
        )
    members = pd.DataFrame(
        {"symbol": ["SLOW", "FAST", "TWIN"], "isin": ["I3", "I2", "I1"], "sector": "software"}
    )
    return pd.concat(frames, ignore_index=True), members


@pytest.mark.parametrize("lookback", [126, 252])
def test_regression_precedence_and_no_future_effect(lookback):
    bars, members = fixture()
    when = sorted(set(bars.day))[-10]
    before = ranked(bars[bars.day <= when], members, lookback)
    after = ranked(bars, members, lookback, as_of=when)
    pd.testing.assert_frame_equal(before, after)
    assert list(after.symbol) == ["TWIN", "FAST", "SLOW"]
    assert after.score.iloc[0] == pytest.approx(np.expm1(0.252))
    assert after.rsi2.tolist() == [100, 100, 100]
    assert after.prior_high10.iloc[0] == pytest.approx(
        bars[(bars.symbol == "TWIN") & (bars.day < when)].tail(10).high.max()
    )


def test_gaps_quarantines_and_absolute_jumps_exclude_before_ranking():
    bars, members = fixture()
    bars = bars.drop(bars[bars.symbol == "FAST"].index[-8])
    bars.loc[bars[bars.symbol == "TWIN"].index[-4], "close"] *= 0.7
    result = ranked(
        bars, members, context={s: {"quarantined": s == "SLOW"} for s in members.symbol}
    )
    assert result.empty


def test_rsi_wilder_seed_flat_and_nonconstant_r_squared():
    c = pd.DataFrame({"x": [100, 103, 102, 100], "flat": [5, 5, 5, 5]})
    assert wilder_rsi2(c).to_dict() == pytest.approx({"x": 37.5, "flat": 50})
    values = np.exp(0.002 * np.arange(126) + 0.1 * np.sin(np.arange(126)))
    x = np.arange(126)
    slope = np.polyfit(x, np.log(values), 1)[0]
    assert regression_scores(pd.DataFrame({"x": values}), 126)["x"] == pytest.approx(
        np.expm1(252 * slope) * np.corrcoef(x, np.log(values))[0, 1] ** 2
    )


def test_normal_scan_dispatches_dip_after_computing_the_full_universe_gate():
    from pathlib import Path

    from thesistally.market import BENCHMARKS
    from thesistally.signals import rules, scan_frame

    bars, members = fixture()
    template = bars[bars.symbol == "FAST"]
    bars = pd.concat(
        [
            bars,
            *(
                template.assign(symbol=s, close=(16.0 if s == "INDIA VIX" else template.close))
                for s in BENCHMARKS
            ),
        ],
        ignore_index=True,
    )
    context = {
        s: {
            "quarantined": False,
            "surveillance": False,
            "earnings_in_days": None,
            "earnings_coverage": "known",
        }
        for s in members.symbol
    }
    cfg = rules(Path("src/thesistally/revisions/dip-rsi10.yaml"))
    gate, calls, _ = scan_frame(bars, members, cfg, context)
    assert gate["enabled"] and gate["breadth_count"] == 3
    assert calls == []  # Monotone price paths have RSI of 100.
