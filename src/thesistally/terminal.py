"""Live market monitor and research journal in one terminal."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
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

from . import market
from .dashboard import Dashboard

LABELS = {
    "trend_pullback": "Trend pullback",
    "high_momentum": "52-week high",
    "vcp_breakout": "VCP breakout",
}


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


def call_text(c):
    plan_target = c.get("planned_target", c["target"])
    plan_size = c.get("planned_size", c["size"])
    execution = []
    if c["buy"] is not None:
        execution.append(
            f"Fill ₹{num(c['buy'])}   Stop ₹{num(c['stop'])}   Target ₹{num(c['target'])}"
            f"   Shares {c['size']}   Remaining {c['remaining']}"
        )
    lines = [
        f"{c['symbol']}   ·   {LABELS.get(c['strategy'], c['strategy'])}   ·   {c['day']}",
        f"{c['sector']}   /   {c['status']}   /   {c['allocation']}   /   {c['action']}",
        "",
        f"Plan ₹{num(c['entry'])}   Stop ₹{num(c['stop'])}   Target ₹{num(plan_target)}   Shares {plan_size}",
        *execution,
        f"Net ₹{num(c['net'])}       Outcome {num(c['outcome_r'])} R       Held {c['age']} sessions",
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
        ("q", "quit", "Quit"),
    )

    def __init__(self, dashboard: Dashboard):
        super().__init__()
        self.data = dashboard
        self.overview_data = {}
        self.quote_data = {}
        self.run_name = "observations"
        self.run_options = []
        self.loading_local = self.loading_quotes = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("NSE   ·   Connecting", id="indices", markup=False)
        with TabbedContent(id="tabs"):
            with TabPane("Prices", id="prices"):
                yield Static("Connecting to Kite…", id="quote-status", classes="note", markup=False)
                yield Input(placeholder="Filter symbol or sector", id="filter")
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
                yield Static(
                    "Loading paper account…", id="paper-balance", classes="kicker", markup=False
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
            "Entry plan ₹",
            "Fill ₹",
            "Close ₹",
            "Net P&L ₹",
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
        self.set_interval(5, self.poll_quotes)
        self.set_interval(10, self.poll_local)
        self.set_interval(1, self.update_activity)

    def update_activity(self):
        self.query_one("#activity", Static).update(self.data.activity()["message"])

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
        self.query_one("#prices-table", DataTable).focus()

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
            self.quote_data = await asyncio.to_thread(self.data.quotes)
            self.render_prices()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.query_one("#quote-status", Static).update(str(exc))
        finally:
            self.loading_quotes = False

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
        history, trades = account["history"], account["trades"]
        balance = self.query_one("#paper-balance", Static)
        if history:
            last = history[0]
            balance.update(
                f"{last['day']} close   ·   Equity ₹{num(last['equity'])}   ·   "
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
        rows = []
        for t in trades:
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
                        num(t["entry"]),
                        num(t["buy"]),
                        num(t["eod_close"]),
                        num(t["net"] if t["net"] is not None else t["marked_pnl"]),
                    ],
                )
            )
        self.rows("paper-trades", rows)
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
        members = {m["symbol"]: m for m in self.overview_data.get("members", [])}
        summary = Text()
        for symbol in market.BENCHMARKS:
            quote = quotes.get(symbol, {})
            change = quote.get("change_pct")
            value = quote.get("price", members.get(symbol, {}).get("close"))
            summary.append(f"{symbol}  {num(value)}", style="bold")
            if change is not None:
                summary.append(f" {change:+.2f}%", style="green" if change >= 0 else "red")
            summary.append("    ")
        self.query_one("#indices", Static).update(summary)
        symbols = dict.fromkeys([*market.BENCHMARKS, *members, *quotes])
        search = self.query_one("#filter", Input).value.strip().upper()
        rows = []
        for s in symbols:
            member = members.get(s, {})
            if search not in (s + " " + member.get("sector", "")).upper():
                continue
            q = quotes.get(s, {})
            change = q.get("change_pct")
            move = (
                Text("—")
                if change is None
                else Text(f"{change:+.2f}%", style="green" if change >= 0 else "red")
            )
            fallback = member.get("close")
            rows.append(
                (
                    s,
                    [
                        s,
                        num(q.get("price", fallback)),
                        move,
                        num(q.get("open")),
                        num(q.get("high")),
                        num(q.get("low")),
                        stamp(q.get("timestamp"))
                        if q
                        else (f"EOD {self.overview_data['session']}" if fallback else "—"),
                    ],
                )
            )
        self.rows("prices-table", rows)
        if self.quote_data.get("error"):
            msg = f"Kite unavailable · {self.quote_data['error']} · showing last available prices"
        elif quotes:
            latest = max(
                (q["timestamp"] for q in quotes.values() if q.get("timestamp")), default=None
            )
            recent = (
                latest
                and abs((datetime.now(market.IST) - datetime.fromisoformat(latest)).total_seconds())
                <= 120
            )
            feed = "Live quotes" if recent else "Last exchange quotes"
            msg = f"{feed} · Kite {len(quotes)}/{self.quote_data['requested']} · fetched {stamp(self.quote_data['fetched_at'])} IST · 5s poll"
        else:
            msg = "Connecting to Kite…"
        self.query_one("#quote-status", Static).update(msg)

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
                self.push_screen(
                    Detail(f"Paper trade #{key}", call_text(self.data.paper_trade(int(key))))
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
                q = self.quote_data.get("quotes", {}).get(key)
                if q:
                    self.push_screen(
                        Detail(
                            key,
                            f"Last price   ₹{num(q['price'])}\nPrevious close   ₹{num(q['previous_close'])}\n"
                            f"Open   ₹{num(q['open'])}\nHigh   ₹{num(q['high'])}\nLow   ₹{num(q['low'])}\n"
                            f"Volume   {num(q['volume'], 0)}\nExchange time   {stamp(q['timestamp'])} IST",
                        )
                    )
                else:
                    self.notify("No broker quote for this instrument yet.")
        except (KeyError, OSError, ValueError, sqlite3.Error):
            self.notify("Unable to read the selected record.", severity="warning")

    def action_daily(self):
        self.data.start_daily()
        self.update_activity()
