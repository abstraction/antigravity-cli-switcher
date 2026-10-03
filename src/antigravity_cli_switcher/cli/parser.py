from __future__ import annotations

import argparse
from pathlib import Path

from antigravity_cli_switcher.manager import default_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acs",
        description="Antigravity CLI active-standby account manager and failover switcher",
    )
    parser.add_argument("--root", type=Path, default=default_root(), help="Manager root directory")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Create initial manager layout")
    sub.add_parser("dashboard", help="Open the full-screen dashboard")
    sub.add_parser("proxy-dashboard", help="Open the proxy dashboard")
    sub.add_parser("menu", help="Open the interactive menu")
    status = sub.add_parser("status", help="Show current manager status")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    verify = sub.add_parser("verify-accounts", help="Verify saved account auth/runtime usability")
    verify.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    switch_runtime = sub.add_parser("switch-runtime", help="Show the background account switcher state")
    switch_runtime.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    switch_history = sub.add_parser("switch-history", help="Show recent switch audit events")
    switch_history.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    switch_history.add_argument("--limit", type=int, default=10, help="Maximum number of recent events to print")
    current = sub.add_parser("current", help="Show the current active account")
    current.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    list_cmd = sub.add_parser("list", help="List saved accounts")
    list_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    proxy_list = sub.add_parser("proxy-list", help="List per-account proxy metadata")
    proxy_list.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    proxy_show = sub.add_parser("proxy-show", help="Show proxy metadata for the active or named account")
    proxy_show.add_argument("name", nargs="?")
    proxy_show.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    proxy_set = sub.add_parser("proxy-set", help="Set proxy metadata for an account")
    proxy_set.add_argument("name")
    proxy_set.add_argument("url")
    proxy_set.add_argument("--label")
    proxy_set.add_argument("--disabled", action="store_true", help="Store proxy configuration in disabled state")
    proxy_set.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    proxy_clear = sub.add_parser("proxy-clear", help="Clear proxy metadata for an account")
    proxy_clear.add_argument("name")
    proxy_clear.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    whoami = sub.add_parser("whoami", help="Show authenticated identity for an account")
    whoami.add_argument("name", nargs="?", help="Account name (defaults to active account)")
    whoami.add_argument("--refresh", action="store_true", help="Refresh identity from saved tokens")
    whoami.add_argument(
        "--probe-usage",
        action="store_true",
        help="Run non-mutating agy task against the account home to probe active identity hints",
    )
    whoami.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    whoami.add_argument("--timeout-seconds", type=int, default=20, help="Probe timeout in seconds")
    models = sub.add_parser("models", help="List models available via agy for an account")
    models.add_argument("name", nargs="?", help="Account name (defaults to active account)")
    models.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    models.add_argument("--timeout-seconds", type=int, default=20, help="Command timeout in seconds")
    models.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    add = sub.add_parser("add", help="Add an account by copying or creating a profile home")
    add.add_argument("name")
    add.add_argument("--source-dir", type=Path, default=None, help="Optional existing ~/.gemini directory to copy")
    import_cur = sub.add_parser("import-current", help="Import the active ~/.gemini profile as a named account")
    import_cur.add_argument("name")
    import_cur.add_argument("--source-dir", type=Path, default=None, help="Custom active ~/.gemini directory")
    login = sub.add_parser("login", help="Run browser login into an isolated account home")
    login.add_argument("name", nargs="*", help="Account name, alias, or index")
    login.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    login.add_argument("--timeout-seconds", type=int, default=600, help="Login timeout in seconds")
    login.add_argument(
        "--overwrite", "-f", action="store_true", help="Overwrite existing account profile without prompt"
    )
    switch = sub.add_parser("switch", help="Switch the active account profile")
    switch.add_argument("name", nargs="+", help="Account name or index")
    activate = sub.add_parser("activate", help="Alias for switch")
    activate.add_argument("name", nargs="+", help="Account name or index")
    sub.add_parser("switch-next", help="Switch to the next standby account in round-robin order")
    rotate = sub.add_parser("rotate", help="Rotate to the next standby account and print target")
    rotate.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    sub.add_parser("apply-active", help="Re-apply the currently active profile to live ~/.gemini")
    ensure_active = sub.add_parser(
        "ensure-active",
        help="Ensure a valid usable account is active, switching away from broken/cooldown/exhausted accounts",
    )
    ensure_active.add_argument(
        "--force", action="store_true", help="Force failover even if current account is marked healthy"
    )
    ensure_active.add_argument(
        "--family",
        choices=["gemini", "claude", "other"],
        default=None,
        help="Required model family that candidate account must have quota for",
    )
    ensure_active.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    resolve_route_cmd = sub.add_parser(
        "resolve-route",
        help="Recommend or activate the best account for a requested model family",
    )
    resolve_route_cmd.add_argument("family", choices=["gemini", "claude", "other"], help="Requested model family")
    resolve_route_cmd.add_argument(
        "--fallback-strategy",
        choices=[
            "same-family-first",
            "same-account-first",
            "strict-family",
            "same_family_first",
            "same_account_first",
            "strict_family",
        ],
        default=None,
        help="Fallback behavior when preferred family quota is exhausted",
    )
    resolve_route_cmd.add_argument(
        "--force-switch",
        action="store_true",
        help="Immediately apply and switch active profile to recommended account",
    )
    resolve_route_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    switch_mode = sub.add_parser("switch-mode", help="Get or set automatic failover mode (auto/manual)")
    switch_mode.add_argument("mode", nargs="?", choices=["auto", "manual"], help="New switch mode")
    switch_mode.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    quota_backend_cmd = sub.add_parser("quota-backend", help="Get or set quota polling backend (native/http/auto)")
    quota_backend_cmd.add_argument(
        "backend", nargs="?", choices=["native", "http", "auto"], help="New quota backend (native/http/auto)"
    )
    quota_backend_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    switch_policy = sub.add_parser("switch-policy", help="Get or set auto-failover policy thresholds")
    switch_policy.add_argument(
        "--short-threshold",
        type=float,
        default=None,
        help="Failover when short-term usage percentage is at or above this threshold",
    )
    switch_policy.add_argument(
        "--gemini-threshold",
        type=float,
        default=None,
        help="Failover when Gemini short-term usage percentage is at or above this threshold",
    )
    switch_policy.add_argument(
        "--other-threshold",
        type=float,
        default=None,
        help="Failover when Other/Claude short-term usage percentage is at or above this threshold",
    )
    switch_policy.add_argument(
        "--refresh-failure-threshold",
        type=int,
        default=None,
        help="Failover after this many consecutive usage refresh failures",
    )
    switch_policy.add_argument(
        "--candidate-strategy",
        choices=["balanced", "highest-short", "round-robin", "highest_short", "round_robin"],
        default=None,
        help="Strategy for choosing the next active account",
    )
    switch_policy.add_argument(
        "--family-fallback-strategy",
        choices=[
            "same-family-first",
            "same-account-first",
            "strict-family",
            "same_family_first",
            "same_account_first",
            "strict_family",
        ],
        default=None,
        help="Default fallback strategy for family routing",
    )
    switch_policy.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    disable = sub.add_parser("disable", help="Disable an account from rotation")
    disable.add_argument("name")
    enable = sub.add_parser("enable", help="Re-enable an account for rotation")
    enable.add_argument("name")
    mark_bad_cmd = sub.add_parser("mark-bad", help="Mark an account broken with a cooldown period")
    mark_bad_cmd.add_argument("name")
    mark_bad_cmd.add_argument("--reason", default="manual", help="Reason for failure mark")
    mark_bad_cmd.add_argument("--cooldown-minutes", type=int, default=60, help="Cooldown duration")
    clear_bad_cmd = sub.add_parser("clear-bad", help="Clear an account's broken/cooldown state")
    clear_bad_cmd.add_argument("name")
    rename = sub.add_parser("rename", help="Rename an account profile")
    rename.add_argument("old_name")
    rename.add_argument("new_name")
    delete = sub.add_parser("delete", help="Delete an account profile directory")
    delete.add_argument("name")
    set_email = sub.add_parser("set-email", help="Set or clear expected email for an account profile")
    set_email.add_argument("name", help="Account name")
    set_email.add_argument("email", nargs="?", default="", help="Expected email address (omit or empty to clear)")
    set_email.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    set_live = sub.add_parser("set-live-dir", help="Configure custom live directory target")
    set_live.add_argument("path", nargs="?", default=None, help="Target path (clears if omitted)")
    refresh_usage = sub.add_parser("refresh-usage", help="Query agy to refresh quota usage metrics for an account")
    refresh_usage.add_argument("name", help="Account name to refresh")
    refresh_usage.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    refresh_usage.add_argument("--warmup-timeout-seconds", type=int, default=25, help="Task warmup timeout")
    refresh_usage.add_argument(
        "--backend", choices=["native", "http", "auto"], default=None, help="Quota polling backend (native/http/auto)"
    )
    refresh_usage.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    refresh_due = sub.add_parser("refresh-due", help="Refresh usage for the next account due by policy")
    refresh_due.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    refresh_due.add_argument("--warmup-timeout-seconds", type=int, default=25, help="Task warmup timeout")
    refresh_due.add_argument(
        "--backend", choices=["native", "http", "auto"], default=None, help="Quota polling backend (native/http/auto)"
    )
    refresh_due.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    refresh_all = sub.add_parser("refresh-all", help="Refresh usage metrics sequentially for all accounts")
    refresh_all.add_argument("--agy-binary", default=None, help="Explicit agy binary path")
    refresh_all.add_argument("--warmup-timeout-seconds", type=int, default=25, help="Task warmup timeout per account")
    refresh_all.add_argument("--delay-seconds", type=float, default=2.0, help="Delay between account refreshes")
    refresh_all.add_argument("--include", nargs="+", help="Only refresh these specific accounts")
    refresh_all.add_argument("--exclude", nargs="+", help="Skip these accounts")
    refresh_all.add_argument("--skip-disabled", action="store_true", help="Skip disabled accounts")
    refresh_all.add_argument(
        "--skip-exhausted", action="store_true", help="Skip accounts with 0% short window remaining"
    )
    refresh_all.add_argument(
        "--backend", choices=["native", "http", "auto"], default=None, help="Quota polling backend (native/http/auto)"
    )
    refresh_all.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    rotate_fail = sub.add_parser("rotate-after-failure", help="Record failure, mark cooldown, and switch to standby")
    rotate_fail.add_argument("--reason", default="runtime failure", help="Failure reason")
    rotate_fail.add_argument("--cooldown-minutes", type=int, default=60, help="Cooldown duration")
    rotate_fail.add_argument("--live-dir", default=None, help="Optional live directory path")
    rotate_fail.add_argument("--force-switch", action="store_true", help="Switch even if manual mode is enabled")
    rotate_fail.add_argument("--trigger", default=None, help="Event trigger name")
    rotate_fail.add_argument("--request-id", default=None, help="Associated request ID")
    rotate_fail.add_argument(
        "--family", choices=["gemini", "claude", "other"], default=None, help="Required model family"
    )
    rotate_fail.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    ack_restart = sub.add_parser(
        "ack-restart", help="Acknowledge and clear restart_required state after restarting agy"
    )
    ack_restart.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    watch = sub.add_parser("watch", help="Watch agy quota logs and trigger auto-failover on exhaustion")
    watch.add_argument("--once", action="store_true", help="Poll once and exit")
    watch.add_argument("--from-start", action="store_true", help="Read log file from beginning")
    watch.add_argument("--poll-seconds", type=float, default=2.0, help="Polling interval in seconds")
    watch.add_argument("--no-rotate", action="store_true", help="Only monitor; do not switch accounts")
    watch.add_argument("--force-switch", action="store_true", help="Switch even in manual mode")
    watch.add_argument("--cooldown-minutes", type=int, default=60, help="Cooldown applied to exhausted account")
    watch.add_argument("--on-rotate", default=None, help="Shell command to run after account switch")
    watch.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    update_meta = sub.add_parser("update-meta", help="Update account runtime metadata fields")
    update_meta.add_argument("name", help="Account name")
    update_meta.add_argument("--usage-status", default=None)
    update_meta.add_argument("--usage-value", type=float, default=None)
    update_meta.add_argument("--reset-at", default=None)
    update_meta.add_argument("--short-usage-status", default=None)
    update_meta.add_argument("--short-usage-value", type=float, default=None)
    update_meta.add_argument("--short-reset-at", default=None)
    update_meta.add_argument("--weekly-usage-status", default=None)
    update_meta.add_argument("--weekly-usage-value", type=float, default=None)
    update_meta.add_argument("--weekly-reset-at", default=None)
    update_meta.add_argument("--health-status", default=None)
    update_meta.add_argument("--last-live-check-at", default=None)
    update_meta.add_argument("--last-live-check-error", default=None)
    update_meta.add_argument("--next-live-check-at", default=None)
    update_meta.add_argument("--refresh-policy-seconds", type=int, default=None)
    update_meta.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    migrate = sub.add_parser("migrate", help="Migrate configuration and profiles from legacy agy-cli-manager")
    migrate.add_argument("--legacy-root", default=None, help="Legacy directory (default: ~/.agy-cli-manager)")
    migrate.add_argument("--target-root", default=None, help="Target directory (default: ~/.antigravity-cli-switcher)")
    migrate.add_argument("--dry-run", action="store_true", help="Scan and report without writing changes")
    migrate.add_argument("--no-backup", action="store_true", help="Do not create a backup archive of legacy directory")
    migrate.add_argument(
        "--update-shell", action="store_true", help="Update shell configuration files with new aliases"
    )
    migrate.add_argument("--json", action="store_true", help="Print machine-readable JSON report")
    hygiene = sub.add_parser("hygiene", help="Audit credentials, tokens, and storage health across accounts")
    hygiene.add_argument("--fix", action="store_true", help="Sanitize synthetic test tokens from keyring and runtime")
    hygiene.add_argument("--json", action="store_true", help="Print machine-readable JSON report")

    return parser
