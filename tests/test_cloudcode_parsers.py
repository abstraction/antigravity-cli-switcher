"""Unit tests for CloudCode schema adaptations, parsers, and identity aliases."""

from __future__ import annotations

import base64
import json
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from antigravity_cli_switcher.cli.formatters import _get_nearest_reset, _problem_badge
from antigravity_cli_switcher.manager.cloudcode import (
    CODE_ASSIST_MODELS_PATH,
    _extract_groups_from_summary,
    _extract_plan_tier,
    _extract_single_bucket_quota,
    _parse_quota_windows_from_summary,
    format_plan_type_label,
)
from antigravity_cli_switcher.manager.keyring import is_synthetic_or_test_token
from antigravity_cli_switcher.manager.models_catalog import _parse_raw_models, list_models
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.models import (
    AccountIdentity,
    AccountMeta,
)
from antigravity_cli_switcher.tui.formatters import format_problem_summary
from antigravity_cli_switcher.tui.theme import format_health_badge


class TestPlanTierExtraction(unittest.TestCase):
    def test_extract_plan_tier_current_tier(self) -> None:
        payload = {"currentTier": {"id": "g1-pro-tier"}}
        self.assertEqual(_extract_plan_tier(payload), "g1-pro-tier")

    def test_extract_plan_tier_paid_tier(self) -> None:
        payload = {"paidTier": {"id": "standard-tier"}}
        self.assertEqual(_extract_plan_tier(payload), "standard-tier")

    def test_extract_plan_tier_paid_tier_precedence_over_free_current(self) -> None:
        payload = {
            "currentTier": {"id": "free-tier"},
            "paidTier": {"id": "g1-pro-tier", "name": "Google AI Pro"},
            "planType": "GEMINI_CODE_ASSIST",
        }
        self.assertEqual(_extract_plan_tier(payload), "g1-pro-tier")

    def test_extract_plan_tier_paid_standard_tier_precedence(self) -> None:
        payload = {
            "currentTier": {"id": "free-tier"},
            "paidTier": {"id": "standard-tier"},
        }
        self.assertEqual(_extract_plan_tier(payload), "standard-tier")

    def test_extract_plan_tier_both_free(self) -> None:
        payload = {
            "currentTier": {"id": "free-tier"},
            "paidTier": {"id": "free-tier"},
        }
        self.assertEqual(_extract_plan_tier(payload), "free-tier")

    def test_extract_plan_tier_root_plan_type_camel_case(self) -> None:
        payload = {"planType": "GEMINI_CODE_ASSIST"}
        self.assertEqual(_extract_plan_tier(payload), "GEMINI_CODE_ASSIST")

    def test_extract_plan_tier_root_plan_type_snake_case(self) -> None:
        payload = {"plan_type": "GOOGLE_AI_ULTRA"}
        self.assertEqual(_extract_plan_tier(payload), "GOOGLE_AI_ULTRA")

    def test_extract_plan_tier_unknown_and_empty(self) -> None:
        self.assertIsNone(_extract_plan_tier({}))
        self.assertIsNone(_extract_plan_tier({"other": "field"}))
        self.assertIsNone(_extract_plan_tier({"currentTier": {}}))
        self.assertIsNone(_extract_plan_tier({"currentTier": {"id": "   "}}))
        self.assertIsNone(_extract_plan_tier({"planType": "  "}))
        self.assertIsNone(_extract_plan_tier({"currentTier": "not-a-dict"}))
        self.assertIsNone(_extract_plan_tier({"paidTier": 12345}))
        self.assertIsNone(_extract_plan_tier({"planType": 123}))


class TestSingleBucketQuotaExtraction(unittest.TestCase):
    def test_extract_single_bucket_quota_nested_buckets(self) -> None:
        payload = {
            "buckets": [
                {
                    "remainingFraction": 0.85,
                    "resetTime": "2026-10-12T12:00:00Z",
                }
            ]
        }
        short_window, weekly_window = _extract_single_bucket_quota(payload)
        self.assertEqual(short_window["status"], "known")
        self.assertEqual(short_window["value"], 85.0)
        self.assertEqual(short_window["reset_at"], "2026-10-12T12:00:00+00:00")
        self.assertEqual(weekly_window["status"], "unknown")
        self.assertIsNone(weekly_window["value"])

    def test_extract_single_bucket_quota_root_camel_case(self) -> None:
        payload = {
            "remainingFraction": 0.5,
            "resetTime": "2026-10-12T08:00:00Z",
        }
        short_window, weekly_window = _extract_single_bucket_quota(payload)
        self.assertEqual(short_window["status"], "known")
        self.assertEqual(short_window["value"], 50.0)
        self.assertEqual(short_window["reset_at"], "2026-10-12T08:00:00+00:00")
        self.assertEqual(weekly_window["status"], "unknown")

    def test_extract_single_bucket_quota_snake_case(self) -> None:
        payload = {
            "buckets": [
                {
                    "remaining_fraction": 0.3333,
                    "reset_time": "2026-10-12T15:30:00Z",
                }
            ]
        }
        short_window, _ = _extract_single_bucket_quota(payload)
        self.assertEqual(short_window["status"], "known")
        self.assertEqual(short_window["value"], 33.33)
        self.assertEqual(short_window["reset_at"], "2026-10-12T15:30:00+00:00")

    def test_extract_single_bucket_quota_empty_payload(self) -> None:
        short_window, weekly_window = _extract_single_bucket_quota({})
        self.assertEqual(short_window["status"], "unknown")
        self.assertIsNone(short_window["value"])
        self.assertEqual(weekly_window["status"], "unknown")
        self.assertIsNone(weekly_window["value"])

        empty_buckets, _ = _extract_single_bucket_quota({"buckets": []})
        self.assertEqual(empty_buckets["status"], "unknown")

    def test_extract_single_bucket_quota_root_snake_case(self) -> None:
        payload = {
            "remaining_fraction": 0.25,
            "reset_time": "2026-10-12T18:00:00Z",
        }
        short_window, weekly_window = _extract_single_bucket_quota(payload)
        self.assertEqual(short_window["status"], "known")
        self.assertEqual(short_window["value"], 25.0)
        self.assertEqual(short_window["reset_at"], "2026-10-12T18:00:00+00:00")
        self.assertEqual(weekly_window["status"], "unknown")


class TestSummaryGroupsExtraction(unittest.TestCase):
    def test_extract_groups_connectrpc_response_wrapper(self) -> None:
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini",
                        "buckets": [{"bucketId": "gemini-5h", "remainingFraction": 0.9}],
                    }
                ]
            }
        }
        groups = _extract_groups_from_summary(payload)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["displayName"], "Gemini")

    def test_extract_groups_command_and_data_wrappers(self) -> None:
        cmd_payload = {"command": {"data": {"groups": [{"name": "group_cmd"}]}}}
        self.assertEqual(len(_extract_groups_from_summary(cmd_payload)), 1)

        data_payload = {"data": {"groups": [{"name": "group_data"}]}}
        self.assertEqual(len(_extract_groups_from_summary(data_payload)), 1)

    def test_extract_groups_flat_buckets_fallback(self) -> None:
        payload = {
            "buckets": [
                {
                    "bucketId": "gemini-5h",
                    "remainingFraction": 0.75,
                    "resetTime": "2026-10-12T10:00:00Z",
                }
            ]
        }
        groups = _extract_groups_from_summary(payload)
        self.assertEqual(len(groups), 1)
        self.assertIn("buckets", groups[0])

        short_win, _, bucket_count = _parse_quota_windows_from_summary(payload)
        self.assertEqual(bucket_count, 1)
        self.assertEqual(short_win["value"], 75.0)


class TestModelsCatalogDualFormat(unittest.TestCase):
    def test_parse_raw_models_live_api_dict_format(self) -> None:
        raw = {
            "gemini-2.5-pro": {
                "displayName": "Gemini 2.5 Pro (Thinking)",
                "quotaInfo": {"remainingFraction": 1.0},
            },
            "gemini-2.5-flash": {
                "displayName": "Gemini 2.5 Flash",
            },
            "claude-3-5-sonnet": {},
        }
        models = _parse_raw_models(raw)
        self.assertEqual(len(models), 3)
        names = [m["name"] for m in models]
        self.assertIn("Gemini 2.5 Pro (Thinking)", names)
        self.assertIn("Gemini 2.5 Flash", names)
        self.assertIn("claude-3-5-sonnet", names)

    def test_parse_raw_models_legacy_list_format(self) -> None:
        raw_strings = ["gemini-2.5-pro", "gemini-2.5-flash"]
        models_str = _parse_raw_models(raw_strings)
        self.assertEqual(len(models_str), 2)
        self.assertEqual(models_str[0]["name"], "gemini-2.5-pro")

        raw_dicts = [{"displayName": "Gemini 2.5 Pro"}, {"name": "claude-3-5-sonnet"}]
        models_dict = _parse_raw_models(raw_dicts)
        self.assertEqual(len(models_dict), 2)
        self.assertEqual(models_dict[0]["name"], "Gemini 2.5 Pro")
        self.assertEqual(models_dict[1]["name"], "claude-3-5-sonnet")

    def test_parse_raw_models_empty_and_invalid(self) -> None:
        self.assertEqual(_parse_raw_models(None), [])
        self.assertEqual(_parse_raw_models({}), [])
        self.assertEqual(_parse_raw_models([]), [])
        self.assertEqual(_parse_raw_models("unexpected_string"), [])

    def _run_list_models_with_mocks(
        self,
        *,
        cloudcode_resp: Mapping[str, object] | None = None,
        cloudcode_err: Exception | None = None,
        project_id: str | None = "project-12345",
        fallback_models: Sequence[Mapping[str, object]] | None = None,
    ) -> tuple[dict[str, object], mock.MagicMock, mock.MagicMock]:
        paths = ManagerPaths(
            root=Path("/tmp/synthetic_root"),
            accounts_dir=Path("/tmp/synthetic_root/accounts"),
            state_file=Path("/tmp/synthetic_root/state.json"),
            runtime_dir=Path("/tmp/synthetic_root/runtime"),
            lock_file=Path("/tmp/synthetic_root/lock"),
        )
        mock_run_agy = mock.MagicMock(return_value=fallback_models or [])
        if cloudcode_err:
            mock_req = mock.MagicMock(side_effect=cloudcode_err)
        else:
            mock_req = mock.MagicMock(return_value=cloudcode_resp or {})

        with (
            mock.patch("antigravity_cli_switcher.manager.models_catalog.manager_lock"),
            mock.patch(
                "antigravity_cli_switcher.manager.models_catalog.sync_state_from_disk", return_value=mock.MagicMock()
            ),
            mock.patch("antigravity_cli_switcher.manager.models_catalog.load_state"),
            mock.patch(
                "antigravity_cli_switcher.manager.quota._resolve_usage_refresh_target",
                return_value=("acc_alpha", Path("/tmp/synthetic_root/accounts/acc_alpha")),
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.models_catalog.check_token_account_match",
                return_value=mock.MagicMock(status="ok"),
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
                return_value="synthetic_access_token",
            ),
            mock.patch("antigravity_cli_switcher.manager.models_catalog._extract_project_id", return_value=project_id),
            mock.patch("antigravity_cli_switcher.manager.models_catalog._cloudcode_request", mock_req),
            mock.patch("antigravity_cli_switcher.manager.models_catalog._isolated_keyring_warmup"),
            mock.patch("antigravity_cli_switcher.manager.models_catalog._run_agy_models_command", mock_run_agy),
        ):
            result = list_models(paths, name="acc_alpha")
            return result, mock_req, mock_run_agy

    def test_list_models_live_api_request_and_payload(self) -> None:
        fake_resp = {"models": {"gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro"}}}
        result, mock_req, _ = self._run_list_models_with_mocks(cloudcode_resp=fake_resp)
        self.assertEqual(result["count"], 1)
        mock_req.assert_called_once_with(
            "synthetic_access_token",
            CODE_ASSIST_MODELS_PATH,
            {"project": "project-12345"},
        )

    def test_list_models_legacy_list_format(self) -> None:
        fake_resp = {"models": ["gemini-2.5-pro", "gemini-2.5-flash"]}
        result, _, _ = self._run_list_models_with_mocks(cloudcode_resp=fake_resp)
        self.assertEqual(result["count"], 2)

    def test_list_models_fallback_on_empty_api(self) -> None:
        fallback = [{"name": "fallback-model", "provider": "fallback", "family": None, "variant": None}]
        result, mock_req, mock_agy = self._run_list_models_with_mocks(
            cloudcode_resp={"models": {}}, project_id=None, fallback_models=fallback
        )
        mock_req.assert_called_once_with("synthetic_access_token", CODE_ASSIST_MODELS_PATH, {})
        mock_agy.assert_called_once()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["models"], fallback)

    def test_list_models_fallback_on_api_error(self) -> None:
        fallback = [{"name": "fallback-model", "provider": "fallback", "family": None, "variant": None}]
        result, _, mock_agy = self._run_list_models_with_mocks(
            cloudcode_err=ValueError("connection refused"), fallback_models=fallback
        )
        mock_agy.assert_called_once()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["models"], fallback)

    def test_list_models_connectrpc_wrapped_response(self) -> None:
        wrapped_resp = {
            "response": {
                "models": {
                    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro (Thinking)"},
                }
            }
        }
        result, _, _ = self._run_list_models_with_mocks(cloudcode_resp=wrapped_resp)
        self.assertEqual(result["count"], 1)
        models = result.get("models")
        self.assertIsInstance(models, list)
        if isinstance(models, list):
            self.assertEqual(models[0].get("name"), "Gemini 2.5 Pro (Thinking)")


class TestAccountIdentityValidation(unittest.TestCase):
    def test_identity_alias_display_name(self) -> None:
        data = {"email": "user1@example.com", "display_name": "Test User"}
        ident = AccountIdentity.model_validate(data)
        self.assertEqual(ident.email, "user1@example.com")
        self.assertEqual(ident.name, "Test User")

    def test_identity_alias_camel_case_display_name(self) -> None:
        data = {"email": "user1@example.com", "displayName": "Camel User"}
        ident = AccountIdentity.model_validate(data)
        self.assertEqual(ident.email, "user1@example.com")
        self.assertEqual(ident.name, "Camel User")

    def test_identity_name_direct(self) -> None:
        data = {"email": "user1@example.com", "name": "Direct User"}
        ident = AccountIdentity.model_validate(data)
        self.assertEqual(ident.name, "Direct User")

    def test_identity_validate_display_name(self) -> None:
        ident = AccountIdentity.model_validate({"display_name": "Validated User"})
        self.assertEqual(ident.name, "Validated User")

    def test_account_meta_with_display_name_in_identity(self) -> None:
        meta = AccountMeta.model_validate(
            {
                "identity": {
                    "email": "user1@example.com",
                    "display_name": "Nested Display User",
                }
            }
        )
        self.assertIsNotNone(meta.identity)
        if meta.identity:
            self.assertEqual(meta.identity.name, "Nested Display User")


class TestFormattersAndBadges(unittest.TestCase):
    def test_problem_badge_oauth_rotated(self) -> None:
        self.assertEqual(_problem_badge("oauth_rotated"), "ROTATE")

    def test_problem_summary_oauth_rotated(self) -> None:
        summary = format_problem_summary("oauth_rotated")
        self.assertEqual(summary, "OAuth credentials rotated (relogin required)")

    def test_health_badge_oauth_rotated(self) -> None:
        badge = format_health_badge("oauth_rotated")
        self.assertEqual(badge.plain, "ROTATE")
        self.assertEqual(badge.style, "bold red reverse")

    def test_nearest_reset_usage_families(self) -> None:
        now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
        meta = {
            "usage_families": {
                "gemini": {
                    "short": {"reset_at": "2026-10-12T14:00:00Z"},
                    "weekly": {"reset_at": "2026-10-15T10:00:00Z"},
                },
                "other": {
                    "short": {"reset_at": "2026-10-12T12:00:00Z"},
                    "weekly": {"reset_at": "2026-10-16T10:00:00Z"},
                },
            }
        }
        nearest = _get_nearest_reset(meta, now)
        self.assertEqual(nearest, "2026-10-12T12:00:00+00:00")

    def test_nearest_reset_account_meta_model(self) -> None:
        now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
        meta_model = AccountMeta.model_validate(
            {
                "usage_families": {
                    "gemini": {
                        "short": {"reset_at": "2026-10-12T16:00:00Z"},
                        "weekly": {"reset_at": "2026-10-19T10:00:00Z"},
                    }
                }
            }
        )
        nearest = _get_nearest_reset(meta_model, now)
        self.assertEqual(nearest, "2026-10-12T16:00:00+00:00")

    def test_nearest_reset_naive_datetime(self) -> None:
        now_naive = datetime(2026, 10, 12, 10, 0, 0)
        meta = {
            "usage_windows": {
                "short": {"reset_at": "2026-10-12T15:00:00Z"},
            }
        }
        nearest = _get_nearest_reset(meta, now_naive)
        self.assertEqual(nearest, "2026-10-12T15:00:00+00:00")

    def test_format_plan_type_label(self) -> None:
        self.assertEqual(format_plan_type_label("g1-pro-tier"), "Google AI Pro")
        self.assertEqual(format_plan_type_label("free-tier"), "Antigravity Free")
        self.assertEqual(format_plan_type_label("standard-tier"), "Google AI Plus")
        self.assertEqual(format_plan_type_label("unknown-custom"), "unknown-custom")
        self.assertEqual(format_plan_type_label(None), "-")


def _make_jwt(email: str, sub: str = "123456789", name: str = "Test User") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "email": email,
                    "sub": sub,
                    "name": name,
                    "email_verified": True,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


class TestKeyringSyntheticToken(unittest.TestCase):
    def test_nested_id_token_detected(self) -> None:
        jwt_fake = _make_jwt("developer@example.com")
        token_payload = {
            "token": {
                "access_token": "ya29.authentic_looking_token",
                "refresh_token": "1//authentic_looking_token",
                "id_token": jwt_fake,
            }
        }
        self.assertTrue(is_synthetic_or_test_token(token_payload))

    def test_nested_id_token_authentic_not_flagged(self) -> None:
        jwt_real = _make_jwt("authentic_user@gmail.com")
        token_payload = {
            "token": {
                "access_token": "ya29.authentic_looking_token",
                "refresh_token": "1//authentic_looking_token",
                "id_token": jwt_real,
            }
        }
        self.assertFalse(is_synthetic_or_test_token(token_payload))

    def test_flat_synthetic_access_token_detected(self) -> None:
        self.assertTrue(is_synthetic_or_test_token({"access_token": "ya29.fake_token_alpha"}))

    def test_flat_synthetic_refresh_token_detected(self) -> None:
        self.assertTrue(is_synthetic_or_test_token({"refresh_token": "1//fake"}))

    def test_raw_signature_in_id_token_detected(self) -> None:
        self.assertTrue(is_synthetic_or_test_token({"id_token": "fake_non_jwt_token"}))
