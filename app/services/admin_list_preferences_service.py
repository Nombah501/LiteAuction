from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminListPreference
from app.web.auth import AdminAuthContext

ALLOWED_DENSITY_VALUES = frozenset({"compact", "standard", "comfortable"})
DEFAULT_DENSITY = "standard"


def _normalize_subject_key(*, auth: AdminAuthContext, admin_token: str | None = None) -> str:
    if auth.tg_user_id is not None:
        return f"tg:{auth.tg_user_id}"

    if auth.via == "token":
        token = (admin_token or "").strip()
        if not token:
            raise ValueError("Token-auth context requires admin token")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"tok:{digest}"

    raise ValueError("Cannot derive admin subject key from auth context")


def _normalize_queue_key(queue_key: str) -> str:
    normalized = queue_key.strip().lower()
    if not normalized:
        raise ValueError("Queue key is required")
    return normalized


def _normalize_density(density: str) -> str:
    normalized = density.strip().lower()
    if normalized not in ALLOWED_DENSITY_VALUES:
        raise ValueError("Invalid density value")
    return normalized


def _normalize_allowed_columns(allowed_columns: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for column in allowed_columns:
        key = column.strip()
        if not key:
            raise ValueError("Allowed columns cannot include blank values")
        if key in seen:
            raise ValueError("Allowed columns cannot include duplicates")
        seen.add(key)
        normalized.append(key)
    if not normalized:
        raise ValueError("Allowed columns cannot be empty")
    return tuple(normalized)


def _normalize_column_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError(f"{field_name} must contain only strings")
        key = raw.strip()
        if not key:
            raise ValueError(f"{field_name} cannot contain blank values")
        if key in seen:
            raise ValueError(f"{field_name} cannot contain duplicates")
        seen.add(key)
        normalized.append(key)
    return normalized


def _default_columns_payload(allowed_columns: Sequence[str]) -> dict[str, list[str]]:
    normalized = list(_normalize_allowed_columns(allowed_columns))
    return {
        "visible": list(normalized),
        "order": list(normalized),
        "pinned": [],
    }


def _normalize_columns_payload(
    columns_payload: Mapping[str, Any],
    *,
    allowed_columns: Sequence[str],
) -> dict[str, list[str]]:
    if not isinstance(columns_payload, Mapping):
        raise ValueError("Columns payload must be an object")

    required_keys = {"visible", "order", "pinned"}
    received_keys = set(columns_payload)
    if required_keys != received_keys:
        raise ValueError("Columns payload must include visible, order, and pinned")

    normalized_allowed = _normalize_allowed_columns(allowed_columns)
    allowed_set = set(normalized_allowed)

    visible = _normalize_column_list(columns_payload.get("visible"), field_name="visible")
    order = _normalize_column_list(columns_payload.get("order"), field_name="order")
    pinned = _normalize_column_list(columns_payload.get("pinned"), field_name="pinned")

    for field_name, values in (("visible", visible), ("order", order), ("pinned", pinned)):
        unknown = [item for item in values if item not in allowed_set]
        if unknown:
            raise ValueError(f"Unknown columns in {field_name}")

    order_set = set(order)
    if order_set != allowed_set:
        raise ValueError("order must include each allowed column exactly once")
    if not set(visible).issubset(order_set):
        raise ValueError("visible columns must be present in order")
    if not set(pinned).issubset(set(visible)):
        raise ValueError("pinned columns must be visible")

    return {
        "visible": visible,
        "order": order,
        "pinned": pinned,
    }


async def load_admin_list_preference(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_key: str,
    allowed_columns: Sequence[str],
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_queue = _normalize_queue_key(queue_key)
    defaults = _default_columns_payload(allowed_columns)

    row = await session.scalar(
        select(AdminListPreference).where(
            AdminListPreference.subject_key == subject_key,
            AdminListPreference.queue_key == normalized_queue,
        )
    )
    if row is None:
        return {
            "density": DEFAULT_DENSITY,
            "columns": defaults,
        }

    density = _normalize_density(row.density)
    columns = _normalize_columns_payload(row.columns_json, allowed_columns=allowed_columns)
    return {
        "density": density,
        "columns": columns,
    }


async def save_admin_list_preference(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_key: str,
    density: str,
    columns_payload: Mapping[str, Any],
    allowed_columns: Sequence[str],
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_queue = _normalize_queue_key(queue_key)
    normalized_density = _normalize_density(density)
    normalized_columns = _normalize_columns_payload(columns_payload, allowed_columns=allowed_columns)

    statement = insert(AdminListPreference).values(
        subject_key=subject_key,
        queue_key=normalized_queue,
        density=normalized_density,
        columns_json=normalized_columns,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_admin_list_preferences_subject_queue",
        set_={
            "density": normalized_density,
            "columns_json": normalized_columns,
            "updated_at": func.timezone("utc", func.now()),
        },
    )
    await session.execute(statement)
    await session.flush()

    return {
        "density": normalized_density,
        "columns": normalized_columns,
    }
