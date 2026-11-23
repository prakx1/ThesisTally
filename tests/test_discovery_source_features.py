"""Outcome-blind checks of dated stock information and source-strategy ordering."""

import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from thesistally import stage3_market
from thesistally.discovery_trades import build, build_plans

ARCHIVE = Path(__file__).resolve().parents[2] / "ThesisTally" / "data"
pytestmark = pytest.mark.skipif(
    not (ARCHIVE / "baseline/runs/e923bed5c9d9448d7340/walk-1/journal.sqlite").exists(),
    reason="The optional immutable historical price and journal archive is unavailable",
)


def test_each_stock_and_sector_matches_dated_published_member_snapshot():
    trades, _ = build(ARCHIVE)
    observations = pd.concat([trades, build_plans(ARCHIVE)], ignore_index=True)
    for (_, day), g in observations.groupby([observations.strategy.eq("pullback"), "day"]):
        view = "baseline" if g.strategy.iloc[0] == "pullback" else "stage3"
        folder = ARCHIVE / view / "market/sessions" / day
        raw = (folder / "members.csv").read_bytes()
        digest = json.loads((folder / "manifest.json").read_bytes())["sha256"]["members.csv"]
        assert hashlib.sha256(raw).hexdigest() == digest
        dated = {
            r["ISIN Code"]: (r["Symbol"], r["Industry"])
            for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        }
        for isin, symbol, sector in g[["isin", "symbol", "sector"]].itertuples(
            index=False, name=None
        ):
            s, industry = dated[isin]
            assert symbol == s
            normalized = (
                "unknown"
                if not industry or industry.strip().casefold() in {"unknown", "na", "n/a", "-"}
                else industry
            )
            assert sector == normalized


def test_ranks_reconcile_to_signal_date_market_without_later_trades():
    frame, _ = build(ARCHIVE)
    for (strategy, year), group in frame.groupby(["strategy", "year"]):
        # Deterministic first observed signal date in each available strategy-year.
        day = group.day.min()
        expected = group[group.day == day][["isin", "symbol", "rank_score"]]
        key = "baseline" if strategy == "pullback" else "stage3"
        root = ARCHIVE / key / "market"
        if strategy == "pullback":
            folder = root / "sessions" / day
            current = pd.read_csv(folder / "members.csv")["Symbol"]
            prices = pd.read_parquet(folder / "bars.parquet", columns=["symbol", "day", "close"])
            assert str(prices.day.max()) == day
            prices = prices[prices.symbol.isin(current)].sort_values(["symbol", "day"])
            grouped = prices.groupby("symbol").close
            strength = grouped.last().div(grouped.shift(126).groupby(prices.symbol).last()) - 1
            ranks = strength.rank(pct=True)
            recalculated = expected.symbol.map(ranks)
        else:
            _, ranked = stage3_market.session(root, date.fromisoformat(day), lookbacks=(126,))
            recalculated = expected["isin"].map(ranked[126].set_index("isin")["rank"])
        np.testing.assert_allclose(expected.rank_score, recalculated, atol=1e-12, equal_nan=True)
