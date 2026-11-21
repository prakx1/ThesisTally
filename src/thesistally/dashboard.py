"""Local market data and journal queries for the terminal."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import market, paper
from .journal import journal, metrics, read_call, record_action, run_report


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


def _scan(row):
    return {**dict(row), "regime": json.loads(row["regime"])}


class Dashboard:
    interval = 5

    def __init__(self, root: Path):
        self.root = root.resolve()
        if (self.root / "journal.sqlite").is_file():
            with closing(journal(self.root / "journal.sqlite")):
                pass
        self._archive_lock = threading.Lock()
        self._bootstrap_members = []
        self._archive_key = None
        self._archive = None
        self._quote_state = {
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
                    {"symbol": r.symbol, "sector": r.sector, "close": _number(closes.get(r.symbol))}
                    for r in frame.itertuples(index=False)
                )
            self._archive = {"session": day, "members": members}
            self._archive_key = day
            return self._archive

    def reports(self):
        folder = self.root / "backtests"
        result = []
        for path in folder.glob("*.json"):
            report = json.loads(path.read_text())
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
        return json.loads(path.read_text())

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
                    for r in db.execute("SELECT id FROM runs ORDER BY id='observations' DESC,id")
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
                return {"capital": None, "history": [], "trades": []}
            db.execute("BEGIN")
            return paper.portfolio(db)

    def paper_trade(self, call_id):
        with self.db() as db:
            if db is None:
                raise market.MarketError("No paper trades have been recorded.")
            db.execute("BEGIN")
            call = read_call(db, call_id)
            if call["run"] != "observations" or call["allocation"] != "portfolio":
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

    def quotes(self):
        members = self.archive()["members"]
        symbols = list(dict.fromkeys([*market.BENCHMARKS, *(r["symbol"] for r in members)]))
        try:
            with market.Feed(timeout=5) as feed:
                if not members:
                    if not self._bootstrap_members:
                        current = market.read_members(
                            feed.get(market.UNIVERSE_URL, attempts=1).content
                        )
                        self._bootstrap_members = current.symbol.tolist()
                    symbols.extend(self._bootstrap_members)
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
            if not quotes:
                raise market.MarketError("Kite returned no usable quotes.")
            self._quote_state = {
                "quotes": quotes,
                "fetched_at": datetime.now(market.IST).isoformat(),
                "error": None,
                "interval": self.interval,
                "requested": len(symbols),
            }
        except (ValueError, OSError) as exc:
            self._quote_state = {
                **self._quote_state,
                "error": str(exc),
                "requested": len(symbols),
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
