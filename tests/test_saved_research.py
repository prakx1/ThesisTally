import json
import os
from datetime import date

import pytest

from thesistally.market import MarketError
from thesistally.research import run_report
from thesistally.saved_research import latest_stock_report


def test_saved_report_uses_generation_order_and_the_requested_market_date(tmp_path):
    folder = tmp_path / "research" / "M&M"
    folder.mkdir(parents=True)
    first = {"symbol": "M&M", "as_of": "2025-06-02", "report": {"rating": "hold"}}
    latest = {**first, "as_of": "2025-05-30"}
    (folder / "2025-06-03T10-00-00+05-30-11111111.json").write_text(json.dumps(first))
    (folder / "2025-06-04T10-00-00+05-30-22222222.json").write_text(json.dumps(latest))
    assert latest_stock_report(tmp_path, "m&m") == latest
    assert latest_stock_report(tmp_path, "M&M", date(2025, 6, 2)) == first
    assert latest_stock_report(tmp_path, "M&M", date(2025, 6, 3)) is None
    assert latest_stock_report(tmp_path, "SBIN") is None
    assert not (tmp_path / "journal.sqlite").exists()


def test_unreadable_or_mismatched_report_is_identified(tmp_path):
    directory = tmp_path / "research" / "SBIN"
    directory.mkdir(parents=True)
    file = directory / "2025-01-31T20-01-00+05-30-11111111.json"
    file.write_text("{partial")
    with pytest.raises(MarketError, match="unreadable"):
        latest_stock_report(tmp_path, "SBIN")
    file.write_text('{"symbol":"OTHER","as_of":"2025-01-31","report":{}}')
    with pytest.raises(MarketError, match="missing report data for SBIN"):
        latest_stock_report(tmp_path, "SBIN")
    with pytest.raises(MarketError, match="NSE stock symbol"):
        latest_stock_report(tmp_path, "../SBIN")


def test_same_second_reports_use_write_order_instead_of_the_random_suffix(tmp_path):
    folder = tmp_path / "research" / "SBIN"
    folder.mkdir(parents=True)
    for order, suffix, rating in [(1, "ffffffff", "hold"), (2, "00000000", "sell")]:
        path = folder / f"2025-06-03T10-00-00+05-30-{suffix}.json"
        path.write_text(
            json.dumps({"symbol": "SBIN", "as_of": "2025-06-02", "report": {"rating": rating}})
        )
        os.utime(path, (order, order))
    assert latest_stock_report(tmp_path, "SBIN")["report"]["rating"] == "sell"


def test_fresh_stock_research_explains_missing_market_data(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_AI_PROVIDER", "codex")
    with pytest.raises(MarketError, match="No archived market sessions"):
        run_report(tmp_path, "SBIN")
    with pytest.raises(MarketError, match="No archived session for 2025-06-02"):
        run_report(tmp_path, "SBIN", date(2025, 6, 2))
