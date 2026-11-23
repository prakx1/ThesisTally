"""Entry coverage from the same dated candidates and order dispositions as the B replay."""

from collections import Counter
from types import SimpleNamespace

from .journal import exclusion
from .market import MarketError

UNKNOWN_FILTERS = {
    "surveillance_unknown",
    "results_unknown",
    "results_window_unknown",
    "data_quarantined",
}
CENSUS = """
CREATE TABLE IF NOT EXISTS dip_setup_census (
    run TEXT NOT NULL REFERENCES runs(id), day TEXT NOT NULL, isin TEXT NOT NULL,
    symbol TEXT NOT NULL, eligibility_reason TEXT, independent_veto TEXT,
    PRIMARY KEY (run, day, isin)
)
"""


def known_veto(candidate, cfg, regime):
    # Optimistic values are used for reason attribution only. Neither the recorded
    # features nor actual order eligibility are modified. A known veto dominates a
    # missing field when attributing a block to incomplete data.
    features = candidate.features.copy()
    if features.get("surveillance") == "unknown":
        features["surveillance"] = False
    features.update(quarantined=False, earnings_coverage="known")
    if cfg["filters"].get("earnings_blackout_mode") == "holding_period":
        horizon = features.get("holding_blackout_days")
        if horizon is None:
            # With no known horizon an announced meeting isn't a proved veto.
            features["holding_blackout_days"] = 0
            horizon = 0
        features["meeting_coverage_days"] = horizon
    probe = SimpleNamespace(**{**vars(candidate), "features": features})
    return exclusion(probe, cfg, regime)


def capture(db, run, day, candidates, cfg, regime):
    """Capture all raw detections, including those absent from the independent call book."""
    db.execute(CENSUS)
    db.execute("""
        CREATE TABLE IF NOT EXISTS dip_funding_veto (
          run TEXT NOT NULL, day TEXT NOT NULL, isin TEXT NOT NULL, reason TEXT NOT NULL,
          PRIMARY KEY(run,day,isin), FOREIGN KEY(run,day,isin) REFERENCES dip_setup_census(run,day,isin))
    """)
    observations = []
    for c in candidates:
        reason = exclusion(c, cfg, regime)
        independent = known_veto(c, cfg, regime) if reason in UNKNOWN_FILTERS else None
        observations.append((run, day, c.isin, c.symbol, reason, independent))
    db.executemany("INSERT INTO dip_setup_census VALUES (?,?,?,?,?,?)", observations)


def opening_disposition(reason):
    if not reason:
        raise MarketError("A B order without an opening disposition needs investigation")
    if reason.startswith("Corporate action requires reconciliation"):
        return "data", "unresolved_corporate_action"
    if reason.startswith("Open above signal close"):
        return "rule", "max_chase"
    reasons = {
        "Opening price at or below the planned stop": ("rule", "open_at_or_below_stop"),
        "Opening fill crossed the planned stop": ("rule", "fill_at_or_below_stop"),
        "Fill stop distance out of bounds": ("rule", "fill_stop_bounds"),
        "Verified upper band locked at open": ("market", "verified_upper_band_lock"),
        "No traded volume at open": ("market", "zero_traded_volume"),
        "Suspended before the original orders could fill": ("market", "security_suspended"),
        "Cannot fund one share at the opening risk": ("allocation", "opening_quantity_zero"),
        "Cannot fund one share at the planned risk": ("allocation", "planned_quantity_zero"),
        "No traded price for the original ISIN": ("data", "missing_traded_quote"),
        "Missing valid opening quote": ("data", "missing_opening_quote"),
        "Missing positive signal ATR(14)": ("data", "missing_atr"),
        "Quarantined daily price": ("data", "quarantined_price"),
    }
    if reason not in reasons:
        raise MarketError(f"Unclassified B opening disposition: {reason}")
    return reasons[reason]


def entry_coverage(db, run, scheduled_last_day, selected):
    rows = db.execute(
        """
        SELECT d.day,d.eligibility_reason,coalesce(d.independent_veto,v.reason),c.allocation,c.status,c.buy,
               (SELECT e.detail FROM events e WHERE e.call_id=c.id AND e.kind=c.status
                     ORDER BY e.id DESC LIMIT 1),
               EXISTS(SELECT 1 FROM events e WHERE e.call_id=c.id AND e.kind='band_unknown')
          FROM dip_setup_census d LEFT JOIN calls c
            ON (c.run,c.day,c.isin)=(d.run,d.day,d.isin)
          LEFT JOIN dip_funding_veto v ON (v.run,v.day,v.isin)=(d.run,d.day,d.isin)
         WHERE d.run=? ORDER BY d.day,d.isin
        """,
        (run,),
    )
    raw = pending = filled = flagged = 0
    pre_order, data, other = Counter(), Counter(), Counter()
    for day, veto, independent, allocation, status, buy, reason, unknown_band in rows:
        raw += 1
        if day == scheduled_last_day:
            pending += 1
        elif veto in UNKNOWN_FILTERS and independent is None:
            data[veto] += 1
        elif veto or (allocation is not None and allocation != selected):
            pre_order[independent or veto or allocation] += 1
        elif allocation != selected:
            raise MarketError(f"Missing eligible B order for {day} in {run}")
        elif buy is not None:
            filled += 1
            flagged += unknown_band
        elif status == "confirmed":
            pending += 1
        else:
            category, label = opening_disposition(reason)
            (data if category == "data" else other)[label] += 1
    planned = filled + sum(other.values()) + sum(data.values())
    if raw != planned + pending + sum(pre_order.values()):
        raise MarketError("B signal census and order outcomes do not reconcile")
    fraction = sum(data.values()) / planned if planned else None
    return {
        "raw_signals": raw,
        "planned_entries": planned,
        "filled": filled,
        "band_unknown_fills": flagged,
        "blocked_data": sum(data.values()),
        "blocked_other": sum(other.values()),
        "data_reasons": dict(sorted(data.items())),
        "other_reasons": dict(sorted(other.items())),
        "pre_order_exclusions": dict(sorted(pre_order.items())),
        "censored": pending,
        "data_blocked_fraction": fraction,
        "coverage_excluded": 10 * sum(data.values()) > planned,
    }
