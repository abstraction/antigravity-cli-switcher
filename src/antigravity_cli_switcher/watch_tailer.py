from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antigravity_cli_switcher.manager.failover import RotationResult
    from antigravity_cli_switcher.manager.paths import ManagerPaths

LOG_WATCH_STATE_NAME = "log-watch.json"
MAX_EVENT_LINE_CHARS = 300
DEFAULT_WATCH_POLL_SECONDS: float = 1.0
DEFAULT_WATCH_COOLDOWN_MINUTES: int = 60

INDIVIDUAL_QUOTA_RE = re.compile(r"Individual quota reached", re.IGNORECASE)
RESOURCE_EXHAUSTED_RE = re.compile(r"RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)", re.IGNORECASE)
WEEKLY_QUOTA_RE = re.compile(r"weekly quota reached", re.IGNORECASE)
RESET_HINT_RE = re.compile(r"Resets in\s+(?P<reset>~?[^.)]+)", re.IGNORECASE)


@dataclass(frozen=True)
class QuotaLogEvent:
    kind: str
    path: str
    reset_hint: str | None
    line: str


@dataclass
class WatchPollResult:
    events: list[QuotaLogEvent]
    rotated: bool
    rotation: RotationResult | None
    switch_mode: str
    restart_required: bool
    message: str
    files_tracked: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_watch_state_path(root: Path) -> Path:
    return root / LOG_WATCH_STATE_NAME


def _default_log_watch_state() -> dict:
    return {
        "cursors": {},
        "last_event_at": None,
        "last_kind": None,
        "last_path": None,
        "restart_required": False,
        "restart_armed_at": None,
        "restart_armed_account": None,
        "restart_source_logs": [],
        "initialized": False,
        "updated_at": None,
    }


def _normalize_log_watch_state(raw: object) -> dict:
    data = _default_log_watch_state()
    if not isinstance(raw, dict):
        return data
    cursors = raw.get("cursors")
    if isinstance(cursors, dict):
        clean: dict[str, dict[str, int]] = {}
        for key, value in cursors.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            try:
                offset = int(value.get("offset", 0) or 0)
            except (TypeError, ValueError):
                continue
            clean[key] = {"offset": max(0, offset)}
        data["cursors"] = clean
    for key in ("last_event_at", "last_kind", "last_path", "updated_at", "restart_armed_at", "restart_armed_account"):
        value = raw.get(key)
        data[key] = value if isinstance(value, str) or value is None else str(value)
    data["restart_required"] = bool(raw.get("restart_required"))
    source_logs = raw.get("restart_source_logs")
    if isinstance(source_logs, list):
        data["restart_source_logs"] = [item for item in source_logs if isinstance(item, str)]
    if "initialized" in raw:
        data["initialized"] = bool(raw.get("initialized"))
    else:
        data["initialized"] = bool(data["cursors"])
    return data


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_log_watch_state(root: Path) -> dict:
    path = log_watch_state_path(root)
    if not path.is_file():
        return _default_log_watch_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_log_watch_state()
    return _normalize_log_watch_state(raw)


def save_log_watch_state(root: Path, state: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = _normalize_log_watch_state(state)
    payload["updated_at"] = _utc_now_iso()
    _atomic_write_json(log_watch_state_path(root), payload)


def _arm_restart(state: dict, *, account: str | None, source_logs: list[str]) -> None:
    state["restart_required"] = True
    state["restart_armed_at"] = _utc_now_iso()
    state["restart_armed_account"] = account
    state["restart_source_logs"] = list(source_logs)


def _disarm_restart(state: dict, *, keep_source_logs: bool = False) -> None:
    state["restart_required"] = False
    state["restart_armed_at"] = None
    state["restart_armed_account"] = None
    if not keep_source_logs:
        state["restart_source_logs"] = []


def get_log_watch_snapshot(paths: ManagerPaths) -> dict:
    state = load_log_watch_state(paths.root)
    return {
        "files_tracked": len(state.get("cursors") or {}),
        "last_event_at": state.get("last_event_at"),
        "last_kind": state.get("last_kind"),
        "last_path": state.get("last_path"),
        "restart_required": bool(state.get("restart_required")),
        "restart_armed_at": state.get("restart_armed_at"),
        "restart_armed_account": state.get("restart_armed_account"),
        "initialized": bool(state.get("initialized")),
        "updated_at": state.get("updated_at"),
    }


def clear_restart_required(paths: ManagerPaths) -> dict:
    from antigravity_cli_switcher.manager import manager_lock

    with manager_lock(paths):
        state = load_log_watch_state(paths.root)
        _disarm_restart(state)
        save_log_watch_state(paths.root, state)
        return load_log_watch_state(paths.root)


def resolve_antigravity_cli_dir(live_dir: Path) -> Path | None:
    direct = live_dir / "antigravity-cli"
    nested = live_dir / ".gemini" / "antigravity-cli"
    if direct.is_dir():
        return direct
    if nested.is_dir():
        return nested
    return None


def iter_live_agy_log_files(live_dir: Path | None) -> list[Path]:
    if live_dir is None:
        return []
    base_dir = resolve_antigravity_cli_dir(live_dir)
    if base_dir is None:
        return []
    candidates: list[Path] = []
    cli_log = base_dir / "cli.log"
    if cli_log.is_file():
        candidates.append(cli_log)
    log_dir = base_dir / "log"
    if log_dir.is_dir():
        try:
            log_files = sorted(
                (path for path in log_dir.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            log_files = []
        candidates.extend(log_files)
    return candidates


def parse_quota_log_line(line: str) -> QuotaLogEvent | None:
    text = line.strip()
    if not text:
        return None
    reset_match = RESET_HINT_RE.search(text)
    reset_hint = reset_match.group("reset").strip() if reset_match else None
    if INDIVIDUAL_QUOTA_RE.search(text) or (RESOURCE_EXHAUSTED_RE.search(text) and "quota reached" in text.lower()):
        kind = "individual_quota"
    elif WEEKLY_QUOTA_RE.search(text):
        kind = "weekly_quota"
    else:
        return None
    clipped = text if len(text) <= MAX_EVENT_LINE_CHARS else text[: MAX_EVENT_LINE_CHARS - 3] + "..."
    return QuotaLogEvent(kind=kind, path="", reset_hint=reset_hint, line=clipped)


def read_new_complete_lines(path: Path, offset: int) -> tuple[int, list[str]]:
    try:
        size = path.stat().st_size
    except OSError:
        return offset, []
    if size < offset:
        offset = 0
    if size == offset:
        return offset, []
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return offset, []
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return offset, []
    chunk = data[: last_nl + 1]
    new_offset = offset + len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    return new_offset, text.splitlines()


def initial_log_offset(
    path: Path,
    *,
    from_start: bool,
    started_at: float | None = None,
    known: bool = False,
    initialized: bool = False,
) -> int:
    del started_at
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if from_start:
        return 0
    if known:
        return 0
    if initialized:
        return 0
    return size


def consume_log_events(
    live_dir: Path | None,
    cursors: dict[str, dict[str, int]],
    *,
    from_start: bool = False,
    started_at: float | None = None,
    initialized: bool = False,
) -> tuple[dict[str, dict[str, int]], list[QuotaLogEvent]]:
    events: list[QuotaLogEvent] = []
    next_cursors = dict(cursors)
    for path in iter_live_agy_log_files(live_dir):
        key = str(path)
        known = key in next_cursors
        if known and not from_start:
            offset = int(next_cursors[key].get("offset", 0) or 0)
        else:
            offset = initial_log_offset(
                path,
                from_start=from_start,
                started_at=started_at,
                known=known,
                initialized=initialized,
            )
        new_offset, lines = read_new_complete_lines(path, offset)
        next_cursors[key] = {"offset": new_offset}
        for line in lines:
            parsed = parse_quota_log_line(line)
            if parsed is None:
                continue
            events.append(
                QuotaLogEvent(
                    kind=parsed.kind,
                    path=key,
                    reset_hint=parsed.reset_hint,
                    line=parsed.line,
                )
            )
    return next_cursors, events
