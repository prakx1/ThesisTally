"""Apply Strategy A's dated tax table and FIFO method to a single D walk-forward book."""

import copy
import json
from collections import defaultdict, deque
from datetime import date

from . import corporate, equity_tax, steady_tax
from .journal import cost

D = date.fromisoformat


def benchmark_tax(benchmark, start, end, capital, config):
    buy, sell = benchmark[str(start)], benchmark[str(end)]
    units = capital / buy
    fees = round(units * sell * config["fund_redemption_stt"], 2)
    lot = equity_tax.Lot(units, start, buy, benchmark.get(config["grandfather_day"]))
    kind, amount = equity_tax.gain(config, lot, end, units, sell, 0)
    years = equity_tax.annual_taxes(
        [(end, kind, amount)], equity_tax.fy(start), equity_tax.fy(end), config
    )
    return {
        **steady_tax.endpoint(units * sell - fees, start, end, years),
        "per_fy": years,
        "redemption_stt": fees,
        "sale_day": str(end),
        "capital_gain_category": kind,
    }


def add_action(book, event, day, fractions, bonus_keys, notices):
    """Consume only the journal's actual pre-open entitlements and settlements."""
    stage = event["stage"]
    if stage in (
        "suspended",
        "tradable",
        "bonus_credited",
        "bonus_pending_rebased",
        "pending_entry_rebased",
        "rights_lapsed",
    ):
        return
    if stage in ("converted", "credited", "fraction_paid", "delisted"):
        old = event["predecessor_isin"]
        if stage == "converted":
            ratio = event["ratio"]
            lots = book.convert(
                old,
                event["successor_isin"],
                ratio[0] / ratio[1],
                event["successor_qty"],
                event["fraction"],
            )
            if lots:
                fractions[old] = lots
        elif stage == "delisted":
            q = sum(lot.units for lot in book.lots[old])
            if q:
                book.sell(old, day, q, event["cash"] / q, 0)
        elif event.get("fraction") and (stage == "fraction_paid" or str(day) >= event["cash_date"]):
            lots = fractions.pop(old)
            price = event["fractional_cash"] / sum(lot.units for lot in lots)
            for lot in lots:
                book.record(old, lot, day, lot.units, price, 0)
        return
    isin = event["isin"]
    key = f"{isin}/{event['ex_date']}"
    allotment = event.get("allotted")
    if not allotment:
        # The same two issuer allotment notices cited in A's HolderTax overlay.
        verified = {"INE296A01024": "2025-06-17", "INE274J01014": "2024-07-04"}
        allotment = verified.get(isin)
        if not allotment or allotment != event.get("credited"):
            raise ValueError(f"Unverified bonus allotment date for {key}")
    allotted = D(allotment)
    if stage == "bonus_entitlement":
        bonus_keys.add(key)
        if event["ex_date"] >= book.cfg["bonus_stripping_on_securities_from"] and event["whole"]:
            bounds = steady_tax.bounds_for(notices, isin, D(event["ex_date"]), "bonus")
            book.bonus_dates[key] = (bounds, isin, allotted)
            book.bonus_extra_basis[key] = 0.0
            for lot in book.lots[isin]:
                lot.original_bonus_ids += (key,)
    elif stage == "bonus_tradable":
        bonus_keys.discard(key)
        if q := event["whole"]:
            extra = book.bonus_extra_basis.pop(key, 0)
            book.add(isin, equity_tax.Lot(q, allotted, extra / q, bonus_id=key))
    elif stage == "bonus_fraction_paid":
        q = event["fraction"]
        book.record(isin, equity_tax.Lot(q, allotted, 0), day, q, event["cash"] / q, 0)
    else:
        raise ValueError(f"Unclassified holder tax event {stage}")


def book_tax(
    db, run, book_kind, root, benchmark, start, end, ending_value, cfg, cash_events, notices
):
    """Funded FIFO by security. Each independent call owns a separate counterfactual position."""
    start, end = D(start), D(end)
    capital = cfg["risk"]["capital"]
    config = equity_tax.load_config()
    reference = benchmark_tax(benchmark, start, end, capital, config)
    allocation = "portfolio" if book_kind == "funded" else "unconstrained"
    where = "c.run=? AND c.allocation=? AND c.buy IS NOT NULL AND c.status!='unmeasurable'"
    events = list(
        db.execute(
            f"""SELECT e.*,c.symbol,c.isin FROM events e JOIN calls c ON c.id=e.call_id
        WHERE {where} AND e.kind IN ('fill','dividend','corporate_action') ORDER BY e.day,e.id""",
            (run, allocation),
        )
    )
    fills = defaultdict(deque)
    for f in db.execute(
        f"""SELECT f.*,s.isin FROM fills f JOIN calls c ON c.id=f.call_id
         JOIN fill_securities s ON s.fill_id=f.id WHERE {where} ORDER BY f.id""",
        (run, allocation),
    ):
        fills[f["call_id"], f["day"]].append(f)
    books, fracs, bonuses = {}, defaultdict(dict), defaultdict(set)
    active_isins = {}
    # Seed grandfathering only on demand; a short-held lot never needs this basis.
    cut = D(config["grandfather_day"])
    cut_checked = False
    for e in events:
        day = D(e["day"])
        if day > cut and not cut_checked:
            wanted = {i for b in books.values() for i, lots in b.lots.items() if lots}
            highs, _ = steady_tax.grandfather_fmv(root, cut, wanted) if wanted else ({}, {})
            for b in books.values():
                for isin, lots in b.lots.items():
                    for lot in lots:
                        lot.fmv = highs[isin]
            cut_checked = True
        cid = e["call_id"]
        owner = cid if book_kind == "equal_risk" else 0
        if owner not in books:
            books[owner] = equity_tax.TaxBook(config)
        book = books[owner]
        if e["kind"] == "fill":
            f = fills[cid, e["day"]].popleft()
            q, price, isin = f["qty"], f["price"], f["isin"]
            fee = f["fees"] / q - price * cfg["cost"]["stt"]
            if f["side"] == "buy":
                book.add(isin, equity_tax.Lot(q, day, price + fee))
                active_isins[cid] = isin
            else:
                book.sell(isin, day, q, price, fee)
        elif e["kind"] == "corporate_action":
            action = json.loads(e["detail"])
            add_action(book, action, day, fracs[owner], bonuses[owner], notices)
            if action["stage"] == "converted":
                active_isins[cid] = action["successor_isin"]
        elif str(day) <= config["dividend_exemption_end"]:
            isin = active_isins[cid]
            item = cash_events.get((str(day), isin))
            if not item or item["cash_per_share"] is None:
                raise ValueError(f"No verified ex-date dividend per unit for {isin} on {day}")
            bounds = steady_tax.bounds_for(notices, isin, day, "dividend", e["symbol"])
            for lot in book.lots[isin]:
                lot.dividends += ((bounds, item["cash_per_share"]),)
    if any(fills.values()):
        raise ValueError("Some tax fills lack a paired journal event")
    gains = [g for b in books.values() for g in b.gains]
    years = equity_tax.annual_taxes(gains, equity_tax.fy(start), equity_tax.fy(end), config)
    measured = steady_tax.endpoint(ending_value, start, end, years)
    liquidation = None
    if not any(fracs.values()) and not any(bonuses.values()):
        clone = copy.deepcopy(books)
        ord_cost = 0.0
        for b in clone.values():
            for isin, lots in list(b.lots.items()):
                q = sum(l.units for l in lots)
                if not q:
                    continue
                quote = corporate.quote(root, end, isin)
                if not quote:
                    return {
                        "strategy": measured,
                        "per_fy": years,
                        "tri": reference,
                        "terminal_liquidation": None,
                        "liquidation_gap": f"No terminal quote for {isin}",
                        "tax_revision": config["revision"],
                    }
                px = quote["close"] * (1 - cfg["cost"]["slippage_bps"] / 10000)
                charge = cost("sell", q * px, cfg)
                ord_cost += charge + q * (quote["close"] - px)
                b.sell(isin, end, q, px, charge / q - px * cfg["cost"]["stt"])
        liquidation_taxes = equity_tax.annual_taxes(
            [g for b in clone.values() for g in b.gains],
            equity_tax.fy(start),
            equity_tax.fy(end),
            config,
        )
        liquidation = {
            **steady_tax.endpoint(ending_value - ord_cost, start, end, liquidation_taxes),
            "additional_terminal_costs": ord_cost,
        }
    return {
        "per_fy": years,
        "strategy": measured,
        "tri": reference,
        "terminal_liquidation": liquidation,
        "tax_revision": config["revision"],
        "equal_risk_reference_only": book_kind == "equal_risk",
    }
