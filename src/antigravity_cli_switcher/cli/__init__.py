"""Antigravity CLI Switcher - Command Line Interface Package."""

from __future__ import annotations

from antigravity_cli_switcher.cli.commands import (
    dispatch_command,
    prompt_nonempty,
    prompt_optional_path,
    prompt_optional_text,
    run_dashboard,
    run_login_with_prompt,
    run_menu,
    run_proxy_dashboard,
)
from antigravity_cli_switcher.cli.output import (
    DEFAULT_SORT_MODE,
    SORT_MODES,
    _family_usage_windows,
    _format_age,
    _format_countdown,
    _format_identity,
    _format_last_error,
    _format_last_switch_event,
    _format_live_state,
    _format_model_usage,
    _format_natural_duration,
    _format_next_refresh,
    _format_proxy_brief,
    _format_reset_compact,
    _format_reset_value,
    _format_switch_runtime_policy,
    _format_switch_runtime_summary,
    _format_usage,
    _format_usage_value,
    _format_window_summary,
    _get_group_window,
    _get_min_window,
    _get_nearest_reset,
    _parse_iso_timestamp,
    _problem_badge,
    _usage_window_values,
    print_account_list,
    print_current_account,
    print_hygiene,
    print_proxy_list,
    print_proxy_show,
    print_switch_history,
    print_switch_runtime,
    print_verify_accounts,
)
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.manager import build_paths


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = build_paths(args.root)

    try:
        return dispatch_command(paths, args, parser)
    except ValueError as e:
        parser.exit(2, f"error: {e}\n")
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "DEFAULT_SORT_MODE",
    "SORT_MODES",
    "_family_usage_windows",
    "_format_age",
    "_format_countdown",
    "_format_identity",
    "_format_last_error",
    "_format_last_switch_event",
    "_format_live_state",
    "_format_model_usage",
    "_format_natural_duration",
    "_format_next_refresh",
    "_format_proxy_brief",
    "_format_reset_compact",
    "_format_reset_value",
    "_format_switch_runtime_policy",
    "_format_switch_runtime_summary",
    "_format_usage",
    "_format_usage_value",
    "_format_window_summary",
    "_get_group_window",
    "_get_min_window",
    "_get_nearest_reset",
    "_parse_iso_timestamp",
    "_problem_badge",
    "_usage_window_values",
    "build_parser",
    "dispatch_command",
    "main",
    "print_account_list",
    "print_current_account",
    "print_hygiene",
    "print_proxy_list",
    "print_proxy_show",
    "print_switch_history",
    "print_switch_runtime",
    "print_verify_accounts",
    "prompt_nonempty",
    "prompt_optional_path",
    "prompt_optional_text",
    "run_dashboard",
    "run_login_with_prompt",
    "run_menu",
    "run_proxy_dashboard",
]
