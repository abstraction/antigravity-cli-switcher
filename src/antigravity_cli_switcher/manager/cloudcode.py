from __future__ import annotations

import json
import platform
import re
import ssl
import subprocess
import urllib.request

from antigravity_cli_switcher.manager.state import (
    USAGE_FAMILY_NAMES,
    _default_usage_families,
    _normalize_timestamp,
)

CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
CODE_ASSIST_LOAD_PATH = "/v1internal:loadCodeAssist"
CODE_ASSIST_QUOTA_PATH = "/v1internal:retrieveUserQuota"
CODE_ASSIST_QUOTA_SUMMARY_PATH = "/v1internal:retrieveUserQuotaSummary"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _get_dynamic_user_agent() -> str:
    try:
        result = subprocess.run(["agy", "--version"], capture_output=True, text=True, timeout=1.0)
        version = result.stdout.strip()
        if not version:
            version = "1.2.1"
    except Exception:
        version = "1.2.1"

    os_name = "darwin" if platform.system().lower() == "darwin" else "linux"
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    return f"antigravity/{version} {os_name}/{arch}"


CODE_ASSIST_USER_AGENT = _get_dynamic_user_agent()

_PLAN_TYPE_LABELS: dict[str, tuple[str, str]] = {
    "GEMINI_CODE_ASSIST_STARTER": ("Google AI Free", "Free"),
    "GEMINI_CODE_ASSIST": ("Google AI Pro", "Pro"),
    "CLOUD_AI_COMPANION": ("Google AI Pro", "Pro"),
    "CLOUD_AI_COMPANION_STANDARD": ("Google AI Plus", "Plus"),
    "GOOGLE_AI_PRO": ("Google AI Pro", "Pro"),
    "GOOGLE_AI_ULTRA": ("Google AI Ultra", "Ultra"),
    "GOOGLE_AI_ULTRA_MAX": ("Ultra Max", "UltMax"),
    "GEMINI_CODE_ASSIST_ENTERPRISE": ("Enterprise", "Ent"),
    "CLOUD_AI_COMPANION_ENTERPRISE": ("Enterprise", "Ent"),
}


def format_plan_type_label(plan_type: str | None) -> str:
    if not plan_type:
        return "-"
    return _PLAN_TYPE_LABELS.get(plan_type, (plan_type, plan_type))[0]


def format_plan_type_compact(plan_type: str | None) -> str:
    if not plan_type:
        return "?"
    return _PLAN_TYPE_LABELS.get(plan_type, (plan_type, plan_type[:6]))[1]


def _cloudcode_request(access_token: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CODE_ASSIST_BASE_URL + path,
        data=body,
        headers={
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
            "User-Agent": CODE_ASSIST_USER_AGENT,
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


def _parse_summary_bucket(bucket: dict) -> dict:
    remaining = bucket.get("remainingFraction")
    reset_raw = bucket.get("resetTime")
    reset_at = None
    if isinstance(reset_raw, str):
        reset_at = _normalize_timestamp(reset_raw.replace("Z", "+00:00"))
    return {
        "status": "known" if isinstance(remaining, (int, float)) or reset_at else "unknown",
        "value": round(float(remaining) * 100, 2) if isinstance(remaining, (int, float)) else None,
        "reset_at": reset_at,
    }


def _select_quota_summary_group(summary_response: dict) -> dict | None:
    groups = summary_response.get("groups")
    if not isinstance(groups, list):
        return None
    normalized = [group for group in groups if isinstance(group, dict)]
    if not normalized:
        return None
    for group in normalized:
        display_name = group.get("displayName")
        if isinstance(display_name, str) and "gemini" in display_name.lower():
            return group
    return normalized[0]


def _quota_group_family(group: dict) -> str | None:
    buckets = group.get("buckets")
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket_id = str(bucket.get("bucketId") or "").lower()
            if bucket_id.startswith("gemini-"):
                return "gemini"
            if bucket_id.startswith("3p-"):
                return "other"
    label = " ".join(str(group.get(key) or "") for key in ("displayName", "description")).lower()
    if "gemini" in label:
        return "gemini"
    if "claude" in label or "gpt" in label:
        return "other"
    return None


def _parse_quota_families_from_summary(summary_response: dict) -> tuple[dict, int]:
    families = _default_usage_families()
    groups = summary_response.get("groups")
    if not isinstance(groups, list):
        return families, 0
    bucket_count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        family = _quota_group_family(group)
        buckets = group.get("buckets")
        if family not in USAGE_FAMILY_NAMES or not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bucket_count += 1
            window_name = bucket.get("window")
            if window_name == "5h":
                families[family]["short"] = _parse_summary_bucket(bucket)
            elif window_name == "weekly":
                families[family]["weekly"] = _parse_summary_bucket(bucket)
    return families, bucket_count


def _parse_quota_windows_from_summary(summary_response: dict) -> tuple[dict, dict, int]:
    families, bucket_count = _parse_quota_families_from_summary(summary_response)
    return families["gemini"]["short"], families["gemini"]["weekly"], bucket_count


__all__ = [
    "CODE_ASSIST_BASE_URL",
    "CODE_ASSIST_LOAD_PATH",
    "CODE_ASSIST_QUOTA_PATH",
    "CODE_ASSIST_QUOTA_SUMMARY_PATH",
    "CODE_ASSIST_USER_AGENT",
    "GOOGLE_USERINFO_URL",
    "_cloudcode_request",
    "_google_userinfo_request",
    "_parse_model_label",
    "_parse_quota_families_from_summary",
    "_parse_quota_windows_from_summary",
    "_parse_summary_bucket",
    "_quota_group_family",
    "_select_quota_summary_group",
    "format_plan_type_compact",
    "format_plan_type_label",
]
