from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

from antigravity_cli_switcher.manager.keyring import (
    _IN_MEMORY_KEYRING,
    KEYRING_ACCOUNT,
    KEYRING_SERVICE,
    _oauth_token_path,
    _sync_keyring_to_home,
)
from antigravity_cli_switcher.manager.quota import _refresh_token_http


def test_refresh_token_http_writes_atomically_with_0600_permissions(tmp_path: Path) -> None:
    source_home = tmp_path / "account_home"
    tok_path = _oauth_token_path(source_home)
    tok_path.parent.mkdir(parents=True, exist_ok=True)

    initial_data = {
        "token": {
            "refresh_token": "valid_refresh_token",
            "access_token": "initial_access_token",
            "expiry": "2026-01-01T00:00:00Z",
        }
    }
    tok_path.write_text(json.dumps(initial_data), encoding="utf-8")

    mock_resp_data = {"access_token": "new_access_token_123", "expires_in": 3600}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        success = _refresh_token_http(source_home)
        assert success is True

    # Assert file updated
    updated_data = json.loads(tok_path.read_text(encoding="utf-8"))
    assert updated_data["token"]["access_token"] == "new_access_token_123"

    # Assert permissions are 0o600
    file_mode = stat.S_IMODE(tok_path.stat().st_mode)
    assert file_mode == 0o600

    # Assert no .tmp files left in parent directory
    temp_files = list(tok_path.parent.glob(".tmp_*"))
    assert len(temp_files) == 0


def test_sync_keyring_to_home_writes_atomically_with_0600_permissions(tmp_path: Path) -> None:
    target_home = tmp_path / "sync_home"
    tok_path = _oauth_token_path(target_home)

    keyring_data = {
        "token": {
            "access_token": "keyring_access_token",
            "refresh_token": "keyring_refresh_token",
        }
    }
    _IN_MEMORY_KEYRING[(KEYRING_SERVICE, KEYRING_ACCOUNT)] = json.dumps(keyring_data)

    try:
        success = _sync_keyring_to_home(target_home)
        assert success is True

        assert tok_path.is_file()
        file_mode = stat.S_IMODE(tok_path.stat().st_mode)
        assert file_mode == 0o600

        loaded = json.loads(tok_path.read_text(encoding="utf-8"))
        assert loaded["token"]["access_token"] == "keyring_access_token"

        temp_files = list(tok_path.parent.glob(".tmp_*"))
        assert len(temp_files) == 0
    finally:
        _IN_MEMORY_KEYRING.pop((KEYRING_SERVICE, KEYRING_ACCOUNT), None)
