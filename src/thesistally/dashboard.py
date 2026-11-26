"""Local market data and journal queries for the terminal."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from . import market, paper
from .journal import journal, metrics, read_call, record_action, run_report
from .paper_quotes import quote_identities


def _published_report(path):
    report = json.loads(path.read_text())
    if path.stem == "stage3-study" and report.get("type") == "stage3_study":
        report["rules"] += " · Superseded"
        report["terminal_summary"] = (
            "Superseded for decisions. This study blocked buys when the upper band was "
            "unknown. See the Stage 3 opening-band revision in Reports.\n\n"
            + report["terminal_summary"]
        )
    if path.stem == "stage3-study-entry-v2" and report.get("type") == "stage3_study":
        report["rules"] += " · Earlier momentum results"
        report["terminal_summary"] = (
            "The independent closing-price review completes the ten momentum windows. See the "
            "monthly momentum review in Reports. The B10 and B5 books are parked.\n\n"
            + report["terminal_summary"]
        )
    return report


def _number(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def parse_quotes(data, symbols):
    quotes = {}
    for symbol in symbols:
        row = data.get(f"NSE:{symbol}")
        if not isinstance(row, dict):
            continue
        price = _number(row.get("last_price"))
        if price is None or price <= 0:
            continue
        ohlc = row.get("ohlc") or {}
        previous = _number(ohlc.get("close"))
        stamp = row.get("last_trade_time") or row.get("timestamp")
        timestamp = None
        if stamp:
            try:
                timestamp = datetime.fromisoformat(stamp)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=market.IST)
            except (TypeError, ValueError):
                timestamp = None
        quotes[symbol] = {
            "price": price,
            "change_pct": (price / previous - 1) * 100 if previous and previous > 0 else None,
            "previous_close": previous,
            "open": _number(ohlc.get("open")),
            "high": _number(ohlc.get("high")),
            "low": _number(ohlc.get("low")),
            "volume": _number(row.get("volume")),
            "timestamp": timestamp.isoformat() if timestamp else None,
        }
    return quotes


def _quote_key(row):
    return row["symbol"] if row.get("series", "EQ") == "EQ" else f"{row['symbol']}-{row['series']}"


def _scan(row):
    return {**dict(row), "regime": json.loads(row["regime"])}


class Dashboard:
    def __init__(self, root: Path, quote_provider: str | None = None):
        selected = (
            os.environ.get("RADAR_QUOTE_PROVIDER", "nse")
            if quote_provider is None
            else quote_provider
        )
        self.quote_provider = selected.strip().lower()
        if self.quote_provider not in {"nse", "kite"}:
            raise ValueError("Choose nse or kite for the terminal quote provider.")
        self.interval = 120 if self.quote_provider == "nse" else 5
        self.root = root.resolve()
        if (self.root / "journal.sqlite").is_file():
            with closing(journal(self.root / "journal.sqlite")):
                pass
        self._archive_lock = threading.Lock()
        self._roster_cache = {}
        self._quote_owners = {}
        self._identity_changed_on = {}
        self._held_isins = frozenset()
        self._archive_key = None
        self._archive = None
        self._quote_state = {
            "provider": self.quote_provider,
            "members": [],
            "quotes": {},
            "fetched_at": None,
            "error": None,
            "interval": self.interval,
            "requested": 0,
        }
        self._activity_lock = threading.Lock()
        self._activity = {"running": False, "message": ""}

    @contextmanager
    def db(self):
        path = self.root / "journal.sqlite"
        if not path.is_file():
            yield None
            return
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def archive(self):
        dates = market.sessions(self.root)
        day = str(dates[-1]) if dates else None
        with self._archive_lock:
            if self._archive is not None and self._archive_key == day:
                return self._archive
            members = []
            if day:
                folder = self.root / "sessions" / day
                frame = market.read_members((folder / "members.csv").read_bytes())
                prices = pd.read_parquet(
                    folder / "bars.parquet",
                    columns=["symbol", "close"],
                    filters=[("day", "=", dates[-1])],
                )
                closes = prices.set_index("symbol").close
                members = [
                    {"symbol": s, "sector": "Index", "close": _number(closes.get(s))}
                    for s in market.BENCHMARKS
                ]
                members.extend(
                    {
                        "symbol": r.symbol,
                        "sector": r.sector,
                        "isin": r.isin,
                        "series": r.series,
                        "close": _number(closes.get(r.symbol)),
                    }
                    for r in frame.itertuples(index=False)
                )
            self._archive = {"session": day, "members": members}
            self._archive_key = day
            return self._archive

    def reports(self):
        folder = self.root / "backtests"
        result = []
        for path in folder.glob("*.json"):
            report = _published_report(path)
            if "variants" in report and "terminal_summary" not in report:
                # The combined Stage 3 report links its machine-readable study inputs.
                continue
            holdout = {}
            if report.get("type") not in ("stage2_baseline", "stage3_study"):
                holdout = next(
                    (f["portfolio"] for f in report["folds"] if f["period"] == "holdout"), {}
                )
            result.append(
                {
                    "id": path.stem,
                    **{k: report[k] for k in ("start", "end", "rules")},
                    "holdout": holdout,
                }
            )
        for path in (self.root / "rankers").glob("*.json"):
            if path.name == "active.json":
                continue
            report = json.loads(path.read_text())
            if not report.get("research"):
                continue
            baseline = Path(report["benchmark_report"]).name
            ref = self.root / "backtests" / baseline
            if ref.is_file():
                dates = json.loads(ref.read_text())
                result.append(
                    {
                        "id": "rank-" + path.stem,
                        "start": dates["start"],
                        "end": dates["end"],
                        "rules": "LightGBM ranker study",
                        "holdout": {},
                    }
                )
        return sorted(result, key=lambda r: (r["end"], r["start"], r["id"]), reverse=True)

    def report(self, report_id):
        if not report_id or Path(report_id).name != report_id or report_id.startswith("."):
            raise KeyError("Unknown report.")
        path = self.root / "backtests" / (report_id + ".json")
        if report_id.startswith("rank-"):
            path = self.root / "rankers" / (report_id[5:] + ".json")
        if not path.is_file():
            raise KeyError("Unknown report.")
        return _published_report(path)

    def overview(self):
        archive = self.archive()
        with self.db() as db:
            row = None
            runs = []
            if db is not None:
                row = db.execute(
                    "SELECT * FROM scans WHERE run='observations' ORDER BY day DESC LIMIT 1"
                ).fetchone()
                runs = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM runs WHERE id<>? ORDER BY id='observations' DESC,id",
                        (paper.ENTRY_SHADOW,),
                    )
                ]
        return {
            **archive,
            "scan": _scan(row) if row else None,
            "runs": runs,
            "reports": self.reports(),
        }

    def calls(self, run):
        with self.db() as db:
            if db is None:
                return {
                    "calls": [],
                    "summary": {
                        "portfolio": metrics([]),
                        "shadow": metrics([]),
                        "by_strategy": {},
                        "run": run,
                    },
                }
            rows = db.execute(
                """SELECT id,day,symbol,strategy,sector,status,entry,stop,target,size,
                              allocation,outcome_r,net,action FROM calls WHERE run=? ORDER BY day DESC,id""",
                (run,),
            )
            return {
                "calls": [dict(x) for x in rows],
                "summary": run_report(db, run),
            }

    def call(self, call_id):
        with self.db() as db:
            if db is None:
                raise market.MarketError("No calls have been journaled yet.")
            return read_call(db, call_id)

    def paper_account(self):
        with self.db() as db:
            if db is None:
                return {"capital": None, "history": [], "trades": [], "comparisons": []}
            db.execute("BEGIN")
            account = paper.portfolio(db)
            self._held_isins = frozenset(
                t["isin"]
                for t in account["trades"]
                if t["status"] == "active" and t["remaining"] > 0
            )
            return account

    def paper_trade(self, call_id, *, comparison=False):
        with self.db() as db:
            if db is None:
                raise market.MarketError("No paper trades have been recorded.")
            db.execute("BEGIN")
            call = read_call(db, call_id)
            if comparison:
                if call["run"] not in ("observations", paper.ENTRY_SHADOW):
                    raise market.MarketError("This call is outside the forward comparison.")
            elif call["run"] != "observations" or call["allocation"] != "portfolio":
                raise market.MarketError("This call is outside the paper account.")
            call["marks"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM paper_position_marks WHERE call_id=? ORDER BY day DESC",
                    (call_id,),
                )
            ]
            return call

    def paper_fills(self, day):
        with self.db() as db:
            if db is None:
                return []
            return [
                dict(row)
                for row in db.execute(
                    """SELECT f.*,c.symbol,coalesce(s.isin,c.isin) AS isin FROM fills f
                   JOIN calls c ON c.id=f.call_id LEFT JOIN fill_securities s ON s.fill_id=f.id
                   WHERE c.run='observations' AND c.allocation='portfolio' AND f.day=?
                   ORDER BY f.id""",
                    (day,),
                )
            ]

    def feedback(self, call_id, decision):
        with closing(journal(self.root / "journal.sqlite")) as db:
            record_action(db, call_id, decision)

    def _current_roster(self, *, all_equities=False):
        """Read a public NSE identity file once per IST day for the terminal only."""
        day = datetime.now(market.IST).date()
        previous = self._roster_cache.get(all_equities)
        if previous and previous[0] == day:
            return previous[1]
        url = market.ARCHIVE + "/equities/EQUITY_L.csv" if all_equities else market.UNIVERSE_URL
        response = httpx.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "ThesisTally/0.1", "Accept": "text/csv,*/*"},
        )
        response.raise_for_status()
        if all_equities:
            frame = market.csv_table(response.content)
            frame.columns = frame.columns.str.strip()
            required = {"SYMBOL": "symbol", "SERIES": "series", "ISIN NUMBER": "isin"}
            if not required.keys() <= set(frame):
                raise market.MarketError("NSE equity list has no security identifiers.")
            frame = frame.rename(columns=required)[list(required.values())]
            frame = frame.apply(lambda column: column.str.strip())
            frame = frame[frame["isin"].str.fullmatch(r"IN[A-Z0-9]{10}")]
            frame = frame[~frame.duplicated(["symbol", "series"], keep=False)]
        else:
            frame = market.read_members(response.content)
        rows = frame.to_dict("records")
        self._roster_cache[all_equities] = (day, rows)
        return rows

    def _kite_quotes(self, symbols, identities):
        with market.Feed(timeout=5) as feed:
            result = feed.get(
                market.KITE + "/quote",
                kite=True,
                attempts=1,
                params=[("i", f"NSE:{s}") for s in symbols],
            ).json()
        if (
            not isinstance(result, dict)
            or result.get("status") != "success"
            or not isinstance(result.get("data"), dict)
        ):
            raise market.MarketError("Kite returned an incomplete quote response.")
        quotes = parse_quotes(result["data"], symbols)
        for symbol, (isin, token) in identities.items():
            if (
                symbol in quotes
                and result["data"][f"NSE:{symbol}"].get("last_trade_time")
                and result["data"][f"NSE:{symbol}"].get("instrument_token") == token
            ):
                quotes[symbol]["isin"] = isin
        return quotes

    def _nse_snapshot(self, symbols, verified, archived, archive_day):
        from . import nse_quotes

        quotes, warning = nse_quotes.fetch_quotes(symbols)
        today = datetime.now(market.IST).date().isoformat()
        for key in list(quotes):
            if key in market.BENCHMARKS:
                continue
            quote = quotes[key]
            identity = verified.get(key)
            historical = archived.get(key, {})
            if not identity or (quote.get("symbol"), quote.get("series")) != (
                identity["symbol"],
                identity["series"],
            ):
                del quotes[key]
                continue
            when = (quote.get("timestamp") or "")[:10]
            prior_isin = historical.get("isin")
            if when < self._identity_changed_on.get(key, "") or (
                when != today and prior_isin and prior_isin != identity["isin"]
            ):
                # An older tick can belong to a previous holder of this symbol.
                del quotes[key]
            elif when == today or (when and when == archive_day and prior_isin == identity["isin"]):
                quote["isin"] = identity["isin"]
        return quotes, warning

    def quotes(self):
        archive = self.archive()
        archived = {_quote_key(r): r for r in archive["members"]}
        members = archive["members"]
        current = []
        warnings = []
        if self.quote_provider == "nse" or not members:
            try:
                current = self._current_roster()
                members = current
            except (httpx.HTTPError, ValueError, OSError):
                prior_day, prior = self._roster_cache.get(False, (archive.get("session"), members))
                members = prior
                warnings.append(
                    f"Using NSE constituents from {prior_day}."
                    if members
                    else "Current NSE constituents unavailable. Showing indices."
                )

        roster = {_quote_key(r): r for r in members}
        held = self._held_isins
        verified = {_quote_key(r): r for r in current}
        if self.quote_provider == "nse" and (
            (members and not current) or held - {r["isin"] for r in current}
        ):
            try:
                for row in self._current_roster(all_equities=True):
                    key = _quote_key(row)
                    if (key not in roster and row["isin"] in held) or roster.get(key, {}).get(
                        "isin"
                    ) == row["isin"]:
                        verified.setdefault(key, row)
            except (httpx.HTTPError, OSError, ValueError):
                warnings.append("NSE security list unavailable for identity checks.")

        symbols = list(dict.fromkeys([*market.BENCHMARKS, *roster, *verified]))
        kite_ids = {}
        if self.quote_provider == "kite" and held and archive.get("session"):
            try:
                ids = quote_identities(self.root / "sessions" / archive["session"])
            except (ValueError, OSError):
                ids = {}
            kite_ids = {s: pair for s, pair in ids.items() if pair[0] in held}
            symbols.extend(s for s in kite_ids if s not in symbols)

        # A cached price belongs to the security that produced it, even on a total outage.
        owners = {
            k: r.get("isin")
            for k, r in (verified if self.quote_provider == "nse" else roster).items()
        }
        owners.update({k: pair[0] for k, pair in kite_ids.items()})
        for key, isin in owners.items():
            if isin and self._quote_owners.get(key) not in (None, isin):
                self._identity_changed_on[key] = market.today().isoformat()
        old = self._quote_state["quotes"]
        cached = {
            k: q
            for k, q in old.items()
            if k in symbols
            and (
                k in market.BENCHMARKS or (owners.get(k) and self._quote_owners.get(k) == owners[k])
            )
        }
        fetched_at = self._quote_state["fetched_at"]
        error = None
        try:
            if self.quote_provider == "nse":
                quotes, partial = self._nse_snapshot(
                    symbols, verified, archived, archive.get("session")
                )
                if partial:
                    warnings.append(partial)
            else:
                quotes = self._kite_quotes(symbols, kite_ids)
            if not quotes:
                raise market.MarketError(
                    f"{self.quote_provider.upper()} returned no usable quotes."
                )
            fetched_at = datetime.now(market.IST).isoformat()
            cached = {k: {**q, "cached": True} for k, q in cached.items() if k not in quotes}
            quotes = {**cached, **quotes}
        except (OSError, ValueError) as exc:
            error = str(exc)
            quotes = cached
        self._quote_owners.update({k: v for k, v in owners.items() if v})
        self._quote_state = {
            "provider": self.quote_provider,
            "interval": self.interval,
            "members": [
                {**r, "symbol": k, "exchange_symbol": r["symbol"]} for k, r in roster.items()
            ],
            "requested": len(symbols),
            "quotes": quotes,
            "fetched_at": fetched_at,
            "error": error,
            "warning": " ".join(warnings) or None,
        }
        return self._quote_state

    def activity(self):
        with self._activity_lock:
            return self._activity.copy()

    def start_daily(self):
        with self._activity_lock:
            if not self._activity["running"]:
                self._activity = {"running": True, "message": "Collecting the completed session"}
                threading.Thread(target=self._daily, daemon=True, name="radar-daily").start()
            return self._activity.copy()

    def start_automatic(self):
        with self._activity_lock:
            if self._activity["running"]:
                return
            self._activity["running"] = True
        threading.Thread(target=self._automatic, daemon=True, name="radar-automatic").start()

    def _automatic(self):
        from .daemon import holidays, scan_after_close
        from .research_worker import start_pending

        message = None
        try:
            start_pending(self.root)
            now = datetime.now(market.IST)
            day = now.date()
            if (
                (now.hour, now.minute) >= (15, 45)
                and holidays(self.root, day)
                and scan_after_close(self.root, day)
            ):
                message = f"{day} scan complete"
        except (OSError, ValueError, sqlite3.Error) as exc:
            message = f"Automatic scan unavailable · {exc}"
        finally:
            with self._activity_lock:
                self._activity["running"] = False
                if message:
                    self._activity["message"] = message

    def _daily(self):
        from .cli import collect_session, scan_session

        message = "Scan interrupted. See the terminal log."
        try:
            day = market.today()
            collect_session(self.root, day, None, lambda _: None)
            result = scan_session(self.root, day, "observations", None)
            message = f"{day} scan complete · {result['detected']} setups"
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            message = (
                f"Scan unavailable · {exc}"
                if isinstance(exc, (ValueError, OSError))
                else "Scan failed. Check the local data and broker session."
            )
        finally:
            with self._activity_lock:
                self._activity = {"running": False, "message": message}
