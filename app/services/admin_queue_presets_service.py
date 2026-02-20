from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminQueuePreset, AdminQueuePresetDefault, AdminQueuePresetSelection
from app.services.admin_list_preferences_service import (
    DEFAULT_DENSITY,
    _normalize_columns_payload,
    _normalize_density,
    _normalize_subject_key,
)
from app.web.auth import AdminAuthContext

QUEUE_CONTEXT_TO_QUEUE_KEY = {
    "moderation": "complaints",
    "appeals": "appeals",
    "risk": "signals",
    "feedback": "trade_feedback",
}
QUEUE_KEY_TO_QUEUE_CONTEXT = {value: key for key, value in QUEUE_CONTEXT_TO_QUEUE_KEY.items()}
ALLOWED_QUEUE_CONTEXTS = frozenset(QUEUE_CONTEXT_TO_QUEUE_KEY)
_ADMIN_ROLES = frozenset({"owner", "admin"})


def _is_missing_preset_tables_error(exc: ProgrammingError) -> bool:
    message = str(exc).lower()
    return "admin_queue_preset" in message and (
        "does not exist" in message or "undefinedtable" in message or "no such table" in message
    )


def _default_resolved_state(*, allowed_columns: Sequence[str]) -> dict[str, Any]:
    return {
        "source": "none",
        "state": {
            "density": DEFAULT_DENSITY,
            "columns": {
                "visible": list(allowed_columns),
                "order": list(allowed_columns),
                "pinned": [],
            },
            "filters": {},
            "sort": {},
        },
        "active_preset": None,
        "presets": [],
        "notice": None,
    }


def _normalize_queue_context(*, queue_context: str | None = None, queue_key: str | None = None) -> str:
    if queue_context is not None:
        value = queue_context.strip().lower()
    else:
        value = QUEUE_KEY_TO_QUEUE_CONTEXT.get((queue_key or "").strip().lower(), "")
    if value not in ALLOWED_QUEUE_CONTEXTS:
        raise ValueError("Unknown queue context")
    return value


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Preset name is required")
    value = name.strip()
    if len(value) < 1 or len(value) > 40:
        raise ValueError("Preset name must be 1-40 characters")
    return value


def _normalize_string_map(value: Any, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        normalized[key] = str(raw_value).strip()
    return normalized


def _normalize_state_payload(
    *,
    density: str,
    columns_payload: Mapping[str, Any],
    allowed_columns: Sequence[str],
    filters_payload: Mapping[str, Any] | None = None,
    sort_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "density": _normalize_density(density),
        "columns": _normalize_columns_payload(columns_payload, allowed_columns=allowed_columns),
        "filters": _normalize_string_map(filters_payload, field="filters"),
        "sort": _normalize_string_map(sort_payload, field="sort"),
    }


def _tolerant_columns(raw: Any, *, allowed_columns: Sequence[str]) -> tuple[dict[str, list[str]], list[str]]:
    allowed = [item.strip() for item in allowed_columns if item.strip()]
    allowed_set = set(allowed)
    notices: list[str] = []
    if not isinstance(raw, Mapping):
        notices.append("Preset columns were reset to defaults")
        return {"visible": list(allowed), "order": list(allowed), "pinned": []}, notices

    def parse_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        parsed: list[str] = []
        for item in value:
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            parsed.append(key)
        return parsed

    raw_order = parse_list(raw.get("order"))
    order = [item for item in raw_order if item in allowed_set]
    dropped_from_order = len(order) != len(raw_order)
    for key in allowed:
        if key not in order:
            order.append(key)

    raw_visible = parse_list(raw.get("visible"))
    visible = [item for item in order if item in raw_visible]
    dropped_from_visible = len(visible) != len(raw_visible)
    if not visible:
        visible = list(order)

    raw_pinned = parse_list(raw.get("pinned"))
    pinned = [item for item in order if item in raw_pinned and item in visible]
    dropped_from_pinned = len(pinned) != len(raw_pinned)

    if dropped_from_order or dropped_from_visible or dropped_from_pinned:
        notices.append("Some stale preset columns were skipped")

    return {"visible": visible, "order": order, "pinned": pinned}, notices


def _tolerant_snapshot(row: AdminQueuePreset, *, allowed_columns: Sequence[str]) -> tuple[dict[str, Any], str | None]:
    density_notice = None
    try:
        density = _normalize_density(row.density)
    except ValueError:
        density = DEFAULT_DENSITY
        density_notice = "Preset density was reset to default"

    columns, notices = _tolerant_columns(row.columns_json, allowed_columns=allowed_columns)
    filters = _normalize_string_map(row.filters_json, field="filters")
    sort = _normalize_string_map(row.sort_json, field="sort")

    all_notices = list(notices)
    if density_notice:
        all_notices.append(density_notice)
    notice = "; ".join(all_notices) if all_notices else None

    return {
        "density": density,
        "columns": columns,
        "filters": filters,
        "sort": sort,
    }, notice


def _is_admin(auth: AdminAuthContext) -> bool:
    return auth.role in _ADMIN_ROLES


def _can_mutate(owner_subject_key: str, *, auth: AdminAuthContext, subject_key: str) -> bool:
    return owner_subject_key == subject_key or _is_admin(auth)


async def list_presets(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    admin_token: str | None = None,
) -> list[dict[str, Any]]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)

    try:
        result = await session.execute(
            select(AdminQueuePreset)
            .where(
                AdminQueuePreset.queue_context == normalized_context,
                AdminQueuePreset.owner_subject_key == subject_key,
            )
            .order_by(AdminQueuePreset.updated_at.desc(), AdminQueuePreset.id.desc())
        )
    except ProgrammingError as exc:
        if _is_missing_preset_tables_error(exc):
            await session.rollback()
            return []
        raise

    if hasattr(result, "scalars"):
        rows = result.scalars().all()
    else:
        raw_rows = result.all() if hasattr(result, "all") else []
        rows = []
        for item in raw_rows:
            if isinstance(item, AdminQueuePreset):
                rows.append(item)
            elif isinstance(item, tuple) and item and isinstance(item[0], AdminQueuePreset):
                rows.append(item[0])

    return [
        {
            "id": row.id,
            "name": row.name,
            "queue_context": row.queue_context,
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        for row in rows
    ]


async def save_preset(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    name: str,
    density: str,
    columns_payload: Mapping[str, Any],
    allowed_columns: Sequence[str],
    filters_payload: Mapping[str, Any] | None = None,
    sort_payload: Mapping[str, Any] | None = None,
    admin_token: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)
    normalized_name = _normalize_name(name)
    payload = _normalize_state_payload(
        density=density,
        columns_payload=columns_payload,
        allowed_columns=allowed_columns,
        filters_payload=filters_payload,
        sort_payload=sort_payload,
    )

    duplicate = await session.scalar(
        select(AdminQueuePreset).where(
            AdminQueuePreset.owner_subject_key == subject_key,
            AdminQueuePreset.queue_context == normalized_context,
            AdminQueuePreset.name == normalized_name,
        )
    )
    if duplicate is not None and not overwrite:
        return {
            "ok": False,
            "conflict": True,
            "preset": {"id": duplicate.id, "name": duplicate.name},
        }

    if duplicate is not None:
        duplicate.density = payload["density"]
        duplicate.columns_json = payload["columns"]
        duplicate.filters_json = payload["filters"]
        duplicate.sort_json = payload["sort"]
        duplicate.updated_at = datetime.now(UTC)
        await session.flush()
        preset_id = duplicate.id
    else:
        statement = insert(AdminQueuePreset).values(
            owner_subject_key=subject_key,
            queue_context=normalized_context,
            name=normalized_name,
            density=payload["density"],
            columns_json=payload["columns"],
            filters_json=payload["filters"],
            sort_json=payload["sort"],
        )
        result = await session.execute(statement.returning(AdminQueuePreset.id))
        preset_id = int(result.scalar_one())

    await select_preset(
        session,
        auth=auth,
        queue_context=normalized_context,
        preset_id=preset_id,
        allowed_columns=allowed_columns,
        admin_token=admin_token,
    )

    return {
        "ok": True,
        "conflict": False,
        "preset": {"id": preset_id, "name": normalized_name},
    }


async def update_preset(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    preset_id: int,
    density: str,
    columns_payload: Mapping[str, Any],
    allowed_columns: Sequence[str],
    filters_payload: Mapping[str, Any] | None = None,
    sort_payload: Mapping[str, Any] | None = None,
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)
    payload = _normalize_state_payload(
        density=density,
        columns_payload=columns_payload,
        allowed_columns=allowed_columns,
        filters_payload=filters_payload,
        sort_payload=sort_payload,
    )

    row = await session.scalar(
        select(AdminQueuePreset).where(
            AdminQueuePreset.id == int(preset_id),
            AdminQueuePreset.queue_context == normalized_context,
        )
    )
    if row is None:
        raise ValueError("Preset not found")
    if not _can_mutate(row.owner_subject_key, auth=auth, subject_key=subject_key):
        raise PermissionError("Forbidden")

    row.density = payload["density"]
    row.columns_json = payload["columns"]
    row.filters_json = payload["filters"]
    row.sort_json = payload["sort"]
    row.updated_at = datetime.now(UTC)
    await session.flush()

    return {"ok": True, "preset": {"id": row.id, "name": row.name}}


async def select_preset(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    preset_id: int | None,
    allowed_columns: Sequence[str],
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)

    chosen: AdminQueuePreset | None = None
    if preset_id is not None:
        chosen = await session.scalar(
            select(AdminQueuePreset).where(
                AdminQueuePreset.id == int(preset_id),
                AdminQueuePreset.queue_context == normalized_context,
            )
        )
        if chosen is None:
            raise ValueError("Preset not found")

    statement = insert(AdminQueuePresetSelection).values(
        subject_key=subject_key,
        queue_context=normalized_context,
        preset_id=chosen.id if chosen is not None else None,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_admin_queue_preset_selections_subject_context",
        set_={
            "preset_id": chosen.id if chosen is not None else None,
            "updated_at": func.timezone("utc", func.now()),
        },
    )
    await session.execute(statement)
    await session.flush()

    if chosen is None:
        return {
            "ok": True,
            "preset": None,
            "state": None,
            "notice": None,
        }

    state, notice = _tolerant_snapshot(chosen, allowed_columns=allowed_columns)
    return {
        "ok": True,
        "preset": {"id": chosen.id, "name": chosen.name},
        "state": state,
        "notice": notice,
    }


async def delete_preset(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    preset_id: int,
    allowed_columns: Sequence[str],
    keep_current: bool,
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)

    row = await session.scalar(
        select(AdminQueuePreset).where(
            AdminQueuePreset.id == int(preset_id),
            AdminQueuePreset.queue_context == normalized_context,
        )
    )
    if row is None:
        raise ValueError("Preset not found")
    if not _can_mutate(row.owner_subject_key, auth=auth, subject_key=subject_key):
        raise PermissionError("Forbidden")

    selection = await session.scalar(
        select(AdminQueuePresetSelection).where(
            AdminQueuePresetSelection.subject_key == subject_key,
            AdminQueuePresetSelection.queue_context == normalized_context,
        )
    )
    was_active = bool(selection and selection.preset_id == row.id)

    default_row = await session.scalar(
        select(AdminQueuePresetDefault).where(AdminQueuePresetDefault.queue_context == normalized_context)
    )
    fallback_preset_id = default_row.preset_id if default_row is not None else None

    await session.delete(row)
    await session.flush()

    fallback_state = None
    if was_active and not keep_current:
        fallback = None
        if fallback_preset_id is not None:
            fallback = await session.scalar(select(AdminQueuePreset).where(AdminQueuePreset.id == fallback_preset_id))
        if fallback is None:
            if selection is not None:
                selection.preset_id = None
                selection.updated_at = datetime.now(UTC)
        else:
            if selection is None:
                statement = insert(AdminQueuePresetSelection).values(
                    subject_key=subject_key,
                    queue_context=normalized_context,
                    preset_id=fallback.id,
                )
                statement = statement.on_conflict_do_update(
                    constraint="uq_admin_queue_preset_selections_subject_context",
                    set_={
                        "preset_id": fallback.id,
                        "updated_at": func.timezone("utc", func.now()),
                    },
                )
                await session.execute(statement)
            else:
                selection.preset_id = fallback.id
                selection.updated_at = datetime.now(UTC)
            fallback_state, _ = _tolerant_snapshot(fallback, allowed_columns=allowed_columns)

    return {
        "ok": True,
        "was_active": was_active,
        "fallback_preset_id": fallback_preset_id,
        "fallback_state": fallback_state,
    }


async def set_admin_default(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    preset_id: int | None,
) -> dict[str, Any]:
    if not _is_admin(auth):
        raise PermissionError("Forbidden")
    normalized_context = _normalize_queue_context(queue_context=queue_context)

    if preset_id is not None:
        row = await session.scalar(
            select(AdminQueuePreset).where(
                AdminQueuePreset.id == int(preset_id),
                AdminQueuePreset.queue_context == normalized_context,
            )
        )
        if row is None:
            raise ValueError("Preset not found")

    statement = insert(AdminQueuePresetDefault).values(
        queue_context=normalized_context,
        preset_id=preset_id,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_admin_queue_preset_defaults_queue_context",
        set_={
            "preset_id": preset_id,
            "updated_at": func.timezone("utc", func.now()),
        },
    )
    await session.execute(statement)
    await session.flush()

    return {"ok": True, "queue_context": normalized_context, "preset_id": preset_id}


async def resolve_queue_preset_state(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    allowed_columns: Sequence[str],
    admin_token: str | None = None,
) -> dict[str, Any]:
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)
    normalized_context = _normalize_queue_context(queue_context=queue_context)

    try:
        selection = await session.scalar(
            select(AdminQueuePresetSelection).where(
                AdminQueuePresetSelection.subject_key == subject_key,
                AdminQueuePresetSelection.queue_context == normalized_context,
            )
        )
        active_row: AdminQueuePreset | None = None
        source = "none"
        if selection is not None and selection.preset_id is not None:
            active_row = await session.scalar(
                select(AdminQueuePreset).where(
                    AdminQueuePreset.id == selection.preset_id,
                    AdminQueuePreset.queue_context == normalized_context,
                )
            )
            if active_row is not None:
                source = "last_selected"

        if active_row is None:
            default_row = await session.scalar(
                select(AdminQueuePresetDefault).where(AdminQueuePresetDefault.queue_context == normalized_context)
            )
            if default_row is not None and default_row.preset_id is not None:
                candidate = await session.scalar(
                    select(AdminQueuePreset).where(
                        AdminQueuePreset.id == default_row.preset_id,
                        AdminQueuePreset.queue_context == normalized_context,
                    )
                )
                if candidate is not None:
                    active_row = candidate
                    source = "first_entry_default"
                    statement = insert(AdminQueuePresetSelection).values(
                        subject_key=subject_key,
                        queue_context=normalized_context,
                        preset_id=candidate.id,
                    )
                    statement = statement.on_conflict_do_update(
                        constraint="uq_admin_queue_preset_selections_subject_context",
                        set_={
                            "preset_id": candidate.id,
                            "updated_at": func.timezone("utc", func.now()),
                        },
                    )
                    await session.execute(statement)

        notice = None
        if active_row is None:
            state = _default_resolved_state(allowed_columns=allowed_columns)["state"]
        else:
            state, notice = _tolerant_snapshot(active_row, allowed_columns=allowed_columns)

        presets = await list_presets(
            session,
            auth=auth,
            queue_context=normalized_context,
            admin_token=admin_token,
        )

        return {
            "source": source,
            "state": state,
            "active_preset": None
            if active_row is None
            else {
                "id": active_row.id,
                "name": active_row.name,
                "owner_subject_key": active_row.owner_subject_key,
            },
            "presets": presets,
            "notice": notice,
        }
    except ProgrammingError as exc:
        if _is_missing_preset_tables_error(exc):
            await session.rollback()
            return _default_resolved_state(allowed_columns=allowed_columns)
        raise
