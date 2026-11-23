from datetime import date, timedelta

import numpy as np
import pandas as pd

from thesistally import discovery_cap
from thesistally import discovery_features as f


def test_historical_full_cap_uses_measured_period_publication_and_revision_clocks():
    old = (date(2019, 12, 31), date(2020, 1, 6), "sha-old", {"A": 1})
    updated = (date(2020, 6, 30), date(2020, 7, 8), "sha-updated", {"A": 2})
    late = (date(2018, 12, 31), date(2020, 7, 9), "sha-late", {"A": 8})
    assert f.full_cap([updated, old, late], date(2020, 7, 7)) is old
    assert f.full_cap([updated, old, late], date(2020, 7, 9)) is updated
    assert f.full_cap([old], date(2021, 6, 30)) is None
    assert f.full_cap([old], date(2020, 1, 5)) is None
    period = date(2017, 12, 31)
    migrated = date(2025, 8, 19)
    assert discovery_cap.usable_date(period, date(2018, 1, 3), migrated) == date(2018, 1, 6)
    assert discovery_cap.usable_date(period, date(2018, 2, 1), migrated) == migrated + timedelta(
        days=1
    )


def test_close_date_terciles_use_whole_current_universe_and_preserve_price_gaps():
    names = [f"S{i:02}" for i in range(12)]
    members = pd.DataFrame({"symbol": names, "isin": ["I" + s for s in names]})
    days = pd.date_range("2019-01-01", periods=21)
    rng = np.random.default_rng(2026)
    changes = rng.normal(0, 0.004, size=(21, 12)) * np.arange(1, 13)
    closes = pd.DataFrame(100 * np.exp(changes.cumsum(axis=0)), index=days, columns=names)
    volume = pd.DataFrame(
        np.broadcast_to(np.arange(1, 13) * 100, (21, 12)), index=days, columns=names
    )
    closes.loc[days[6], names[0]] = np.nan
    cap = (date(2018, 12, 31), date(2019, 1, 6), "x", dict(zip(members["isin"], range(12))))
    desc = f.market_descriptors(closes, volume, members, cap).set_index("symbol")
    assert desc.loc[names[0], "volatility_tercile"] == "unknown"
    assert desc.loc[names[0], "liquidity_tercile"] == "unknown"
    assert desc.loc[names[-1], "size_tercile"] == "high"
    assert desc.loc[names[1], "volatility_20"] == np.log(closes[names[1]]).diff().std() * np.sqrt(
        252
    )
    tied = pd.Series([1.0] * 4 + [2.0] * 4 + [3.0] * 4)
    assert list(f.terciles(tied).unique()) == ["low", "middle", "high"]
    assert (f.terciles(tied.mask(tied.index < 3)) == "unknown").all()


def test_planned_stop_and_regime_cutoffs_have_explicit_unknowns():
    rows = pd.DataFrame(
        {
            "vix": [14.9, 15, 22, 23, None],
            "breadth": [0.39, 0.4, 0.6, 0.61, None],
            "stop_fraction": [0.0499, 0.05, 0.0699, 0.07, None],
        }
    )
    df = f.fixed_buckets(rows)
    assert list(df.vix_bucket) == ["<15", "15–22", "15–22", ">22", "unknown"]
    assert list(df.breadth_bucket) == ["<40%", "40–60%", "40–60%", ">60%", "unknown"]
    assert list(df.stop_distance_bucket) == ["<5%", "5–<7%", "5–<7%", "≥7%", "unknown"]
