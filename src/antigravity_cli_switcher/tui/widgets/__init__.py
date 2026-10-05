"""ACS Textual dashboard widgets."""

from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.detail_panel import DetailPanel
from antigravity_cli_switcher.tui.widgets.fleet_tab import FleetTab
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar
from antigravity_cli_switcher.tui.widgets.history_tab import HistoryTab
from antigravity_cli_switcher.tui.widgets.hygiene_tab import HygieneTab
from antigravity_cli_switcher.tui.widgets.logs_tab import LogsTab
from antigravity_cli_switcher.tui.widgets.proxy_tab import ProxyTab
from antigravity_cli_switcher.tui.widgets.status_bar import StatusBar

__all__ = [
    "AccountTable",
    "DetailPanel",
    "FleetTab",
    "HeaderBar",
    "HistoryTab",
    "HygieneTab",
    "LogsTab",
    "ProxyTab",
    "StatusBar",
]
