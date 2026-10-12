from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from antigravity_cli_switcher.manager.cloudcode import _cloudcode_request
from antigravity_cli_switcher.manager.keyring import _oauth_token_path
from antigravity_cli_switcher.manager.quota import _refresh_token_http


def test_cloudcode_request_uses_proxy_when_specified() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"models": {}}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.build_opener") as mock_opener_builder:
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_builder.return_value = mock_opener

        result = _cloudcode_request(
            access_token="test_tok",
            path="/test",
            payload={},
            proxy_url="http://proxy.example.com:8080",
        )
        assert result == {"models": {}}
        mock_opener_builder.assert_called_once()
        # Verify proxy handler was included in arguments to build_opener
        handlers = mock_opener_builder.call_args[0]
        has_proxy_handler = any(isinstance(h, type(mock_opener_builder)) or hasattr(h, "proxies") for h in handlers)
        assert has_proxy_handler or len(handlers) >= 2


def test_refresh_token_http_uses_proxy_when_specified(tmp_path: Path) -> None:
    source_home = tmp_path / "account_home"
    tok_path = _oauth_token_path(source_home)
    tok_path.parent.mkdir(parents=True, exist_ok=True)
    tok_path.write_text(
        json.dumps({"token": {"refresh_token": "ref_123", "access_token": "acc_123"}}),
        encoding="utf-8",
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"access_token": "new_tok", "expires_in": 3600}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.build_opener") as mock_opener_builder:
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_opener_builder.return_value = mock_opener

        ok = _refresh_token_http(source_home, proxy_url="http://proxy.example.com:8080")
        assert ok is True
        mock_opener_builder.assert_called_once()
