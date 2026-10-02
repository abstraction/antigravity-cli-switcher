from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from antigravity_cli_switcher.log import get_logger
from antigravity_cli_switcher.manager.paths import _oauth_token_path, _read_json_if_exists, _resolve_home_source

KEYRING_SERVICE = "gemini"
KEYRING_ACCOUNT = "antigravity"

_IN_MEMORY_KEYRING: dict[tuple[str, str], str] = {}


def _decode_jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_test_environment() -> bool:
    """Return True if running inside a test framework or test mode is enabled."""
    if os.environ.get("ACS_TEST_MODE") == "1":
        return True
    if "unittest" in sys.modules or "pytest" in sys.modules:
        if os.environ.get("ACS_FORCE_REAL_KEYRING") != "1":
            return True
    return False


def is_synthetic_or_test_token(token_data: dict | str | None) -> bool:
    """Check if token data contains synthetic or test signatures."""
    if not token_data:
        return False
    if isinstance(token_data, str):
        try:
            token_data = json.loads(token_data)
        except Exception:
            return any(sig in token_data for sig in ("ya29.fake", "wrong@gmail.com", "1//fake", "ya29.test"))
    if not isinstance(token_data, dict):
        return False
    tok = token_data.get("token")
    if isinstance(tok, dict):
        access = str(tok.get("access_token", ""))
        refresh = str(tok.get("refresh_token", ""))
        if access.startswith(("ya29.fake", "fake", "ya29.test")) or "fake" in access.lower():
            return True
        if refresh in {"1//fake", "fake"}:
            return True
    id_tok = token_data.get("id_token")
    if isinstance(id_tok, str):
        payload = _decode_jwt_payload(id_tok)
        if payload and isinstance(payload, dict):
            email = str(payload.get("email", "")).lower()
            if email in {"wrong@gmail.com", "test@gmail.com", "fake@gmail.com"} or email.endswith(
                ("@example.com", "@fake.com")
            ):
                return True
    return False


def _load_antigravity_token_state(home_root: Path) -> dict:
    path = _oauth_token_path(home_root)
    data = _read_json_if_exists(path)
    if isinstance(data, dict) and isinstance(data.get("token"), dict):
        return data
    if not isinstance(data, dict):
        raise ValueError(f"Antigravity token file not found or invalid: {path}")
    token = data.get("token")
    if not isinstance(token, dict):
        raise ValueError(f"Antigravity token payload missing token object: {path}")
    return data


def _load_keyring_token() -> dict | None:
    if _is_test_environment():
        val = _IN_MEMORY_KEYRING.get((KEYRING_SERVICE, KEYRING_ACCOUNT))
        if val:
            try:
                data = json.loads(val)
                if isinstance(data, dict) and "token" in data:
                    return data
            except Exception:
                pass
        return None

    try:
        import keyring  # type: ignore[import-not-found]

        secret = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if secret:
            try:
                data = json.loads(secret)
                if isinstance(data, dict) and "token" in data:
                    return data
            except Exception:
                pass
    except Exception:
        pass

    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        for attr_key in ("username", "account"):
            try:
                proc = subprocess.run(
                    ["secret-tool", "lookup", "service", KEYRING_SERVICE, attr_key, KEYRING_ACCOUNT],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout.strip())
                    if isinstance(data, dict) and "token" in data:
                        return data
            except Exception:
                pass

    if sys.platform == "darwin" and shutil.which("security"):
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                if isinstance(data, dict) and "token" in data:
                    return data
        except Exception:
            pass

    return None


def _save_keyring_token(token_data: dict | str) -> bool:
    token_str = json.dumps(token_data) if isinstance(token_data, dict) else str(token_data)

    if _is_test_environment():
        _IN_MEMORY_KEYRING[(KEYRING_SERVICE, KEYRING_ACCOUNT)] = token_str
        return True

    if is_synthetic_or_test_token(token_data):
        get_logger().warning("Blocked attempt to save synthetic/test token to global OS keyring.")
        return False

    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, token_str)
        return True
    except Exception:
        pass

    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        try:
            proc = subprocess.run(
                [
                    "secret-tool",
                    "store",
                    f"--label=Password for '{KEYRING_ACCOUNT}' on '{KEYRING_SERVICE}'",
                    "service",
                    KEYRING_SERVICE,
                    "username",
                    KEYRING_ACCOUNT,
                ],
                input=token_str,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                return True
        except Exception:
            pass

    if sys.platform == "darwin" and shutil.which("security"):
        try:
            proc = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    KEYRING_SERVICE,
                    "-a",
                    KEYRING_ACCOUNT,
                    "-w",
                    token_str,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                return True
        except Exception:
            pass

    return False


def _clear_keyring_token() -> bool:
    if _is_test_environment():
        _IN_MEMORY_KEYRING.pop((KEYRING_SERVICE, KEYRING_ACCOUNT), None)
        return True

    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass

    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        for attr_key in ("username", "account"):
            try:
                subprocess.run(
                    ["secret-tool", "clear", "service", KEYRING_SERVICE, attr_key, KEYRING_ACCOUNT],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass

    if sys.platform == "darwin" and shutil.which("security"):
        try:
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            pass

    return True


def _sync_keyring_to_home(target_home: Path) -> bool:
    token_data = _load_keyring_token()
    if not isinstance(token_data, dict) or not isinstance(token_data.get("token"), dict):
        return False
    if not _is_test_environment() and is_synthetic_or_test_token(token_data):
        get_logger().warning("Blocked sync of synthetic token from OS keyring to home %s", target_home)
        return False
    token_path = _oauth_token_path(_resolve_home_source(target_home))
    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token_path.write_text(json.dumps(token_data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _sync_home_to_keyring(source_home: Path) -> bool:
    _clear_keyring_token()
    try:
        data = _load_antigravity_token_state(_resolve_home_source(source_home))
        if not _is_test_environment() and is_synthetic_or_test_token(data):
            get_logger().warning("Blocked sync of synthetic token from %s to OS keyring.", source_home)
            return False
        return _save_keyring_token(data)
    except Exception:
        return False


@contextmanager
def _isolated_keyring_warmup(source_home: Path) -> Iterator[None]:
    restore_keyring = _load_keyring_token()
    if restore_keyring and not _is_test_environment() and is_synthetic_or_test_token(restore_keyring):
        get_logger().warning("Discarded synthetic token found in keyring prior to warmup.")
        restore_keyring = None
    _clear_keyring_token()
    _sync_home_to_keyring(source_home)
    try:
        yield
    finally:
        _sync_keyring_to_home(source_home)
        if restore_keyring:
            _save_keyring_token(restore_keyring)
        else:
            _clear_keyring_token()
