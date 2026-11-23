from datetime import date

import pytest

from thesistally.equity_tax import Lot, TaxBook, annual_taxes, fy, gain, load_config, months

D = date.fromisoformat
CFG = load_config()


def test_holding_anniversary_grandfather_and_transfer_fee():
    old = Lot(5, D("2017-06-20"), 10, 30)
    assert gain(CFG, old, D("2018-06-20"), 2, 20, 1) == ("short", 18)
    assert gain(CFG, old, D("2018-06-21"), 2, 20, 1) == ("long", -2)
    assert gain(CFG, old, D("2018-06-21"), 2, 8, 0) == ("long", -4)
    assert months(D("2024-02-29"), 12) == D("2025-02-28")
    assert fy(D("2024-03-31")) == 2023


def test_midyear_switch_one_exemption_cess_and_pre_2018():
    rates = [
        (D("2018-03-31"), "long", 1e6),
        (D("2018-03-31"), "short", 10000),
        (D("2024-07-22"), "long", 90000),
        (D("2024-07-23"), "long", 100000),
        (D("2024-07-22"), "short", 1000),
        (D("2024-07-23"), "short", 2000),
    ]
    t = annual_taxes(rates, 2017, 2024, CFG)
    assert t[2017]["tax"] == 1545
    assert t[2024]["long_tax"] == 6500
    assert t[2024]["short_tax"] == 550
    assert t[2024]["tax"] == pytest.approx(7050 * 1.04)
    assert t[2024]["lt_exemption_used"] == 125000


def test_loss_expiry_and_restriction():
    gains = [
        (D("2018-04-03"), "long", -1000),
        (D("2018-01-03"), "short", -8000),
        (D("2024-06-15"), "short", 5000),
        (D("2026-06-15"), "short", 10000),
        (D("2026-09-01"), "long", 200000),
    ]
    t = annual_taxes(gains, 2017, 2026, CFG)
    assert t[2024]["long_loss_carried"] == 1000
    assert t[2024]["short_loss_carried"] == 3000
    assert t[2026]["short_tax"] == 2000
    assert t[2026]["long_tax"] == 74000 * 0.125


def test_fifo_split_preserves_basis_dates_and_fraction():
    t = TaxBook(CFG)
    t.add("old", Lot(2, D("2021-01-01"), 20))
    t.add("old", Lot(3, D("2021-05-01"), 30))
    fraction = t.convert("old", "new", 0.7, 3, 0.5)
    assert sum(x.units for x in fraction) == pytest.approx(0.5)
    assert sum(x.units * x.basis for x in fraction + t.lots["new"]) == pytest.approx(130)
    t.sell("new", D("2022-04-01"), 3, 30, 0)
    assert [k for _, k, _ in t.gains] == ["long", "short"]
    with pytest.raises(ValueError, match="quantity missing"):
        t.sell("new", D("2022-04-01"), 1, 30, 0)


def test_exempt_dividend_loss_and_bonus_loss_deferral():
    t = TaxBook(CFG)
    bought, record, sale = map(D, ("2018-06-01", "2018-07-02", "2018-09-15"))
    t.add("a", Lot(3, bought, 100, dividends=(((record, record), 12),)))
    t.sell("a", sale, 2, 80, 0)
    assert t.ignored_dividend_loss == 24 and t.gains[0][2] == -16
    key = "2023-A"
    day = D("2023-06-01")
    t.bonus_dates[key] = ((day, day), "b", day)
    t.add("b", Lot(2, D("2023-04-12"), 100, original_bonus_ids=(key,)))
    t.add("b", Lot(2, day, 0, bonus_id=key))
    t.sell("b", D("2023-07-01"), 2, 55, 0)
    assert t.deferred_bonus_loss == 90 and t.lots["b"][0].basis == 45
    t.sell("b", D("2023-08-01"), 2, 50, 0)
    assert t.gains[-1][2] == 10


def test_tax_view_carries_through_fiscal_year_and_crystallises_index_only_at_end(
    tmp_path, monkeypatch
):
    import json

    from thesistally import steady_momentum, steady_tax

    db = steady_momentum.open_journal(tmp_path / "paper.sqlite")
    monkeypatch.setattr(steady_tax.steady_actions, "terms", lambda *args, **kw: {})
    run = "synthetic"
    state = lambda qty: json.dumps(
        {
            "positions": {"x": {"qty": qty, "mark": 130, "symbol": "X", "scheme": {}}},
            "bonuses": [],
            "receivables": [],
        }
    )
    try:
        db.execute("INSERT INTO books VALUES(?,?,?,?)", (run, "{}", "2024-03-27", "{}"))
        for day, prior, eq, q, tv, fees in [
            ("2024-03-28", "2024-03-27", 1_000_000, 100, 10000, 14),
            ("2024-04-01", "2024-03-28", 1_003_000, 50, 6500, 20),
        ]:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run, day, prior, 1, 1, eq, q * 130, eq - q * 130, tv, fees, 0, 0, state(q)),
            )
        for day, side, q, px, fee in [
            ("2024-03-28", "buy", 100, 100, 14),
            ("2024-04-01", "sell", 50, 130, 20),
        ]:
            db.execute(
                """INSERT INTO fills (run,day,signal,isin,side,qty,raw_open,price,fee,slippage,reason,unknown_band)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run, day, day, "x", side, q, px, px, fee, 0, "monthly", 0),
            )
        out = steady_tax.measure(
            db,
            run,
            tmp_path,
            {"2024-03-27": 100.0, "2024-03-28": 105.0, "2024-04-01": 110.0},
            CFG,
            {},
        )
        # Only half the original lot is sold. STT of 10 on the purchase and 6.50
        # on the sale is paid, but excluded from the tax basis/transfer expenses.
        assert out["per_fy"][2024]["realised_short_gain"] == pytest.approx(1484.5)
        assert out["per_fy"][2023]["tax"] == out["per_fy"][2023]["benchmark_tax"] == 0
        assert out["per_fy"][2024]["benchmark_tax"] == pytest.approx(100000 * 0.15 * 1.04)
        assert out["fees"] == 34
        assert out["hypothetical_terminal_liquidation"]["additional_terminal_costs"] > 0
        assert out["terminal_stock_value"] == 6500
        assert out["hypothetical_terminal_liquidation"]["tax"] > out["strategy"]["tax"]
        assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 2
    finally:
        db.close()


def test_investor_xirr_prices_tax_timing_and_nets_the_partial_final_year():
    from thesistally.equity_tax import investor_return

    start, end = D("2022-12-30"), D("2024-09-01")
    early = investor_return(start, end, 1500, {2022: {"tax": 100}, 2024: {"tax": 0}}, 1000)
    late = investor_return(start, end, 1500, {2024: {"tax": 100}}, 1000)
    assert early["investor_xirr_pct"] < late["investor_xirr_pct"]
    assert late["investor_xirr_pct"] == pytest.approx(
        100 * (1.4 ** (365 / (end - start).days) - 1)
    )
    assert early["investor_cash_flows"][1] == {"day": "2023-03-31", "net_inr": -100}
    assert late["investor_cash_flows"][-1] == {"day": str(end), "net_inr": 1400}
