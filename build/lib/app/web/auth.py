from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from fastapi import Request

from app.config import settings
from app.services.rbac_service import VIEWER_SCOPES, resolve_allowlist_role


@dataclass(slots=True)
class AdminAuthContext:
    authorized: bool
    via: str
    role: str = "viewer"
    can_manage: bool = False
    scopes: frozenset[str] = frozenset()
    tg_user_id: int | None = None

    def can(self, scope: str) -> bool:
        return scope in self.scopes


def _resolve_role_and_permissions(tg_user_id: int | None, *, via_token: bool) -> tuple[str, frozenset[str]]:
    return resolve_allowlist_role(tg_user_id, via_token=via_token)


def _token_from_request(request: Request) -> str | None:
    token = request.query_params.get("token")
    if token:
        return token
    header = request.headers.get("x-admin-token", "")
    return header or None


def _session_secret() -> str:
    value = settings.admin_web_session_secret.strip()
    if value:
        return value
    return settings.bot_token


def _build_session_cookie_value(tg_user_id: int, expires_at: int) -> str:
    payload = f"{tg_user_id}:{expires_at}"
    signature = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{signature}"


def build_admin_session_cookie(tg_user_id: int) -> str:
    expires_at = int(datetime.now(UTC).timestamp()) + max(settings.admin_web_auth_max_age_seconds, 60)
    return _build_session_cookie_value(tg_user_id, expires_at)


def _validate_session_cookie(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    uid_raw, expires_raw, signature = parts
    if not uid_raw.isdigit() or not expires_raw.isdigit():
        return None

    expected = _build_session_cookie_value(int(uid_raw), int(expires_raw)).split(":")[-1]
    if not hmac.compare_digest(signature, expected):
        return None

    if int(expires_raw) < int(datetime.now(UTC).timestamp()):
        return None

    tg_user_id = int(uid_raw)
    if tg_user_id not in settings.parsed_admin_user_ids():
        return None
    return tg_user_id


def _check_admin_token(request: Request) -> bool:
    expected = settings.admin_panel_token.strip()
    if not expected:
        return False
    supplied = _token_from_request(request)
    if supplied is None:
        return False
    return hmac.compare_digest(supplied, expected)


def get_admin_auth_context(request: Request) -> AdminAuthContext:
    if _check_admin_token(request):
        role, scopes = _resolve_role_and_permissions(None, via_token=True)
        return AdminAuthContext(
            authorized=True,
            via="token",
            role=role,
            can_manage=bool(scopes),
            scopes=scopes,
            tg_user_id=None,
        )

    raw_cookie = request.cookies.get("la_admin_session", "")
    if raw_cookie:
        tg_user_id = _validate_session_cookie(raw_cookie)
        if tg_user_id is not None:
            role, scopes = _resolve_role_and_permissions(tg_user_id, via_token=False)
            return AdminAuthContext(
                authorized=True,
                via="telegram",
                role=role,
                can_manage=bool(scopes),
                scopes=scopes,
                tg_user_id=tg_user_id,
            )

    return AdminAuthContext(
        authorized=False,
        via="none",
        role="viewer",
        can_manage=False,
        scopes=VIEWER_SCOPES,
        tg_user_id=None,
    )


def validate_telegram_login(payload: Mapping[str, str]) -> tuple[bool, str]:
    provided_hash = payload.get("hash", "")
    if not provided_hash:
        return False, "missing hash"

    auth_date_raw = payload.get("auth_date", "")
    if not auth_date_raw.isdigit():
        return False, "invalid auth_date"

    auth_ts = int(auth_date_raw)
    now_ts = int(datetime.now(UTC).timestamp())
    if now_ts - auth_ts > max(settings.admin_web_auth_max_age_seconds, 60):
        return False, "auth expired"

    data_pairs = []
    for key in sorted(payload):
        if key == "hash":
            continue
        data_pairs.append(f"{key}={payload[key]}")
    data_check_string = "\n".join(data_pairs)

    secret_key = hashlib.sha256(settings.bot_token.encode("utf-8")).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, provided_hash):
        return False, "hash mismatch"

    user_id = payload.get("id", "")
    if not user_id.isdigit():
        return False, "invalid user id"
    if int(user_id) not in settings.parsed_admin_user_ids():
        return False, "user is not in admin allowlist"

    return True, "ok"
