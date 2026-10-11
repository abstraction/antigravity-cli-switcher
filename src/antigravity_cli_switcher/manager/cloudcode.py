from __future__ import annotations

import json
import platform
import re
import ssl
import subprocess
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from antigravity_cli_switcher.manager.paths import (
    _project_id_path,
    _read_text_if_exists,
    resolve_agy_binary,
)
from antigravity_cli_switcher.manager.state import (
    USAGE_FAMILY_NAMES,
    _default_usage_families,
    _normalize_timestamp,
)

CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
CODE_ASSIST_LOAD_PATH = "/v1internal:loadCodeAssist"
CODE_ASSIST_MODELS_PATH = "/v1internal:fetchAvailableModels"
CODE_ASSIST_QUOTA_PATH = "/v1internal:retrieveUserQuota"
CODE_ASSIST_QUOTA_SUMMARY_PATH = "/v1internal:retrieveUserQuotaSummary"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _get_dynamic_user_agent() -> str:
    version = "1.3.3"
    try:
        binary = resolve_agy_binary(None)
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=1.0)
        resolved_version = result.stdout.strip()
        if resolved_version:
            version = resolved_version
    except Exception:
        pass

    os_name = "darwin" if platform.system().lower() == "darwin" else "linux"
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    return f"antigravity/{version} {os_name}/{arch}"


CODE_ASSIST_USER_AGENT = _get_dynamic_user_agent()

_PLAN_TYPE_LABELS: dict[str, tuple[str, str]] = {
    # Legacy planType enum values (may appear in old API responses)
    "GEMINI_CODE_ASSIST_STARTER": ("Google AI Free", "Free"),
    "GEMINI_CODE_ASSIST": ("Google AI Pro", "Pro"),
    "CLOUD_AI_COMPANION": ("Google AI Pro", "Pro"),
    "CLOUD_AI_COMPANION_STANDARD": ("Google AI Plus", "Plus"),
    "GOOGLE_AI_PRO": ("Google AI Pro", "Pro"),
    "GOOGLE_AI_ULTRA": ("Google AI Ultra", "Ultra"),
    "GOOGLE_AI_ULTRA_MAX": ("Ultra Max", "UltMax"),
    "GEMINI_CODE_ASSIST_ENTERPRISE": ("Enterprise", "Ent"),
    "CLOUD_AI_COMPANION_ENTERPRISE": ("Enterprise", "Ent"),
    # Live currentTier.id / paidTier.id values returned by the API
    "free-tier": ("Antigravity Free", "Free"),
    "standard-tier": ("Google AI Plus", "Plus"),
    "g1-pro-tier": ("Google AI Pro", "Pro"),
    "g1-ultra-tier": ("Google AI Ultra", "Ultra"),
    "agy-business-paygo-tier": ("Business Pay-go", "Biz"),
}


def format_plan_type_label(plan_type: str | None) -> str:
    if not plan_type:
        return "-"
    return _PLAN_TYPE_LABELS.get(plan_type, (plan_type, plan_type))[0]


def format_plan_type_compact(plan_type: str | None) -> str:
    if not plan_type:
        return "?"
    return _PLAN_TYPE_LABELS.get(plan_type, (plan_type, plan_type[:6]))[1]


def _extract_plan_tier(load_response: Mapping[str, object]) -> str | None:
    """Extract plan tier ID from a loadCodeAssist response.

    The live API returns ``currentTier.id`` / ``paidTier.id`` instead of
    the legacy root ``planType`` key. When both are present, a non-free
    ``paidTier`` takes precedence over a baseline ``free-tier`` current tier,
    reflecting the user's active paid subscription (e.g. Google AI Pro).
    """
    # 1. Paid tier takes precedence if it indicates an active paid subscription
    for paid_key in ("paidTier", "paid_tier", "g1Tier", "g1_tier"):
        paid_tier = load_response.get(paid_key)
        if isinstance(paid_tier, dict):
            tier_id = paid_tier.get("id")
            if isinstance(tier_id, str) and tier_id.strip() and tier_id.strip() != "free-tier":
                return tier_id.strip()

    # 2. Current tier if non-free
    for curr_key in ("currentTier", "current_tier"):
        current_tier = load_response.get(curr_key)
        if isinstance(current_tier, dict):
            tier_id = current_tier.get("id")
            if isinstance(tier_id, str) and tier_id.strip() and tier_id.strip() != "free-tier":
                return tier_id.strip()

    # 3. Current tier fallback even if free-tier
    for curr_key in ("currentTier", "current_tier"):
        current_tier = load_response.get(curr_key)
        if isinstance(current_tier, dict):
            tier_id = current_tier.get("id")
            if isinstance(tier_id, str) and tier_id.strip():
                return tier_id.strip()

    # 4. Paid tier fallback even if free-tier
    for paid_key in ("paidTier", "paid_tier", "g1Tier", "g1_tier"):
        paid_tier = load_response.get(paid_key)
        if isinstance(paid_tier, dict):
            tier_id = paid_tier.get("id")
            if isinstance(tier_id, str) and tier_id.strip():
                return tier_id.strip()

    # 5. Legacy / mock fallback
    for key in ("planType", "plan_type"):
        raw = load_response.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    return None


def _cloudcode_request(access_token: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CODE_ASSIST_BASE_URL + path,
        data=body,
        headers={
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
            "User-Agent": CODE_ASSIST_USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Unexpected Cloud Code response type for {path}")
            return data
    except urllib.error.HTTPError as exc:
        message = ""
        try:
            payload_text = exc.read().decode("utf-8", "replace")
            payload_data = json.loads(payload_text)
            if isinstance(payload_data, dict):
                error_data = payload_data.get("error")
                if isinstance(error_data, dict) and isinstance(error_data.get("message"), str):
                    message = error_data["message"]
        except Exception:
            message = ""
        if exc.code == 401:
            raise PermissionError(message or "Cloud Code authentication failed.") from exc
        raise ValueError(message or f"Cloud Code request failed with HTTP {exc.code}.") from exc


def _google_userinfo_request(access_token: str) -> dict:
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={
            "Authorization": "Bearer " + access_token,
            "User-Agent": CODE_ASSIST_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Unexpected Google userinfo response type.")
            return data
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise PermissionError("Google userinfo authentication failed.") from exc
        raise ValueError(f"Google userinfo request failed with HTTP {exc.code}.") from exc


def _parse_model_label(value: str) -> dict | None:
    label = value.strip()
    if not label:
        return None
    variant = None
    base = label
    match = re.match(r"^(?P<base>.+?) \((?P<variant>[^()]+)\)$", label)
    if match:
        base = match.group("base").strip()
        variant = match.group("variant").strip()
    provider = None
    family = None
    parts = base.split(None, 1)
    if parts:
        provider = parts[0].strip() or None
    if len(parts) > 1:
        family = parts[1].strip() or None
    return {
        "name": label,
        "provider": provider,
        "family": family,
        "variant": variant,
    }


def _parse_summary_bucket(bucket: Mapping[str, object]) -> dict[str, object]:
    remaining = bucket.get("remaining_fraction")
    if remaining is None:
        remaining = bucket.get("remainingFraction")
    reset_raw = bucket.get("reset_time") or bucket.get("resetTime")
    reset_at = None
    if isinstance(reset_raw, str):
        reset_at = _normalize_timestamp(reset_raw.replace("Z", "+00:00"))
    return {
        "status": "known" if isinstance(remaining, (int, float)) or reset_at else "unknown",
        "value": round(float(remaining) * 100, 2) if isinstance(remaining, (int, float)) else None,
        "reset_at": reset_at,
    }


def _extract_groups_from_summary(summary_response: Mapping[str, object]) -> list[dict[str, object]]:
    groups = summary_response.get("groups")
    if isinstance(groups, list):
        return [g for g in groups if isinstance(g, dict)]
    # ConnectRPC wraps the actual payload under a "response" key
    response_field = summary_response.get("response")
    if isinstance(response_field, dict):
        resp_groups = response_field.get("groups")
        if isinstance(resp_groups, list):
            return [g for g in resp_groups if isinstance(g, dict)]
    cmd = summary_response.get("command")
    if isinstance(cmd, dict):
        cmd_data = cmd.get("data")
        if isinstance(cmd_data, dict):
            cmd_groups = cmd_data.get("groups")
            if isinstance(cmd_groups, list):
                return [g for g in cmd_groups if isinstance(g, dict)]
    data_field = summary_response.get("data")
    if isinstance(data_field, dict):
        data_groups = data_field.get("groups")
        if isinstance(data_groups, list):
            return [g for g in data_groups if isinstance(g, dict)]
    # Flat buckets fallback — wrap them in a synthetic group so callers see a non-empty list
    flat_buckets = summary_response.get("buckets")
    if isinstance(flat_buckets, list) and flat_buckets:
        return [{"buckets": flat_buckets}]
    return []


def _select_quota_summary_group(summary_response: Mapping[str, object]) -> dict[str, object] | None:
    groups = _extract_groups_from_summary(summary_response)
    if not groups:
        return None
    for group in groups:
        display_name = group.get("displayName") or group.get("display_name") or group.get("name")
        if isinstance(display_name, str) and "gemini" in display_name.lower():
            return group
    return groups[0]


def _quota_group_family(group: Mapping[str, object]) -> str | None:
    buckets = group.get("buckets")
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket_id = str(bucket.get("bucketId") or bucket.get("bucket_id") or bucket.get("id") or "").lower()
            if bucket_id.startswith("gemini-"):
                return "gemini"
            if bucket_id.startswith("3p-"):
                return "other"
    label = " ".join(str(group.get(key) or "") for key in ("displayName", "name", "description")).lower()
    if "gemini" in label:
        return "gemini"
    if "claude" in label or "gpt" in label:
        return "other"
    return None


def _parse_quota_families_from_summary(
    summary_response: Mapping[str, object],
) -> tuple[dict[str, dict[str, dict[str, object]]], int]:
    families = _default_usage_families()
    groups = _extract_groups_from_summary(summary_response)
    if not groups:
        return families, 0
    bucket_count = 0
    for group in groups:
        family = _quota_group_family(group)
        buckets = group.get("buckets")
        if family not in USAGE_FAMILY_NAMES or not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket_count += 1
            window_name = bucket.get("window")
            bucket_id = str(bucket.get("bucketId") or bucket.get("bucket_id") or bucket.get("id") or "").lower()
            if window_name == "5h" or (not window_name and "-5h" in bucket_id):
                families[family]["short"] = _parse_summary_bucket(bucket)
            elif window_name == "weekly" or (not window_name and "-weekly" in bucket_id):
                families[family]["weekly"] = _parse_summary_bucket(bucket)
    return families, bucket_count


def _parse_quota_windows_from_summary(
    summary_response: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], int]:
    families, bucket_count = _parse_quota_families_from_summary(summary_response)
    return families["gemini"]["short"], families["gemini"]["weekly"], bucket_count


def _extract_single_bucket_quota(
    quota_response: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Parse the /retrieveUserQuota fallback response.

    The live API puts ``remainingFraction`` inside ``buckets[0]``, not at root.
    Both camelCase and snake_case field names are supported.
    Returns ``(short_window, weekly_window)`` dicts.
    """
    empty: dict[str, object] = {"status": "unknown", "value": None, "reset_at": None}

    # Try root-level keys first (legacy behaviour / some mock shapes)
    root_remaining = quota_response.get("remainingFraction") or quota_response.get("remaining_fraction")
    root_reset = quota_response.get("resetTime") or quota_response.get("reset_time")

    buckets = quota_response.get("buckets")
    if isinstance(buckets, list) and buckets and isinstance(buckets[0], dict):
        first = buckets[0]
        remaining = first.get("remainingFraction") or first.get("remaining_fraction") or root_remaining
        reset_raw = first.get("resetTime") or first.get("reset_time") or root_reset
    else:
        remaining = root_remaining
        reset_raw = root_reset

    reset_at = None
    if isinstance(reset_raw, str):
        reset_at = _normalize_timestamp(reset_raw.replace("Z", "+00:00"))

    if isinstance(remaining, (int, float)):
        short_window: dict[str, object] = {
            "status": "known",
            "value": round(float(remaining) * 100, 2),
            "reset_at": reset_at,
        }
        return short_window, empty

    return empty, empty


def _persist_project_id(home_root: Path, project_id: str | None) -> None:
    if not project_id:
        return
    path = _project_id_path(home_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(project_id.strip() + "\n", encoding="utf-8")


def _extract_project_id(load_response: Mapping[str, object], home_root: Path) -> str | None:
    for key in ("cloudaicompanionProject", "cloudaicompanion_project"):
        project = load_response.get(key)
        if isinstance(project, str) and project.strip():
            _persist_project_id(home_root, project.strip())
            return project.strip()
        if isinstance(project, dict):
            project_id = project.get("id")
            if isinstance(project_id, str) and project_id.strip():
                _persist_project_id(home_root, project_id.strip())
                return project_id.strip()
    cached = _read_text_if_exists(_project_id_path(home_root))
    return cached.strip() if isinstance(cached, str) and cached.strip() else None


__all__ = [
    "CODE_ASSIST_BASE_URL",
    "CODE_ASSIST_LOAD_PATH",
    "CODE_ASSIST_MODELS_PATH",
    "CODE_ASSIST_QUOTA_PATH",
    "CODE_ASSIST_QUOTA_SUMMARY_PATH",
    "CODE_ASSIST_USER_AGENT",
    "GOOGLE_USERINFO_URL",
    "_cloudcode_request",
    "_extract_plan_tier",
    "_extract_project_id",
    "_extract_single_bucket_quota",
    "_google_userinfo_request",
    "_parse_model_label",
    "_parse_quota_families_from_summary",
    "_parse_quota_windows_from_summary",
    "_parse_summary_bucket",
    "_persist_project_id",
    "_quota_group_family",
    "_select_quota_summary_group",
    "format_plan_type_compact",
    "format_plan_type_label",
]
