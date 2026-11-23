"""Indian listed-equity capital-gains overlay; no portfolio decisions or broker calls."""

import calendar
import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("revisions") / "listed-equity-tax-v1.json"


def months(day, n):
    year, month = divmod(day.year * 12 + day.month - 1 + n, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def fy(day):
    return day.year - (day.month < 4)


def load_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_bytes())


@dataclass
class Lot:
    units: float
    acquired: date
    basis: float  # per current share, excluding STT
    fmv: float | None = None  # January 2018 fair value per current unit
    dividends: tuple = ()  # (record date bounds, exempt rupees per unit)
    bonus_id: str | None = None
    original_bonus_ids: tuple = ()


def gain(cfg, lot, day, qty, price, fee):
    long = day > months(lot.acquired, cfg["holding_months"])
    basis = lot.basis
    if (
        long
        and day.isoformat() >= cfg["taxable_ltcg_from"]
        and lot.acquired.isoformat() <= cfg["grandfather_day"]
    ):
        if lot.fmv is None:
            raise ValueError(f"Missing grandfathered fair value for purchase {lot.acquired}")
        # Section 55 caps FMV at gross consideration, before transfer expenses.
        basis = max(basis, min(lot.fmv, price))
    return ("long" if long else "short"), qty * (price - fee - basis)


def in_record_window(acquired, sold, bounds, sale_months):
    # Archive notices sometimes give book closure rather than a unique record date.
    # Use an interval only when its two endpoints prove the same classification.
    def eligible(r):
        return months(r, -3) <= acquired <= r <= sold <= months(r, sale_months)

    low, high = bounds
    if any(
        eligible(low + timedelta(days=i)) != eligible(low) for i in range(1, (high - low).days + 1)
    ):
        raise ValueError(f"Exact record date needed between {low} and {high} for {acquired}/{sold}")
    return eligible(low)


class TaxBook:
    def __init__(self, config):
        self.cfg = config
        self.lots = defaultdict(list)
        self.gains = []
        self.bonus_dates = {}
        self.bonus_extra_basis = defaultdict(float)
        self.ignored_dividend_loss = 0.0
        self.deferred_bonus_loss = 0.0

    def add(self, isin, lot):
        if lot.units > 1e-9:
            self.lots[isin].append(lot)

    def record(self, isin, lot, day, qty, price, fee):
        kind, amount = gain(self.cfg, lot, day, qty, price, fee)
        if amount < 0 and lot.dividends:
            exempt = sum(
                per
                for bounds, per in lot.dividends
                if in_record_window(lot.acquired, day, bounds, 3)
            )
            ignored = min(-amount, qty * exempt)
            self.ignored_dividend_loss += ignored
            amount += ignored
        if amount < 0:
            for key in lot.original_bonus_ids:
                bounds, successor, allotted = self.bonus_dates[key]
                if day < allotted or not in_record_window(lot.acquired, day, bounds, 9):
                    continue
                # The denied loss is added only to free shares still held on sale.
                bonus = [b for b in self.lots[successor] if b.bonus_id == key and b.units > 1e-9]
                if bonus:
                    denominator = sum(b.units for b in bonus)
                    for b in bonus:
                        b.basis += -amount / denominator
                elif key in self.bonus_extra_basis:  # allotted shares awaiting trading permission
                    self.bonus_extra_basis[key] -= amount
                else:
                    continue
                self.deferred_bonus_loss -= amount
                amount = 0.0
                break
        self.gains.append((day, kind, amount))

    def sell(self, isin, day, qty, price, fee):
        """Consume in demat FIFO order, retaining acquisition dates through conversions."""
        lots = self.lots[isin]
        remaining = qty
        while remaining > 1e-8:
            if not lots:
                raise ValueError(f"Tax FIFO quantity missing for {isin} on {day}: {remaining}")
            lot = lots[0]
            count = min(remaining, lot.units)
            self.record(isin, lot, day, count, price, fee)
            remaining -= count
            lot.units -= count
            if lot.units < 1e-8:
                lots.pop(0)

    def convert(self, isin, new, ratio, whole, fraction):
        # Assign the single aggregate fraction proportionally to all predecessor
        # lots; remaining lot quantities and basis sum to the full successor units.
        prior = self.lots.pop(isin)
        total = sum(lot.units for lot in prior) * ratio
        if abs(total - whole - fraction) > 1e-6:
            raise ValueError(f"Corporate tax ledger mismatch for {isin}")
        fractional = []
        for lot in prior:
            translated = replace(
                lot,
                basis=lot.basis / ratio,
                fmv=None if lot.fmv is None else lot.fmv / ratio,
                dividends=tuple((b, v / ratio) for b, v in lot.dividends),
            )
            qty = lot.units * ratio
            self.add(new, replace(translated, units=qty * whole / total))
            if fraction:
                fractional.append(replace(translated, units=qty * fraction / total))
        return fractional


def annual_taxes(gains, first_fy, last_fy, cfg):
    """Set off LT losses against LT gains; ST losses against either. Oldest carry expires first."""
    if first_fy < cfg["from_fy"] or last_fy > cfg["through_fy"]:
        raise ValueError("Uncovered tax year; update the versioned rate table")
    bands = defaultdict(lambda: defaultdict(float))
    totals = defaultdict(lambda: defaultdict(float))
    for day, kind, amount in gains:
        year = fy(day)
        rule = next(r for r in reversed(cfg["rates"]) if r["from"] <= day.isoformat())
        totals[year][kind] += amount
        if rule[kind]:
            bands[year][(kind, rule[kind])] += amount
    carry = []
    result = {}
    for year in range(first_fy, last_fy + 1):
        fiscal = next(r for r in reversed(cfg["fiscal_rules"]) if r["from_fy"] <= year)
        net = bands[year]
        gains_left = {k: v for k, v in net.items() if v > 0}
        carry = [loss for loss in carry if year - loss[0] <= cfg["loss_carry_years"]]
        for (kind, _), value in net.items():
            if value < 0:
                carry.append([year, kind, -value])
        # Within the same rate regime losses and gains have already netted.
        # Apply LT before flexible ST and prefer higher-taxed eligible gains.
        for loss in sorted(carry, key=lambda l: (l[1] != "long", l[0])):
            for key in sorted(gains_left, key=lambda k: k[1], reverse=True):
                if loss[1] == "long" and key[0] != "long":
                    continue
                used = min(loss[2], gains_left[key])
                loss[2] -= used
                gains_left[key] -= used
        exempt = fiscal["long_exemption"]
        tax = {"short": 0.0, "long": 0.0}
        for (kind, rate), value in sorted(gains_left.items(), key=lambda k: k[0][1], reverse=True):
            if kind == "long":
                deduction = min(value, exempt)
                exempt -= deduction
                value -= deduction
            tax[kind] += rate * value
        base = sum(tax.values())
        result[year] = {
            "realised_short_gain": totals[year]["short"],
            "realised_long_gain": totals[year]["long"],
            "short_tax": tax["short"],
            "long_tax": tax["long"],
            "cess": base * fiscal["cess"],
            "tax": base * (1 + fiscal["cess"]),
            "lt_exemption_used": fiscal["long_exemption"] - exempt,
            "short_loss_carried": sum(x[2] for x in carry if x[1] == "short"),
            "long_loss_carried": sum(x[2] for x in carry if x[1] == "long"),
        }
    return result


def investor_return(start, end, proceeds, taxes, capital):
    """Dated annual rate on the investor's own cash, with taxes paid outside the portfolio."""
    flows = defaultdict(float)
    flows[start] -= capital
    for year, year_tax in taxes.items():
        due = min(date(year + 1, 3, 31), end)
        flows[due] -= year_tax["tax"]
    flows[end] += proceeds
    # There is one positive cash flow at the end. The sum of the earlier
    # negative cash flows compounded to that date is strictly monotone in rate.
    if flows[end] <= 0 or end <= start:
        raise ValueError("Positive terminal proceeds over an observed interval required")
    cash = sorted((d, v) for d, v in flows.items() if abs(v) > 1e-9)
    earlier = [((end - d).days / 365, -v) for d, v in cash if d < end]
    if not earlier or any(v < 0 for _, v in earlier):
        raise ValueError("Investor cash flows include an unsupported interim distribution")

    def final_cost(log_growth):
        return sum(v * math.exp(years * log_growth) for years, v in earlier)

    low, high = -1.0, 1.0
    while final_cost(high) < flows[end]:
        high *= 2
    while final_cost(low) > flows[end]:
        low *= 2
    for _ in range(96):
        mid = (high + low) / 2
        if final_cost(mid) < flows[end]:
            low = mid
        else:
            high = mid
    return {
        "investor_xirr_pct": 100 * math.expm1((low + high) / 2),
        "investor_cash_flows": [{"day": str(d), "net_inr": v} for d, v in cash],
    }
