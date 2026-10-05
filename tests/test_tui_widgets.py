"""Tests for individual ACS Textual TUI widget components."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountUtilizationRecord,
    AccountVerification,
    HealthStatus,
    ProblemStatus,
    ProxyConfig,
    SnapshotVerification,
    StatusSnapshot,
    SwitchHistoryEntry,
)
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.detail_panel import DetailPanel
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar
from antigravity_cli_switcher.tui.widgets.history_tab import HistoryTab
from antigravity_cli_switcher.tui.widgets.hygiene_tab import HygieneTab
from antigravity_cli_switcher.tui.widgets.logs_tab import LogsTab
from antigravity_cli_switcher.tui.widgets.proxy_tab import ProxyTab
from antigravity_cli_switcher.tui.widgets.status_bar import StatusBar


def create_sample_snapshot() -> tuple[StatusSnapshot, SnapshotVerification]:
    snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="alpha",
        switch_mode="manual",
        switch_history=[
            SwitchHistoryEntry(
                at="2026-10-02 12:00:00",
                previous_active="beta",
                active="alpha",
                switched_to="alpha",
                outcome="success",
                trigger="manual",
                reason="User switch",
            ),
        ],
        accounts={
            "alpha": AccountMeta(
                enabled=True,
                status="active",
                plan_type="pro",
                fail_count=0,
                proxy=ProxyConfig(enabled=True, url="http://127.0.0.1:8080", label="Local"),
            ),
            "beta": AccountMeta(
                enabled=False,
                status="standby",
                plan_type="free",
                fail_count=1,
                proxy=ProxyConfig(enabled=False, url="http://192.168.1.1:1080", label="VPN"),
            ),
            "gamma": AccountMeta(
                enabled=True,
                status="standby",
                plan_type="pro",
                fail_count=0,
                proxy=ProxyConfig(enabled=False),
            ),
        },
    )
    verification = SnapshotVerification(
        active="alpha",
        switch_mode="manual",
        accounts={
            "alpha": AccountVerification(problem_status=ProblemStatus.OK, summary="Ready"),
            "beta": AccountVerification(problem_status=ProblemStatus.DISABLED, summary="Disabled"),
            "gamma": AccountVerification(
                problem_status=ProblemStatus.SYNTHETIC_TOKEN,
                summary="Synthetic test token found",
                is_synthetic=True,
                recommended_action="fix",
            ),
        },
    )
    return snapshot, verification


@pytest.fixture
def temp_paths(tmp_path: Path) -> ManagerPaths:
    root = tmp_path
    paths = ManagerPaths(
        root=root,
        accounts_dir=root / "accounts",
        state_file=root / "state.json",
        runtime_dir=root / "runtime",
        lock_file=root / ".manager.lock",
    )
    paths.accounts_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "manager.log").write_text(
        "2026-10-02 12:00:00 [INFO] antigravity_cli_switcher: initialized\n"
        "2026-10-02 12:01:00 [WARNING] antigravity_cli_switcher: high quota\n"
        "2026-10-02 12:02:00 [ERROR] antigravity_cli_switcher: failed to connect\n",
        encoding="utf-8",
    )
    return paths


@pytest.mark.asyncio
async def test_header_bar_reactivity() -> None:
    header = HeaderBar()
    header.active_account = "alpha"
    header.account_count = 5
    header.refresh_interval = 10
    header.switch_mode = "auto"
    header.sort_mode = "health"
    header.quota_backend = "http"
    assert header.active_account == "alpha"
    assert header.account_count == 5
    assert header.refresh_interval == 10
    assert header.switch_mode == "auto"
    assert header.sort_mode == "health"
    assert header.quota_backend == "http"


@pytest.mark.asyncio
async def test_status_bar_reactivity() -> None:
    bar = StatusBar()
    bar.message = "Switched to beta"
    bar.worker_status = "REFRESHING"
    assert bar.message == "Switched to beta"
    assert bar.worker_status == "REFRESHING"


@pytest.mark.asyncio
async def test_account_table_populate() -> None:
    table = AccountTable()
    snapshot, verification = create_sample_snapshot()

    class TableApp(ACSApp):
        def compose(self):
            yield table

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TableApp(paths=paths)
    async with app.run_test():
        table.update_accounts(snapshot, verification, sort_mode="name")
        assert table.row_count == 3
        assert table.get_selected_account_name() == "alpha"


@pytest.mark.asyncio
async def test_detail_panel_update() -> None:
    panel = DetailPanel()
    snapshot, verification = create_sample_snapshot()

    class PanelApp(ACSApp):
        def compose(self):
            yield panel

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = PanelApp(paths=paths)
    async with app.run_test():
        meta = snapshot.accounts["alpha"]
        ver = verification.accounts["alpha"]
        panel.update_detail("alpha", meta, ver)
        content_static = panel.query_one("#detail-content", Static)
        assert content_static._render() is not None
        rendered_text = str(content_static.content)
        assert "ProblemStatus" not in rendered_text
        assert "Problem      : None" in rendered_text or "Problem   : None" in rendered_text

        # Test account with issue
        gamma_meta = snapshot.accounts["gamma"]
        gamma_ver = verification.accounts["gamma"]
        panel.update_detail("gamma", gamma_meta, gamma_ver)
        rendered_gamma = str(content_static.content)
        assert "Synthetic token (fix needed)" in rendered_gamma


@pytest.mark.asyncio
async def test_detail_panel_utilization_and_zombie() -> None:
    panel = DetailPanel()
    snapshot, verification = create_sample_snapshot()

    class PanelApp(ACSApp):
        def compose(self):
            yield panel

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = PanelApp(paths=paths)
    async with app.run_test():
        meta = snapshot.accounts["alpha"]
        ver = verification.accounts["alpha"]
        util = AccountUtilizationRecord(
            rolling_7d_active_seconds=3600,
            rolling_7d_gemini_consumed=45.0,
            rolling_7d_other_consumed=20.0,
            rolling_7d_min_gemini_headroom=55.0,
            rolling_7d_min_other_headroom=80.0,
            is_zombie=True,
            monthly_cost_usd=25.0,
        )
        panel.update_detail("alpha", meta, ver, util)
        content_static = panel.query_one("#detail-content", Static)
        rendered = str(content_static.content)
        assert "7D Duty" in rendered
        assert "Burnt" in rendered
        assert "G:45%" in rendered
        assert "O:20%" in rendered
        assert "ZOMBIE" not in rendered
        assert "$25/mo waste" not in rendered


@pytest.mark.asyncio
async def test_account_table_health_column_strictly_operational() -> None:
    table = AccountTable()
    snapshot, verification = create_sample_snapshot()

    class TableApp(ACSApp):
        def compose(self):
            yield table

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TableApp(paths=paths)
    async with app.run_test():
        # Even with zombie accounts, AccountTable strictly displays operational health
        snapshot.fleet_utilization.accounts["gamma"] = AccountUtilizationRecord(is_zombie=True, monthly_cost_usd=20.0)
        snapshot.fleet_utilization.accounts["alpha"] = AccountUtilizationRecord(is_zombie=True, monthly_cost_usd=20.0)
        table.update_accounts(snapshot, verification, sort_mode="name")

        gamma_texts = [str(cell) for cell in table.get_row("gamma")]
        assert not any("ZOMBIE" in t for t in gamma_texts)
        assert any("SYNTHE" in t for t in gamma_texts)

        alpha_texts = [str(cell) for cell in table.get_row("alpha")]
        assert not any("ZOMBIE" in t for t in alpha_texts)
        assert any("OK" in t for t in alpha_texts)


@pytest.mark.asyncio
async def test_proxy_tab_populate() -> None:
    tab = ProxyTab()
    snapshot, _ = create_sample_snapshot()

    class ProxyApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = ProxyApp(paths=paths)
    async with app.run_test():
        tab.update_proxies(snapshot)
        assert len(tab.account_order) == 3
        table = tab.query_one("#proxy-table", DataTable)
        assert table.row_count == 3
        assert tab.get_selected_account_name() == "alpha"
        tab.set_account_latency("alpha", "12ms")
        assert tab._proxy_latencies.get("alpha") == "12ms"


@pytest.mark.asyncio
async def test_hygiene_tab_populate() -> None:
    tab = HygieneTab()
    snapshot, verification = create_sample_snapshot()

    class HygieneApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = HygieneApp(paths=paths)
    async with app.run_test():
        tab.update_hygiene(snapshot, verification)
        assert len(tab.account_order) == 3
        table = tab.query_one("#hygiene-table", DataTable)
        assert table.row_count == 3
        assert tab.get_selected_account_name() == "alpha"


@pytest.mark.asyncio
async def test_history_tab_populate() -> None:
    tab = HistoryTab()
    snapshot, _ = create_sample_snapshot()

    class HistoryApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = HistoryApp(paths=paths)
    async with app.run_test():
        tab.update_history(snapshot)
        table = tab.query_one("#history-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_logs_tab_populate(temp_paths: ManagerPaths) -> None:
    tab = LogsTab()

    class LogsApp(ACSApp):
        def compose(self):
            yield tab

    app = LogsApp(paths=temp_paths)
    async with app.run_test():
        tab.load_initial_logs(temp_paths)
        assert len(tab._raw_lines) == 3
        tab.set_filter_level("ERROR")
        assert tab.active_level_filter == "ERROR"
        tab.set_filter_level("ALL")
        assert tab.active_level_filter == "ALL"


@pytest.mark.asyncio
async def test_account_table_in_place_cell_update() -> None:
    table = AccountTable()
    snapshot, verification = create_sample_snapshot()

    class TableApp(ACSApp):
        def compose(self):
            yield table

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TableApp(paths=paths)
    async with app.run_test():
        table.update_accounts(snapshot, verification, sort_mode="name")
        assert table.row_count == 3
        # Select row 1 ('beta')
        table.cursor_coordinate = Coordinate(1, 0)
        assert table.cursor_row == 1
        assert table.get_selected_account_name() == "beta"

        # Update accounts with same keys - in-place update must preserve cursor_row
        table.update_accounts(snapshot, verification, sort_mode="name")
        assert table.cursor_row == 1
        assert table.get_selected_account_name() == "beta"


@pytest.mark.asyncio
async def test_hygiene_tab_in_place_update() -> None:
    tab = HygieneTab()
    snapshot, verification = create_sample_snapshot()

    class HygieneApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = HygieneApp(paths=paths)
    async with app.run_test():
        tab.update_hygiene(snapshot, verification)
        table = tab.query_one("#hygiene-table", DataTable)
        assert table.row_count == 3
        table.cursor_coordinate = Coordinate(1, 0)
        assert table.cursor_row == 1
        assert tab.get_selected_account_name() == "beta"

        # Re-update with same accounts: cursor must be preserved
        tab.update_hygiene(snapshot, verification)
        assert table.cursor_row == 1
        assert tab.get_selected_account_name() == "beta"


def test_account_table_renders_ineligible_badge() -> None:
    table = AccountTable()
    meta = AccountMeta(enabled=True, health_status=HealthStatus.INELIGIBLE)
    ver = AccountVerification(
        problem_status=ProblemStatus.INELIGIBLE,
        recommended_action="human_intervention",
        summary="Account ineligible: requires human intervention. Verify in browser or use another account.",
        health_status=HealthStatus.INELIGIBLE,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    vals = table._row_values("test_ineligible_acct", meta, ver, "alpha", now)
    health_text = vals[4]
    assert health_text.plain == "INELIG"
    assert "bold red" in str(health_text.style)


def test_account_table_renders_ineligible_without_verification_fallback() -> None:
    table = AccountTable()
    meta = AccountMeta(enabled=True, health_status=HealthStatus.INELIGIBLE)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    vals = table._row_values("test_ineligible_acct", meta, None, "alpha", now)
    health_text = vals[4]
    assert health_text.plain == "INELIG"
    assert "bold red" in str(health_text.style)


@pytest.mark.asyncio
async def test_detail_panel_shows_human_intervention() -> None:
    panel = DetailPanel()

    class DetailApp(ACSApp):
        def compose(self):
            yield panel

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    meta = AccountMeta(enabled=True, health_status=HealthStatus.INELIGIBLE)
    ver = AccountVerification(
        problem_status=ProblemStatus.INELIGIBLE,
        recommended_action="human_intervention",
        summary="Account ineligible: requires human intervention. Verify in browser or use another account.",
        health_status=HealthStatus.INELIGIBLE,
    )
    app = DetailApp(paths=paths)
    async with app.run_test():
        panel.update_detail("test_ineligible_acct", meta, ver)
        content_static = panel.query_one("#detail-content", Static)
        renderable = getattr(content_static, "_renderable", None) or content_static.render()
        text_str = renderable.plain if hasattr(renderable, "plain") else str(renderable)
        assert "Requires human intervention" in text_str
        assert "Ineligible" in text_str or "ineligible" in text_str
        assert "human intervention" in text_str.lower()
