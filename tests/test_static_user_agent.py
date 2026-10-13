from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

from antigravity_cli_switcher.manager.cloudcode import (
    CODE_ASSIST_USER_AGENT,
    _detect_static_binary_version,
)


def test_user_agent_format() -> None:
    assert CODE_ASSIST_USER_AGENT.startswith("antigravity/")
    assert not CODE_ASSIST_USER_AGENT.startswith("aidev_client/")
    assert "linux" in CODE_ASSIST_USER_AGENT or "darwin" in CODE_ASSIST_USER_AGENT
    assert "amd64" in CODE_ASSIST_USER_AGENT or "arm64" in CODE_ASSIST_USER_AGENT


def test_cloudcode_import_runs_no_subprocess() -> None:
    with patch("subprocess.run") as mock_run:
        if "antigravity_cli_switcher.manager.cloudcode" in sys.modules:
            importlib.reload(sys.modules["antigravity_cli_switcher.manager.cloudcode"])
        else:
            importlib.import_module("antigravity_cli_switcher.manager.cloudcode")
        mock_run.assert_not_called()


def test_detect_static_binary_version_extracts_from_path() -> None:
    with patch(
        "antigravity_cli_switcher.manager.cloudcode.resolve_agy_binary",
        return_value="/opt/antigravity-cli/1.4.0/agy",
    ):
        ver = _detect_static_binary_version()
        assert ver == "1.4.0"

    with patch(
        "antigravity_cli_switcher.manager.cloudcode.resolve_agy_binary",
        return_value="/home/user/.local/bin/agy-v1.3.3",
    ):
        ver = _detect_static_binary_version()
        assert ver == "1.3.3"

    with patch(
        "antigravity_cli_switcher.manager.cloudcode.resolve_agy_binary",
        side_effect=ValueError("not found"),
    ):
        ver = _detect_static_binary_version()
        assert ver == "1.3.3"
