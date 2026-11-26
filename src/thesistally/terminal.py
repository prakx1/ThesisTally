"""Live market monitor and research journal in one terminal."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, time

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from . import kite_session, market
from .dashboard import Dashboard
from .paper_quotes import paper_live_view, quote_freshness
from .stock_screen import StockReportScreen

LABELS = {
    "trend_pullback": "Trend pullback",
    "high_momentum": "52-week high",
    "vcp_breakout": "VCP breakout",
}
QUOTE_FEEDS = {"nse": "NSE Official", "kite": "Kite"}


def feed_label(provider):
    return "NSE Official · 1–3 min delayed" if provider == "nse" else "Kite"


def price_for_display(symbol, member, archive, quote, final_at):
    """Choose a single source for the entire row, including the stock detail."""
    if final_at is None or archive.get("close") is None:
        return quote
    if symbol not in market.BENCHMARKS and (quote or member is not archive):
        isin = member.get("isin") or quote.get("isin")
        series = archive.get("series", "EQ")
        if (
            not isin
            or isin != archive.get("isin")
            or quote.get("isin", isin) != isin
            or member.get("series", series) != series
            or quote.get("series", series) != series
        ):
            return quote
    if quote:
        stamp = quote.get("timestamp")
        if not stamp or datetime.fromisoformat(stamp) > final_at:
            return quote
    return {"price": archive["close"], "recorded": final_at.date().isoformat()}


def num(value, decimals=2):
    return "—" if value is None else f"{value:,.{decimals}f}"


def pct(value):
    return num(100 * value if value is not None else None)


def stamp(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(market.IST).strftime("%d %b %H:%M:%S")


def report_text(report):
    if report.get("type") in ("stage2_baseline", "stage3_study"):
        return report["terminal_summary"]
    if "comparable_model" in report:
        lines = [report["research"], "", "Window        Training rows    Comparison"]
        lines.extend(
            f"{w['period']:<12} {w['training_rows']:>10}    {w['status'].replace('_', ' ')}"
            for w in report["windows"]
        )
        for k, title in (("comparable_rs", "Relative strength"), ("comparable_model", "LightGBM")):
            a = report[k]
            lines.append(f"{title}: {a['closed']} closed, mean {num(a['expectancy_r'])}R")
        lines.extend(
            [
                "",
                f"Active model {report['active_model']}",
                "",
                "Strategy outcomes by market condition",
            ]
        )
        for g in report["regime_analysis"]:
            lines.append(
                f"{g['strategy']} · {g['book']} · {g['group']} {g['bucket']} · {g['closed']} closed · {num(g['expectancy_r'])}R"
            )
        return "\n".join(lines)
    lines = [f"{report['start']} to {report['end']}   ·   {report['rules']}", ""]
    if report.get("parameters", {}).get("mode") == "walk_forward":
        lines.extend(
            [
                "Window        Closed   Win %   Mean R     PF   Drawdown %   Exposure %   CAGR %   Nifty 200 %"
            ]
        )
        for fold in report["folds"]:
            row = fold["portfolio"]

            lines.append(
                f"{fold['period']:<12} {row['closed']:>6} {pct(row.get('win_rate')):>7} "
                f"{num(row['expectancy_r']):>8} {num(row['profit_factor']):>6} "
                f"{pct(row.get('max_drawdown')):>12} {num(row.get('exposure_pct')):>12} "
                f"{pct(row.get('cagr')):>8} {pct(row.get('nifty_200_cagr')):>12}"
            )
        lines.append("")
    for fold in report["folds"]:
        lines.extend([fold["period"].upper() + f"  {fold['start']} to {fold['end']}", ""])
        if fold.get("valuation_gap"):
            gap = fold["valuation_gap"]
            lines.append(f"Incomplete from {gap['day']} · {gap['symbol']} · {gap['reason']}")
        for title, stats in [
            ("Portfolio", fold["portfolio"]),
            ("Shadow", fold["shadow"]),
            *fold["by_strategy"].items(),
        ]:
            lines.append(
                f"{LABELS.get(title, title):<16} {stats['closed']} closed   "
                f"{num(stats['expectancy_r'])} R   PF {num(stats['profit_factor'])}   ₹{num(stats['net_inr'])}"
            )
        lines.append("")
    if report.get("qualification"):
        lines.append("Out-of-sample qualification")
        for strategy, gate in report["qualification"].items():
            lines.append(LABELS.get(strategy, strategy))
            for name, row in gate["criteria"].items():
                status = "pass" if row["passed"] else "fail"
                lines.append(
                    f"  {name:<26} {row['value']}  {row['operator']} {row['threshold']}  {status}"
                )
        lines.append("")
    for key, passed in report["passes_measured_thresholds"].items():
        lines.append(f"{LABELS.get(key, key)}   {'Qualified' if passed else 'Research only'}")
    return "\n".join(lines)


def paper_price_source(mark, provider):
    source, as_of = mark["source"], mark["as_of"]
    if as_of and source in ("Live", "Delayed", "Last quote"):
        return f"{source} · {QUOTE_FEEDS[provider]} · {stamp(as_of)} IST"
    return f"{source} {as_of}" if as_of else source


def call_text(c, *, paper_mark=None, provider="kite"):
    plan_target = c.get("planned_target", c["target"])
    plan_size = c.get("planned_size", c["size"])
    execution = []
    if c["buy"] is not None:
        execution.append(
            f"Fill ₹{num(c['buy'])}   Stop ₹{num(c['stop'])}   Target ₹{num(c['target'])}"
            f"   Shares {c['size']}   Remaining {c['remaining']}"
        )
    profit = f"Net ₹{num(c['net'])}"
    if paper_mark and c["status"] == "active":
        profit = (
            f"Snapshot P&L ₹{num(paper_mark['pnl'])} · {paper_price_source(paper_mark, provider)}"
        )
    lines = [
        f"{c['symbol']}   ·   {LABELS.get(c['strategy'], c['strategy'])}   ·   {c['day']}",
        f"{c['sector']}   /   {c['status']}   /   {c['allocation']}   /   {c['action']}",
        "",
        f"Plan ₹{num(c['entry'])}   Stop ₹{num(c['stop'])}   Target ₹{num(plan_target)}   Shares {plan_size}",
        *execution,
        f"{profit}       Outcome {num(c['outcome_r'])} R       Held {c['age']} sessions",
        f"MAE {num(c['mae'])} R       MFE {num(c['mfe'])} R",
        "",
        "Events",
    ]
    lines.extend(f"{e['day']}  {e['kind']}  {e['detail']}" for e in c["events"])
    for v in c.get("ai_verdicts", []):
        lines.extend(["", f"Shadow research · {v['decision']} · {v['summary']}"])
    for report in c.get("research_reports", []):
        lines.append(f"Rating {report['rating']} · {report['report_path']}")
    if c["fills"]:
        lines.extend(["", "Fills"])
        lines.extend(
            f"{f['day']}  {f['side']} {f['qty']} at ₹{num(f['price'])}   Fees ₹{num(f['fees'])}"
            for f in c["fills"]
        )
    if c["entitlements"]:
        lines.extend(["", "Entitlements"])
        lines.extend(f"{x['day']}  ₹{num(x['amount'])}" for x in c["entitlements"])
    if c.get("marks"):
        lines.extend(
            [
                "",
                "Daily position marks",
                "Session       Symbol       Held     Close ₹      Value ₹       Net P&L ₹",
            ]
        )
        for mark in c["marks"]:
            close = num(mark["close"]) if mark["close"] is not None else "Frozen"
            lines.append(
                f"{mark['day']}    {mark['symbol']:<12} {mark['qty']:>4}    {close:>8}"
                f"   {num(mark['value']):>10}   {num(mark['pnl']):>10}"
            )
    for field in ("features", "regime"):
        lines.extend(["", field.title(), json.dumps(c[field], indent=2, ensure_ascii=False)])
    return "\n".join(lines)


class Detail(ModalScreen):
    BINDINGS = (("escape", "dismiss", "Close"),)
    DEFAULT_CSS = """
    Detail { align: center middle; }
    Detail > Vertical { width: 94%; height: 90%; background: $surface; border: round $accent; padding: 1 2; }
    Detail VerticalScroll { height: 1fr; }
    Detail Horizontal { height: 3; margin-top: 1; }
    Detail Button { margin-right: 1; }
    """

    def __init__(self, title, body, feedback=False):
        super().__init__()
        self.heading, self.body = title, body
        self.feedback = feedback

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, classes="kicker", markup=False)
            with VerticalScroll():
                yield Static(self.body, markup=False)
            with Horizontal():
                if self.feedback:
                    yield Button("Mark taken", id="taken")
                    yield Button("Mark skipped", id="skip")
                yield Button("Close", id="close-detail")

    @on(Button.Pressed)
    def close_detail(self, event):
        decision = event.button.id
        self.dismiss(decision if decision in ("taken", "skip") else None)


class KiteControls(Horizontal):
    DEFAULT_CSS = """
    KiteControls { height: auto; align-vertical: middle; padding: 0 1; }
    KiteControls Static { width: 1fr; height: auto; }
    KiteControls #kite-status.connected { color: #7ee787; text-style: bold; }
    KiteControls #kite-login { display: none; }
    KiteControls Button { min-width: 0; margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static("Kite · Checking…", id="kite-status", markup=False)
        yield Button(
            "Log in to Kite", id="kite-login", compact=True, tooltip="F6 · Open Kite login"
        )
        yield Button("Check status", id="kite-check", compact=True, tooltip="F5 · Check session")

    def on_mount(self):
        self.check()
        self.set_interval(60, self.check)

    @on(Button.Pressed, "#kite-check")
    @work(group="kite-status")
    async def check(self):
        button = self.query_one("#kite-check", Button)
        if button.disabled:
            return
        button.disabled = True
        status = self.query_one("#kite-status", Static)
        try:
            result = await asyncio.to_thread(kite_session.check_status)
        except Exception:  # noqa: BLE001 - transport errors may contain private request details
            result = "Status unavailable"
        finally:
            button.disabled = False
        checked = datetime.now(market.IST).strftime("%H:%M IST")
        connected = result == kite_session.CONNECTED
        status.set_class(connected, "connected")
        self.query_one("#kite-login", Button).display = not connected
        label = "Connected to Kite" if connected else f"Kite · {result}"
        status.update(f"{label} · checked {checked}")

    @on(Button.Pressed, "#kite-login")
    @work(group="kite-login")
    async def login(self):
        button = self.query_one("#kite-login", Button)
        if not button.display or button.disabled:
            return
        button.disabled = True
        try:
            message = await asyncio.to_thread(kite_session.open_login)
        except market.MarketError as exc:
            self.notify(str(exc), title="Kite login", severity="warning", markup=False)
        except Exception:  # noqa: BLE001 - browser errors may contain private login URLs
            self.notify("Could not open Kite login.", title="Kite login", severity="warning")
        else:
            self.notify(message, title="Kite login", markup=False)
        finally:
            button.disabled = False


class RadarTerminal(App):
    TITLE = "ThesisTally"
    SUB_TITLE = "Market monitor"
    CSS = """
    Screen { background: #101923; }
    Header, Footer { background: #182932; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    DataTable { height: 1fr; }
    DataTable > .datatable--header { background: #20313b; color: #c5ded9; text-style: bold; }
    DataTable > .datatable--cursor { background: #23564f; color: white; }
    .kicker { color: #7ce0bd; text-style: bold; height: auto; padding: 0 1; }
    .note { color: #bed2d7; height: auto; padding: 0 1; }
    #indices { height: auto; padding: 0 1; background: #162b30; color: #a5efd4; }
    #activity { height: auto; color: #f2d69c; padding: 0 1; }
    #paper-trades, #entry-comparison { height: 2fr; }
    #paper-history { height: 1fr; }
    Input, Select { height: 3; }
    """
    BINDINGS = (
        ("1", "pane('prices')", "Prices"),
        ("2", "pane('calls-pane')", "Calls"),
        ("3", "pane('reports-pane')", "Reports"),
        ("4", "pane('paper-pane')", "Paper"),
        ("v", "compare_entries", "Entry rules"),
        ("slash", "search", "Find"),
        ("r", "reload", "Refresh"),
        ("d", "daily", "Daily scan"),
        Binding("f5", "check_kite", "Check Kite status", show=False),
        Binding("f6", "login_kite", "Log in to Kite", show=False),
        ("q", "quit", "Quit"),
    )

    def __init__(self, dashboard: Dashboard):
        super().__init__()
        self.data = dashboard
        self.kite_controls = KiteControls() if dashboard.quote_provider == "kite" else None
        self.paper_data = None
        self.overview_data = {}
        self.quote_data = {"provider": dashboard.quote_provider}
        self.displayed_prices = {}
        self.stock_research_running = set()
        self.run_name = "observations"
        self.run_options = []
        self.loading_local = self.loading_quotes = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        if self.kite_controls is not None:
            yield self.kite_controls
        yield Static("NSE   ·   Connecting", id="indices", markup=False)
        with TabbedContent(id="tabs"):
            with TabPane("Prices", id="prices"):
                yield Static(
                    f"Connecting to {QUOTE_FEEDS[self.data.quote_provider]}…",
                    id="quote-status",
                    classes="note",
                    markup=False,
                )
                yield Input(placeholder="Filter symbol or sector", id="filter")
                yield Static(
                    "Select a stock and press Enter for its research report", classes="note"
                )
                yield DataTable(id="prices-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Calls", id="calls-pane"):
                yield Select(
                    [("Observations", "observations")],
                    value="observations",
                    allow_blank=False,
                    id="run-select",
                )
                yield Static("Loading journal…", id="call-count", classes="note", markup=False)
                yield DataTable(id="calls-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Reports", id="reports-pane"):
                yield Static("", id="run-result", classes="kicker", markup=False)
                yield Static(
                    "Saved backtests · Enter to inspect folds and strategy results",
                    id="report-count",
                    classes="note",
                )
                yield DataTable(id="reports-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Paper Trading", id="paper-pane"):
                yield Static("", id="paper-estimate", classes="kicker", markup=False)
                yield Static(
                    "Loading paper account…", id="paper-balance", classes="note", markup=False
                )
                yield Static("", id="paper-status", classes="note", markup=False)
                yield DataTable(id="paper-trades", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="entry-comparison", cursor_type="row", zebra_stripes=True)
                yield Static("Daily account history", classes="kicker")
                yield DataTable(id="paper-history", cursor_type="row", zebra_stripes=True)
        yield Static("", id="activity", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#prices-table", DataTable).add_columns(
            "Symbol", "Price ₹", "Day %", "Open", "High", "Low", "Exchange time IST"
        )
        self.query_one("#calls-table", DataTable).add_columns(
            "Symbol",
            "Date",
            "Setup",
            "Book",
            "Status",
            "Entry ₹",
            "Stop ₹",
            "Target ₹",
            "Qty",
            "Net R",
        )
        self.query_one("#reports-table", DataTable).add_columns(
            "From", "Through", "Closed", "Holdout R", "Profit factor", "Net ₹", "Rules version"
        )
        self.query_one("#paper-trades", DataTable).add_columns(
            "Call",
            "Symbol",
            "Status",
            "Planned",
            "Held",
            "Mark ₹",
            "Net P&L ₹",
            "Price source",
            "Entry plan ₹",
            "Fill ₹",
        )
        comparison = self.query_one("#entry-comparison", DataTable)
        comparison.add_columns(
            "Signal",
            "Symbol",
            "Setup",
            "Allocation",
            "Original",
            "Shadow",
            "Original fill ₹",
            "Shadow fill ₹",
            "Original net R",
            "Shadow net R",
        )
        comparison.display = False
        self.query_one("#paper-history", DataTable).add_columns(
            "Session", "Equity ₹", "Cash ₹", "Holdings ₹", "Day P&L ₹", "Total P&L ₹"
        )
        self.query_one("#prices-table").focus()
        self.action_reload()
        self.data.start_automatic()
        self.set_interval(300, self.data.start_automatic)
        self.set_interval(self.data.interval, self.poll_quotes)
        self.set_interval(10, self.poll_local)
        self.set_interval(1, self.update_activity)

    def update_activity(self):
        self.query_one("#activity", Static).update(self.data.activity()["message"])

    def on_app_focus(self):
        if self.kite_controls is not None and self.kite_controls.is_mounted:
            self.kite_controls.check()

    def action_check_kite(self):
        if self.kite_controls is not None:
            self.kite_controls.check()

    def action_login_kite(self):
        if self.kite_controls is not None:
            self.kite_controls.login()

    def action_pane(self, pane):
        self.query_one("#tabs", TabbedContent).active = pane
        self.query_one(
            {
                "prices": "#prices-table",
                "calls-pane": "#calls-table",
                "reports-pane": "#reports-table",
                "paper-pane": "#entry-comparison"
                if self.query_one("#entry-comparison").display
                else "#paper-trades",
            }[pane],
            DataTable,
        ).focus()

    def action_compare_entries(self):
        comparison = self.query_one("#entry-comparison", DataTable)
        comparison.display = not comparison.display
        self.query_one("#paper-trades").display = not comparison.display
        self.action_pane("paper-pane")

    def action_search(self):
        self.action_pane("prices")
        self.query_one("#filter", Input).focus()

    @on(Input.Submitted, "#filter")
    def enter_prices(self):
        table = self.query_one("#prices-table", DataTable)
        symbol = self.query_one("#filter", Input).value.strip().upper()
        if symbol and not table.row_count:
            # A copied report archive can be read without a quote feed or market snapshot.
            folders = (self.data.root / "research").glob("*/")
            if not symbol.isdigit() and any(folder.name == symbol for folder in folders):
                self.push_screen(StockReportScreen(symbol, ""))
            else:
                self.notify("No matching stock or saved report.", severity="information")
            return
        table.focus()

    def action_reload(self):
        self.poll_local()
        self.poll_quotes()

    def poll_quotes(self):
        if not self.loading_quotes:
            self.loading_quotes = True
            self.fetch_quotes()

    @work(group="quotes")
    async def fetch_quotes(self):
        try:
            payload = await asyncio.to_thread(self.data.quotes)
            self.quote_data = {"provider": self.data.quote_provider, **payload}
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.quote_data = {**self.quote_data, "error": str(exc)}
        finally:
            self.loading_quotes = False
        self.render_prices()
        if self.paper_data is not None:
            self.render_paper_estimate()

    def poll_local(self):
        if not self.loading_local:
            self.loading_local = True
            self.fetch_local()

    @work(group="journal")
    async def fetch_local(self):
        run = self.run_name
        try:
            overview = await asyncio.to_thread(self.data.overview)
            result = await asyncio.to_thread(self.data.calls, run)
            self.overview_data = overview
            scan = overview["scan"]
            if scan:
                self.sub_title = f"{scan['day']}   ·   {scan['count']} setups   ·   simulated calls"
            runs = list(dict.fromkeys(["observations", *overview["runs"]]))
            if runs != self.run_options:
                select = self.query_one("#run-select", Select)
                select.set_options(
                    [("Observations" if r == "observations" else r, r) for r in runs]
                )
                select.value = self.run_name if self.run_name in runs else "observations"
                self.run_options = runs
            if run == self.run_name:
                stats = result["summary"]["portfolio"]
                self.query_one("#run-result", Static).update(
                    f"{run}  ·  Portfolio  {stats['closed']} closed   {num(stats['expectancy_r'])} R   "
                    f"PF {num(stats['profit_factor'])}   Net ₹{num(stats['net_inr'])}"
                )
                self.query_one("#call-count", Static).update(
                    f"{len(result['calls'])} calls   ·   {stats['active']} open portfolio calls   ·   Net ₹{num(stats['net_inr'])}   ·   Enter for detail"
                )
                self.rows(
                    "calls-table",
                    [
                        (
                            str(c["id"]),
                            [
                                c["symbol"],
                                c["day"],
                                LABELS.get(c["strategy"], c["strategy"]),
                                c["allocation"],
                                c["status"],
                                num(c["entry"]),
                                num(c["stop"]),
                                num(c["target"]),
                                str(c["size"]),
                                num(c["outcome_r"]),
                            ],
                        )
                        for c in result["calls"]
                    ],
                )
            self.query_one("#report-count", Static).update(
                f"{len(overview['reports'])} saved backtests · Enter to inspect folds and strategy results"
                if overview["reports"]
                else "No saved backtests yet."
            )
            self.rows(
                "reports-table",
                [
                    (
                        r["id"],
                        [
                            r["start"],
                            r["end"],
                            str(r["holdout"].get("closed", "—")),
                            num(r["holdout"].get("expectancy_r")),
                            num(r["holdout"].get("profit_factor")),
                            num(r["holdout"].get("net_inr")),
                            r["rules"],
                        ],
                    )
                    for r in overview["reports"]
                ],
            )
            self.render_paper(await asyncio.to_thread(self.data.paper_account))
            self.render_prices()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.notify(str(exc), severity="warning")
        finally:
            self.loading_local = False
            if run != self.run_name:
                self.poll_local()

    def render_paper(self, account):
        self.paper_data = account
        history, trades = account["history"], account["trades"]
        balance = self.query_one("#paper-balance", Static)
        if history:
            last = history[0]
            balance.update(
                f"Recorded close {last['day']}   ·   Equity ₹{num(last['equity'])}   ·   "
                f"Cash ₹{num(last['cash'])}   ·   Total P&L ₹{num(last['total_pnl'])}"
            )
        else:
            balance.update("Awaiting the first completed observation scan")
        pending = sum(t["status"] == "confirmed" for t in trades)
        held = sum(t["status"] == "active" for t in trades)
        self.query_one("#paper-status", Static).update(
            f"{pending} funded plans awaiting entry   ·   {held} open   ·   "
            "V switches funded trades / all entry comparisons   ·   Enter for detail"
        )
        self.render_paper_estimate()
        self.rows(
            "entry-comparison",
            [
                (
                    f"{c['id']}/{c['shadow_id']}",
                    [
                        c["day"],
                        c["symbol"],
                        LABELS.get(c["strategy"], c["strategy"]),
                        c["allocation"],
                        c["status"],
                        c["shadow_status"],
                        num(c["buy"]),
                        num(c["shadow_buy"]),
                        num(c["outcome_r"]),
                        num(c["shadow_r"]),
                    ],
                )
                for c in account["comparisons"]
            ],
        )
        self.rows(
            "paper-history",
            [
                (
                    h["day"],
                    [
                        h["day"],
                        num(h["equity"]),
                        num(h["cash"]),
                        num(h["invested"]),
                        num(h["daily_pnl"]),
                        num(h["total_pnl"]),
                    ],
                )
                for h in history
            ],
        )

    def render_paper_estimate(self):
        view = paper_live_view(self.paper_data, self.quote_data)
        estimate = self.query_one("#paper-estimate", Static)
        held, quoted, live = view["held_count"], view["quoted_count"], view["live_count"]
        delayed = view["delayed_count"]
        provider = self.quote_data["provider"]
        feed = QUOTE_FEEDS[provider]
        if self.quote_data.get("error"):
            feed += " unavailable"
        estimate.display = held > 0
        if held and not quoted:
            estimate.update(
                f"No usable quote for open holdings · {feed} · see recorded marks below"
            )
        elif held:
            if live == held:
                source = "Live estimate"
            elif delayed == held:
                source = "Delayed estimate"
            elif quoted == held and not (live or delayed):
                source = "Last quote estimate"
            else:
                parts = [
                    (live, "live"),
                    (delayed, "delayed"),
                    (quoted - live - delayed, "last quote"),
                    (held - quoted, "journal"),
                ]
                source = "Mixed marks · " + ", ".join(f"{n} {label}" for n, label in parts if n)
            balance = (
                f"Total P&L ₹{num(view['total_pnl'])} · Equity ₹{num(view['equity'])}"
                if view["total_pnl"] is not None and view["equity"] is not None
                else "Portfolio estimate unavailable"
            )
            holdings_day = self.paper_data["history"][0]["day"]
            estimate.update(
                f"{source} · {balance} · Holdings at {holdings_day} close"
                f" · {feed} · Oldest quote {stamp(view['as_of'])} IST"
            )
        rows = []
        for t in self.paper_data["trades"]:
            mark = view["positions"][t["id"]]
            status = {"confirmed": "Awaiting entry", "active": "Open"}.get(
                t["status"], t["status"].replace("_", " ").capitalize()
            )
            if t["pending_exit"] and t["status"] == "active":
                status = "Exit pending"
            rows.append(
                (
                    str(t["id"]),
                    [
                        str(t["id"]),
                        t["symbol"],
                        status,
                        str(t["size"]),
                        str(t["remaining"]),
                        num(mark["price"]),
                        num(mark["pnl"]),
                        paper_price_source(mark, provider),
                        num(t["entry"]),
                        num(t["buy"]),
                    ],
                )
            )
        self.rows("paper-trades", rows)

    def rows(self, widget, rows):
        table = self.query_one("#" + widget, DataTable)
        if list(table.rows) == [key for key, _ in rows]:
            for key, values in rows:
                for column, value in zip(table.columns, values, strict=True):
                    if table.get_cell(key, column) != value:
                        table.update_cell(key, column, value, update_width=True)
            return
        old_row = table.cursor_row
        selected = None
        if 0 <= old_row < table.row_count:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        for key, values in rows:
            table.add_row(*values, key=key)
        if rows:
            found = next(
                (i for i, (key, _) in enumerate(rows) if key == selected),
                min(old_row, len(rows) - 1),
            )
            table.move_cursor(row=max(0, found), scroll=False)

    @on(Input.Changed, "#filter")
    def filter_prices(self):
        self.render_prices()

    def render_prices(self):
        quotes = self.quote_data.get("quotes", {})
        archived = {m["symbol"]: m for m in self.overview_data.get("members", [])}
        members = {m["symbol"]: m for m in self.quote_data.get("members", [])} or archived
        session = self.overview_data.get("session")
        final_at = (
            datetime.combine(date.fromisoformat(session), time(15, 30), market.IST)
            if session
            else None
        )
        symbols = dict.fromkeys([*market.BENCHMARKS, *members, *quotes])
        self.displayed_prices = {}
        for symbol in symbols:
            member, quote = members.get(symbol, {}), quotes.get(symbol, {})
            canonical = member.get("exchange_symbol", quote.get("symbol", symbol))
            self.displayed_prices[symbol] = price_for_display(
                symbol, member, archived.get(canonical, {}), quote, final_at
            )
        summary = Text()
        for symbol in market.BENCHMARKS:
            quote = self.displayed_prices[symbol]
            change = quote.get("change_pct")
            value = quote.get("price")
            summary.append(f"{symbol}  {num(value)}", style="bold")
            if change is not None:
                summary.append(f" {change:+.2f}%", style="green" if change >= 0 else "red")
            summary.append("    ")
        self.query_one("#indices", Static).update(summary)
        search = self.query_one("#filter", Input).value.strip().upper()
        rows = []
        for s in symbols:
            member = members.get(s, {})
            if search not in (s + " " + member.get("sector", "")).upper():
                continue
            q = self.displayed_prices[s]
            change = q.get("change_pct")
            move = (
                Text("—")
                if change is None
                else Text(f"{change:+.2f}%", style="green" if change >= 0 else "red")
            )
            exchange_time = stamp(q.get("timestamp"))
            if q.get("recorded"):
                exchange_time = f"EOD {q['recorded']}"
            elif q and (self.quote_data.get("error") or q.get("cached")):
                exchange_time = f"Cached · {exchange_time}"
            rows.append(
                (
                    s,
                    [
                        s,
                        num(q.get("price")),
                        move,
                        num(q.get("open")),
                        num(q.get("high")),
                        num(q.get("low")),
                        exchange_time,
                    ],
                )
            )
        self.rows("prices-table", rows)
        provider = self.quote_data["provider"]
        parts = [feed_label(provider)]
        error = self.quote_data.get("error")
        requested = self.quote_data.get("requested", 0)
        if error:
            parts.append(f"Refresh failed · {error}")
        if quotes:
            cached = sum(bool(error or q.get("cached")) for q in quotes.values())
            refreshed = len(quotes) - cached
            count = f"{refreshed}/{requested}" if requested else str(refreshed)
            parts.append(f"{count} quoted" + (f" · {cached} cached" if cached else ""))
            latest_quote = max(
                (q for q in quotes.values() if q.get("timestamp")),
                key=lambda q: q["timestamp"],
                default=None,
            )
            if latest_quote:
                latest = latest_quote["timestamp"]
                freshness = quote_freshness(
                    provider,
                    datetime.fromisoformat(latest),
                    datetime.now(market.IST),
                    cached=bool(error or latest_quote.get("cached")),
                    interval=self.data.interval,
                )
                if freshness != "Delayed":
                    parts.append(freshness)
                parts.append(f"Latest exchange time {stamp(latest)} IST")
            else:
                parts.append("Exchange time unavailable")
        elif error or self.quote_data.get("fetched_at") or requested:
            parts.append(f"0/{requested} quoted" if requested else "No quotes")
            parts.append("Showing recorded closes where available")
        else:
            parts.append("Waiting for quotes")
        if self.quote_data.get("warning"):
            parts.append(self.quote_data["warning"])
        parts.append(f"{self.data.interval}s poll")
        self.query_one("#quote-status", Static).update(" · ".join(parts))

    @on(Select.Changed, "#run-select")
    def choose_run(self, event):
        if isinstance(event.value, str) and event.value != self.run_name:
            self.run_name = event.value
            self.poll_local()

    @on(DataTable.RowSelected)
    def show_detail(self, event):
        key = event.row_key.value
        try:
            if event.data_table.id == "calls-table":
                call_id = int(key)

                def feedback(decision):
                    if decision:
                        try:
                            self.data.feedback(call_id, decision)
                            self.notify(f"Call {call_id} marked {decision}")
                            self.poll_local()
                        except (ValueError, sqlite3.Error) as exc:
                            self.notify(str(exc), severity="warning")

                self.push_screen(
                    Detail(f"Call {key}", call_text(self.data.call(call_id)), feedback=True),
                    feedback,
                )
            elif event.data_table.id == "paper-trades":
                trade = self.data.paper_trade(int(key))
                mark = paper_live_view(self.paper_data, self.quote_data)["positions"].get(int(key))
                self.push_screen(
                    Detail(
                        f"Paper trade #{key}",
                        call_text(trade, paper_mark=mark, provider=self.quote_data["provider"]),
                    )
                )
            elif event.data_table.id == "entry-comparison":
                original, shadow = (
                    self.data.paper_trade(int(i), comparison=True) for i in key.split("/")
                )
                body = (
                    "Shadow rule · skip above close + 0.5 ATR(14). Target 2R from fill."
                    "\n\nOriginal rule\n"
                    + call_text(original)
                    + "\n\nShadow rule\n"
                    + call_text(shadow)
                )
                self.push_screen(Detail("Entry comparison", body))
            elif event.data_table.id == "paper-history":
                fills = self.data.paper_fills(key)
                lines = [
                    f"Call #{f['call_id']} {f['symbol']}  ·  {f['side'].upper()} {f['qty']} "
                    f"at ₹{num(f['price'])}  ·  Fees ₹{num(f['fees'])}  ·  {f['isin']}"
                    for f in fills
                ]
                self.push_screen(
                    Detail(f"Paper fills · {key}", "\n".join(lines) or "No fills this session.")
                )
            elif event.data_table.id == "reports-table":
                self.push_screen(Detail("Backtest report", report_text(self.data.report(key))))
            elif event.data_table.id == "prices-table":
                q = self.displayed_prices.get(key, {})
                quote = ""
                if q.get("recorded"):
                    quote = f"Recorded close ₹{num(q['price'])} · EOD {q['recorded']}"
                elif q:
                    source = feed_label(self.quote_data["provider"])
                    if self.quote_data.get("error") or q.get("cached"):
                        source += " · Cached"
                    quote = (
                        f"{source} · Last ₹{num(q.get('price'))} · Previous close ₹{num(q.get('previous_close'))} · "
                        f"Open ₹{num(q.get('open'))} · High ₹{num(q.get('high'))} · Low ₹{num(q.get('low'))}\n"
                        f"Volume {num(q.get('volume'), 0)} · Exchange time {stamp(q.get('timestamp'))} IST"
                    )
                if key not in market.BENCHMARKS:
                    symbol = next(
                        (
                            r.get("exchange_symbol", key)
                            for r in self.quote_data.get("members", [])
                            if r["symbol"] == key
                        ),
                        self.quote_data.get("quotes", {}).get(key, {}).get("symbol", key),
                    )
                    self.push_screen(StockReportScreen(symbol, quote))
                elif q:
                    self.push_screen(Detail(key, quote))
                else:
                    self.notify("No exchange quote for this instrument yet.")
        except (KeyError, OSError, ValueError, sqlite3.Error):
            self.notify("Unable to read the selected record.", severity="warning")

    def action_daily(self):
        self.data.start_daily()
        self.update_activity()
