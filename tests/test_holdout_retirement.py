from datetime import date

import pandas as pd

from thesistally.evaluation import walk_windows


def test_forward_final_reserves_no_historical_sessions():
    days = [d.date() for d in pd.bdate_range("2018-01-01", "2026-11-20")]
    windows = walk_windows(days, 3, 1, None)
    tested = [d for _, group, _ in windows for d in group]
    assert tested == [d for d in days if d >= date(2021, 1, 1)]
    assert all(name.startswith("walk-") for name, _, _ in windows)
    assert walk_windows(days, 3, 1, 18)[-1][0] == "holdout"
