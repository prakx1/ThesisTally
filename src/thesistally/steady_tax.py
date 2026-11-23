"""Reconstruct a report-only tax account from an uninterrupted monthly paper journal."""

import copy
import csv
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from . import market, steady_actions, steady_replay
from .equity_tax import Lot, TaxBook, annual_taxes, fy, gain, investor_return, load_config
from .journal import cost
from .steady_momentum import CAPITAL

D = date.fromisoformat


def dated(s):
    return datetime.strptime(s, "%d-%b-%Y").date()


def record_dates(root):
    notices = defaultdict(list)
    for path in sorted((root / "sources/reference").glob("actions-*.json")):
        if path.name.endswith(".source.json"):
            continue
        raw = path.read_bytes()
        if (
            market.sha(raw)
            != json.loads(path.with_suffix(".json.source.json").read_bytes())["sha256"]
        ):
            raise ValueError(f"Corporate notice input changed: {path}")
        for row in json.loads(raw):
            if row.get("series") != "EQ" or row.get("exDate") in (None, "-"):
                continue
            subject = str(row.get("subject", "")).lower()
            if "div" not in subject and "bonus" not in subject:
                continue
            day = dated(row["exDate"])
            rd, bc = row.get("recDate"), row.get("bcStartDate")
            if rd and rd != "-":
                bounds = (dated(rd), dated(rd))
            elif bc and bc != "-":
                # Holders on the last pre-closure settlement are entitled.
                # Ex-date to start of book closure bounds the unknown record day.
                bounds = (min(day, dated(bc)), max(day, dated(bc)))
            else:
                continue
            for kind, needle in (("dividend", "div"), ("bonus", "bonus")):
                if needle in subject:
                    notices[(row["isin"], day, kind)].append(bounds)
                    notices[(row["symbol"], day, kind)].append(bounds)
    return notices


def grandfather_fmv(root, cut, wanted):
    """Last traded high on or before 31 Jan. Bind each input to the archived manifest."""
    values = {}
    sources = {}
    for folder in sorted((root / "sessions").iterdir(), reverse=True):
        if not wanted - values.keys():
            break
        if folder.name > str(cut):
            continue
        path = folder / "bhavcopy.csv"
        raw = path.read_bytes()
        sha = market.sha(raw)
        if sha != json.loads((folder / "manifest.json").read_bytes())["sha256"]["bhavcopy.csv"]:
            raise ValueError(f"Changed grandfathering bhavcopy on {folder.name}")
        for row in csv.DictReader(raw.decode().splitlines()):
            isin = row["ISIN"].strip()
            if (
                isin in wanted
                and isin not in values
                and row["SctySrs"].strip() in ("EQ", "BE", "BZ")
                and float(row["TtlTradgVol"] or 0) > 0
            ):
                values[isin] = float(row["HghPric"])
                sources[isin] = {
                    "day": folder.name,
                    "raw_high": values[isin],
                    "bhavcopy_sha256": sha,
                }
    missing = wanted - values.keys()
    if missing:
        raise ValueError(f"Missing traded high on or before grandfather date: {missing}")
    return values, sources


def bounds_for(notices, isin, day, kind, symbol=None):
    bounds = notices.get((isin, day, kind), []) or notices.get((symbol, day, kind), [])
    if not bounds:
        raise ValueError(f"Missing {kind} record/closure notice {isin} {day}")
    return min(x[0] for x in bounds), max(x[1] for x in bounds)


class HolderTax:
    """Translate the paper journal's pre-opening shareholder events into demat lots."""

    def __init__(self, book, notices, terms):
        self.book = book
        self.notices = notices
        self.terms = terms
        self.predecessors = {
            (t["successor_isin"], t["effective"]): old for old, t in terms.items() if t.get("bonus")
        }
        self.converted = {}
        self.fractions = {}
        self.fraction_cash = {}
        self.bonus_allotments = {}
        self.fraction_total = 0.0

    def event(self, isin, day, kind, e, prior_holdings):
        prior_symbol = prior_holdings.get(isin, {}).get("symbol")
        stage = e.get("stage")
        if kind == "dividend":
            if str(day) <= self.book.cfg["dividend_exemption_end"]:
                bounds = bounds_for(self.notices, isin, day, "dividend", prior_symbol)
                for lot in self.book.lots[isin]:
                    lot.dividends += ((bounds, e["per_share"]),)
            return
        if e.get("kind") == "rights_no_subscription" or stage in ("suspended", "bonus_credited"):
            return
        if stage in ("bonus_entitlement", "bonus_tradable", "bonus_fraction_paid"):
            ex = e["ex_date"]
            old = self.predecessors[(isin, ex)]
            leg = self.terms[old]["bonus"]
            # The two older holder records give the allotment and credit on the same
            # date. Their official allotment notices explicitly state that date.
            allot = leg.get("allotted")
            if not allot:
                verified = {"INE296A01024": "2025-06-17", "INE274J01014": "2024-07-04"}
                allot = verified[old]
                if allot != leg["credited"]:
                    raise ValueError(f"Unexpected published allotment: {old} {ex}")
            key = f"{isin}/{ex}"
            self.bonus_allotments[key] = {"allotted": allot, "source_url": leg["source_url"]}
            if stage == "bonus_entitlement":
                if ex >= self.book.cfg["bonus_stripping_on_securities_from"] and e["whole"]:
                    bounds = bounds_for(
                        self.notices, old, D(ex), "bonus", prior_holdings.get(old, {}).get("symbol")
                    )
                    self.book.bonus_dates[key] = (bounds, isin, D(allot))
                    self.book.bonus_extra_basis[key] = 0.0
                    for lot in self.book.lots[old]:
                        lot.original_bonus_ids += (key,)
            elif stage == "bonus_tradable":
                extra = self.book.bonus_extra_basis.pop(key, 0.0)
                if e["whole"]:
                    self.book.add(isin, Lot(e["whole"], D(allot), extra / e["whole"], bonus_id=key))
            else:
                self.book.record(
                    isin,
                    Lot(e["fraction"], D(allot), 0),
                    day,
                    e["fraction"],
                    e["cash"] / e["fraction"],
                    0,
                )
                self.fraction_total += e["cash"]
        elif stage == "converted":
            self.converted[isin] = (e["successor_qty"], e["fraction"])
        elif stage == "tradable":
            whole, frac = self.converted.pop(isin)
            new = e["successor_isin"]
            self.fractions[isin] = self.book.convert(
                isin, new, e["ratio"][0] / e["ratio"][1], whole, frac
            )
            self.settle_fraction(isin)
        elif stage in ("credited", "fraction_cash_paid"):
            if e.get("fraction") and (e.get("cash") or not e.get("receivable")):
                self.fraction_cash[isin] = (day, e["cash"])
                self.settle_fraction(isin)
        elif stage == "delisted":
            qty = sum(l.units for l in self.book.lots[isin])
            self.book.sell(isin, day, qty, e["cash"] / qty, 0)
        else:
            raise ValueError(f"Unclassified holder tax disposition on {day}: {stage}")

    def settle_fraction(self, isin):
        if isin in self.fraction_cash and isin in self.fractions:
            day, amount = self.fraction_cash.pop(isin)
            lots = self.fractions.pop(isin)
            price = amount / sum(l.units for l in lots)
            for lot in lots:
                self.book.record(isin, lot, day, lot.units, price, 0)
            self.fraction_total += amount


def fiscal_costs(sessions):
    result = {}
    prior = CAPITAL
    for s in sessions:
        row = result.setdefault(
            fy(D(s["day"])),
            {
                "first": s["day"],
                "sessions": 0,
                "one_way_turnover_pct": 0.0,
                "buy_sell_notional": 0.0,
                "fees": 0.0,
                "slippage": 0.0,
            },
        )
        row["last"] = s["day"]
        row["pre_tax_ending_value"] = s["equity"]
        row["sessions"] += 1
        row["buy_sell_notional"] += s["turnover"]
        row["one_way_turnover_pct"] += 50 * s["turnover"] / prior
        row["fees"] += s["fees"]
        row["slippage"] += s["slippage"]
        row["total_cost"] = row["fees"] + row["slippage"]
        prior = s["equity"]
    return result


def endpoint(amount, start, end, taxes):
    tax = sum(r["tax"] for r in taxes.values())
    years = (end - start).days / 365.2425
    return {
        "before_tax": amount,
        "tax": tax,
        "after_tax": amount - tax,
        **investor_return(start, end, amount, taxes, CAPITAL),
        "after_tax_return_pct": 100 * ((amount - tax) / CAPITAL - 1),
        "after_tax_cagr_pct": 100 * (((amount - tax) / CAPITAL) ** (1 / years) - 1),
        "before_tax_cagr_pct": 100 * ((amount / CAPITAL) ** (1 / years) - 1),
    }


def measure(db, run, root, benchmark, cfg, notices):
    sessions = list(db.execute("SELECT * FROM sessions WHERE run=? ORDER BY day", (run,)))
    if not sessions:
        raise ValueError(f"No sessions in tax book: {run}")
    start, end = D(sessions[0]["prior"]), D(sessions[-1]["day"])
    events, fills = defaultdict(list), defaultdict(list)
    for e in db.execute(
        "SELECT * FROM events WHERE run=? AND kind IN ('corporate_action','dividend') ORDER BY id",
        (run,),
    ):
        events[e["day"]].append(e)
    for f in db.execute("SELECT * FROM fills WHERE run=? ORDER BY id", (run,)):
        fills[f["day"]].append(f)
    book = TaxBook(cfg)
    holder = HolderTax(
        book,
        notices,
        steady_actions.terms(root, revised=True, close_verified=run.endswith("-tax-continuous")),
    )
    stt = steady_replay.execution_config()["cost"]["stt"]
    assets_at_cut = {}
    prior = str(start)
    holdings = {}
    for s in sessions:
        day = D(s["day"])
        if s["prior"] != prior:
            raise ValueError(f"Discontinuous account {run} on {day}")
        prior = s["day"]
        for e in events[s["day"]]:
            holder.event(e["isin"], day, e["kind"], json.loads(e["detail"]), holdings)
        for f in fills[s["day"]]:
            qty, price = f["qty"], f["price"]
            # STT is part of the paid fee, but is not deductible under section 48.
            fee = f["fee"] / qty - price * stt
            if f["side"] == "buy":
                book.add(f["isin"], Lot(qty, day, price + fee))
            else:
                book.sell(f["isin"], day, qty, price, fee)
        holdings = json.loads(s["state"])["positions"]
        for isin in set(holdings) | set(book.lots):
            actual = sum(l.units for l in book.lots.get(isin, []))
            if abs(actual - holdings.get(isin, {}).get("qty", 0)) > 1e-6:
                raise ValueError(f"Tax lots disagree with journal on {day} {isin}: {actual}")
        if s["day"] == cfg["grandfather_day"]:
            values, assets_at_cut = grandfather_fmv(root, day, set(holdings))
            for isin, value in values.items():
                for lot in book.lots[isin]:
                    lot.fmv = value
    if holder.fraction_cash:
        raise ValueError(f"Unattributed fractional proceeds for {run}")
    year_costs = fiscal_costs(sessions)
    years = annual_taxes(book.gains, fy(start), fy(end), cfg)
    unit_price, sale_price = benchmark[str(start)], benchmark[str(end)]
    units = CAPITAL / unit_price
    redemption_stt = round(units * sale_price * cfg["fund_redemption_stt"], 2)
    investor = Lot(units, start, unit_price, benchmark.get(cfg["grandfather_day"]))
    category, index_gain = gain(cfg, investor, end, units, sale_price, 0)
    index_taxes = annual_taxes([(end, category, index_gain)], fy(start), fy(end), cfg)
    cumulative = 0.0
    for y, row in years.items():
        cumulative += row["tax"]
        row.update(year_costs[y])
        row["after_accrued_tax"] = row["pre_tax_ending_value"] - cumulative
        row["benchmark_tax"] = index_taxes[y]["tax"]
        row["benchmark_after_accrued_tax"] = (
            units * benchmark[row["last"]]
            - row["benchmark_tax"]
            - (redemption_stt if y == fy(end) else 0)
        )
    final_state = json.loads(sessions[-1]["state"])
    liquidation = None
    if not (
        final_state["bonuses"]
        or final_state["receivables"]
        or any(pos["scheme"] for pos in holdings.values())
    ):
        tax_clone = copy.deepcopy(book)
        closing_cost = 0.0
        closing_fees = 0.0
        cfg_cost = steady_replay.execution_config()
        for isin, pos in holdings.items():
            qty = pos["qty"]
            if not qty:
                continue
            price = pos["mark"] * (1 - cfg_cost["cost"]["slippage_bps"] / 10000)
            charges = cost("sell", qty * price, cfg_cost)
            closing_fees += charges
            closing_cost += charges + qty * (pos["mark"] - price)
            tax_clone.sell(isin, end, qty, price, charges / qty - price * stt)
        liquidation_taxes = annual_taxes(tax_clone.gains, fy(start), fy(end), cfg)
        amt = sum(r["tax"] for r in liquidation_taxes.values())
        liquidation = {
            **endpoint(sessions[-1]["equity"] - closing_cost, start, end, liquidation_taxes),
            "additional_terminal_costs": closing_cost,
            "additional_terminal_fees": closing_fees,
            "change_in_total_tax": amt - cumulative,
        }
    return {
        "run": run,
        "start_prior_close": str(start),
        "end": str(end),
        "sessions": len(sessions),
        "capital": CAPITAL,
        "per_fy": years,
        "hypothetical_terminal_liquidation": liquidation,
        "strategy": endpoint(sessions[-1]["equity"], start, end, years),
        "tri_proxy": endpoint(units * sale_price - redemption_stt, start, end, index_taxes),
        "tri_terminal_sale": {
            "units": units,
            "acquisition_nav": unit_price,
            "redemption_stt": redemption_stt,
            "sale_nav": sale_price,
            "grandfather_nav": investor.fmv,
            "gain_kind": category,
            "tax_gain": index_gain,
        },
        "fees": sum(s["fees"] for s in sessions),
        "slippage": sum(s["slippage"] for s in sessions),
        "loss_disallowed_dividend": book.ignored_dividend_loss,
        "loss_deferred_bonus": book.deferred_bonus_loss,
        "corporate_fraction_cash": holder.fraction_total,
        "grandfathered_highs": assets_at_cut,
        "allotments": holder.bonus_allotments,
        "fifo_gains_count": len(book.gains),
        "terminal_stock_value": sessions[-1]["invested"],
    }


def compare(journal, root, benchmark_path, config_path, run_suffix="tax-continuous"):
    cfg = load_config(config_path)
    notices = record_dates(root)
    benchmark = steady_replay.read_benchmark(benchmark_path)
    with closing(sqlite3.connect(f"{Path(journal).resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {
            "method_revision": cfg["revision"],
            "journal_sha256": market.sha(Path(journal).read_bytes()),
            "tax_config_sha256": market.sha(Path(config_path).read_bytes()),
            "rates": cfg,
            "benchmark_sha256": market.sha(Path(benchmark_path).read_bytes()),
            "variants": {
                v: measure(db, f"{v}-{run_suffix}", root, benchmark, cfg, notices)
                for v in ("A126", "A252")
            },
        }
