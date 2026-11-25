"""Scrollable stock research in the Prices tab."""

import asyncio

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import market
from .saved_research import latest_stock_report
from .stock_report import render_stock_report


class StockReportScreen(ModalScreen):
    BINDINGS = (("escape", "dismiss", "Close"), ("a", "analyze", "Analyze"))
    DEFAULT_CSS = """
    StockReportScreen { align: center middle; }
    StockReportScreen > Vertical {
        width: 96%; height: 95%; background: $surface; border: round $accent; padding: 0 1;
    }
    StockReportScreen VerticalScroll { height: 1fr; }
    StockReportScreen Horizontal { height: auto; margin-top: 1; }
    StockReportScreen Button { min-width: 0; margin-right: 1; }
    #stock-report-status { color: #a5efd4; height: auto; }
    #stock-quote { color: #bed2d7; height: auto; }
    """

    def __init__(self, symbol, quote):
        super().__init__()
        self.symbol = symbol
        self.quote = quote
        self.report = None
        self.reading = True
        self.message = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"{self.symbol} · Reading saved report…", id="stock-report-status", markup=False
            )
            yield Static(self.quote, id="stock-quote", markup=False)
            with VerticalScroll(id="stock-report-scroll"):
                yield Static("", id="stock-report-body", markup=False)
            with Horizontal():
                yield Button("Analyze (A)", id="analyze-stock", variant="primary", disabled=True)
                yield Button("Close (Esc)", id="close-stock-report")

    def on_mount(self):
        self.query_one("#stock-quote").display = bool(self.quote)
        self.query_one("#stock-report-scroll").focus()
        self.load_saved()

    @work
    async def load_saved(self):
        try:
            saved = await asyncio.to_thread(latest_stock_report, self.app.data.root, self.symbol)
        except (OSError, ValueError):
            if not self.message:
                self.message = "Could not read the saved report."
        else:
            # A previous analysis can finish while the user reopens this screen.
            if saved is not None and self.report is None:
                self.display_report(saved)
        self.reading = False
        self.update_controls()

    def display_report(self, report):
        self.report = report
        self.query_one("#stock-report-body", Static).update(render_stock_report(report))

    def update_controls(self):
        busy = self.symbol in self.app.stock_research_running
        button = self.query_one("#analyze-stock", Button)
        button.disabled = busy or self.reading
        button.label = "Reanalyze (A)" if self.report is not None else "Analyze (A)"
        if busy:
            status = "Analyzing… You can close this view while research runs."
        elif self.reading:
            status = "Reading saved report…"
        elif self.message:
            status = self.message
        elif self.report is not None:
            status = "Saved report"
        else:
            status = "No saved report. Choose Analyze to research this stock."
        self.query_one("#stock-report-status", Static).update(f"{self.symbol} · {status}")

    @on(Button.Pressed, "#analyze-stock")
    def action_analyze(self):
        app = self.app
        if self.reading or self.symbol in app.stock_research_running:
            return
        app.stock_research_running.add(self.symbol)
        self.message = ""
        self.update_controls()
        # The worker belongs to the app so closing/reopening the view cannot
        # cancel the analysis or start a second analysis for the same stock.
        app.run_worker(self.analyze(app))

    async def analyze(self, app):
        from .research import run_report

        try:
            result = await asyncio.to_thread(run_report, app.data.root, self.symbol)
        except market.MarketError as exc:
            result = {"status": "ai_unavailable", "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 - keep private transport details out of the UI
            result = {"status": "ai_unavailable", "reason": type(exc).__name__}
        finally:
            app.stock_research_running.discard(self.symbol)
        screen = app.screen
        if (
            isinstance(screen, StockReportScreen)
            and screen.symbol == self.symbol
            and screen.is_mounted
        ):
            if result.get("report"):
                screen.display_report(result)
                screen.message = "Analysis complete"
                screen.query_one("#stock-report-scroll", VerticalScroll).scroll_home(animate=False)
            elif result.get("status") == "disabled":
                screen.message = "Research is disabled. Enable it with radar research-mode on."
            else:
                screen.message = (
                    f"Analysis unavailable · {result.get('reason') or 'Try again later.'}"
                )
            screen.update_controls()

    @on(Button.Pressed, "#close-stock-report")
    def close_stock_report(self):
        self.dismiss()
