from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import RuntimeSettingOverride
from app.db.session import SessionFactory


RuntimeSettingValue = bool | int


@dataclass(slots=True, frozen=True)
class RuntimeSettingSpec:
    key: str
    value_type: str
    description: str
    default_value: RuntimeSettingValue
    min_value: int | None = None
    max_value: int | None = None


@dataclass(slots=True, frozen=True)
class RuntimeSettingSnapshotItem:
    key: str
    value_type: str
    description: str
    default_value: RuntimeSettingValue
    effective_value: RuntimeSettingValue
    override_raw_value: str | None
    updated_by_user_id: int | None
    updated_at: datetime | None


RUNTIME_SETTING_SPECS: dict[str, RuntimeSettingSpec] = {
    "fraud_alert_threshold": RuntimeSettingSpec(
        key="fraud_alert_threshold",
        value_type="int",
        description="Minimum fraud score required to open a fraud signal.",
        default_value=int(settings.fraud_alert_threshold),
        min_value=0,
        max_value=1000,
    ),
    "publish_high_risk_requires_guarantor": RuntimeSettingSpec(
        key="publish_high_risk_requires_guarantor",
        value_type="bool",
        description="Require assigned guarantor for HIGH-risk sellers before publish.",
        default_value=bool(settings.publish_high_risk_requires_guarantor),
    ),
    "publish_guarantor_assignment_max_age_days": RuntimeSettingSpec(
        key="publish_guarantor_assignment_max_age_days",
        value_type="int",
        description="Maximum age in days for accepted guarantor assignment.",
        default_value=int(settings.publish_guarantor_assignment_max_age_days),
        min_value=0,
        max_value=3650,
    ),
    "message_drafts_enabled": RuntimeSettingSpec(
        key="message_drafts_enabled",
        value_type="bool",
        description="Enable Bot API message drafts for long-running interactions.",
        default_value=bool(settings.message_drafts_enabled),
    ),
}

_CACHE_TTL_SECONDS = 10.0
_runtime_cache_expires_at: float = 0.0
_runtime_cache_values: dict[str, RuntimeSettingValue] | None = None


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def list_runtime_setting_specs() -> tuple[RuntimeSettingSpec, ...]:
    return tuple(sorted(RUNTIME_SETTING_SPECS.values(), key=lambda item: item.key))


def get_runtime_setting_spec(key: str) -> RuntimeSettingSpec:
    normalized_key = _normalize_key(key)
    spec = RUNTIME_SETTING_SPECS.get(normalized_key)
    if spec is None:
        raise ValueError(f"Unknown runtime setting key: {normalized_key}")
    return spec


def _parse_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Expected boolean value: true/false (or 1/0)")


def _parse_int(spec: RuntimeSettingSpec, raw_value: str) -> int:
    normalized = raw_value.strip()
    if not normalized:
        raise ValueError("Expected integer value")
    try:
        parsed = int(normalized)
    except ValueError as exc:  # pragma: no cover - exercised by tests
        raise ValueError("Expected integer value") from exc

    if spec.min_value is not None and parsed < spec.min_value:
        raise ValueError(f"Value must be >= {spec.min_value}")
    if spec.max_value is not None and parsed > spec.max_value:
        raise ValueError(f"Value must be <= {spec.max_value}")
    return parsed


def parse_runtime_setting_value(key: str, raw_value: str) -> RuntimeSettingValue:
    spec = get_runtime_setting_spec(key)
    if spec.value_type == "bool":
        return _parse_bool(raw_value)
    if spec.value_type == "int":
        return _parse_int(spec, raw_value)
    raise ValueError(f"Unsupported runtime setting type: {spec.value_type}")


def serialize_runtime_setting_value(key: str, value: RuntimeSettingValue) -> str:
    spec = get_runtime_setting_spec(key)
    if spec.value_type == "bool":
        return "true" if bool(value) else "false"
    if spec.value_type == "int":
        return str(int(value))
    raise ValueError(f"Unsupported runtime setting type: {spec.value_type}")


def _coerce_stored_value(spec: RuntimeSettingSpec, raw_value: str) -> RuntimeSettingValue:
    try:
        return parse_runtime_setting_value(spec.key, raw_value)
    except ValueError:
        return spec.default_value


def _invalidate_runtime_cache() -> None:
    global _runtime_cache_expires_at, _runtime_cache_values
    _runtime_cache_expires_at = 0.0
    _runtime_cache_values = None


async def list_runtime_setting_overrides(session: AsyncSession) -> list[RuntimeSettingOverride]:
    rows = await session.execute(
        select(RuntimeSettingOverride).order_by(RuntimeSettingOverride.key.asc())
    )
    return list(rows.scalars().all())


async def build_runtime_settings_snapshot(session: AsyncSession) -> list[RuntimeSettingSnapshotItem]:
    override_rows = await list_runtime_setting_overrides(session)
    override_map = {row.key: row for row in override_rows}

    items: list[RuntimeSettingSnapshotItem] = []
    for spec in list_runtime_setting_specs():
        row = override_map.get(spec.key)
        effective_value = spec.default_value
        override_raw_value: str | None = None
        updated_by_user_id: int | None = None
        updated_at: datetime | None = None
        if row is not None:
            override_raw_value = row.value
            updated_by_user_id = row.updated_by_user_id
            updated_at = row.updated_at
            effective_value = _coerce_stored_value(spec, row.value)

        items.append(
            RuntimeSettingSnapshotItem(
                key=spec.key,
                value_type=spec.value_type,
                description=spec.description,
                default_value=spec.default_value,
                effective_value=effective_value,
                override_raw_value=override_raw_value,
                updated_by_user_id=updated_by_user_id,
                updated_at=updated_at,
            )
        )
    return items


async def resolve_runtime_setting_value(session: AsyncSession, key: str) -> RuntimeSettingValue:
    spec = get_runtime_setting_spec(key)
    row = await session.scalar(
        select(RuntimeSettingOverride).where(RuntimeSettingOverride.key == spec.key)
    )
    if row is None:
        return spec.default_value
    return _coerce_stored_value(spec, row.value)


async def upsert_runtime_setting_override(
    session: AsyncSession,
    *,
    key: str,
    raw_value: str,
    updated_by_user_id: int,
) -> RuntimeSettingOverride:
    spec = get_runtime_setting_spec(key)
    parsed_value = parse_runtime_setting_value(spec.key, raw_value)
    serialized_value = serialize_runtime_setting_value(spec.key, parsed_value)

    existing = await session.scalar(
        select(RuntimeSettingOverride).where(RuntimeSettingOverride.key == spec.key)
    )
    now = datetime.now(UTC)
    if existing is None:
        existing = RuntimeSettingOverride(
            key=spec.key,
            value=serialized_value,
            updated_by_user_id=updated_by_user_id,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.value = serialized_value
        existing.updated_by_user_id = updated_by_user_id
        existing.updated_at = now

    await session.flush()
    _invalidate_runtime_cache()
    return existing


async def delete_runtime_setting_override(session: AsyncSession, *, key: str) -> bool:
    spec = get_runtime_setting_spec(key)
    existing = await session.scalar(
        select(RuntimeSettingOverride).where(RuntimeSettingOverride.key == spec.key)
    )
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    _invalidate_runtime_cache()
    return True


def _default_runtime_values() -> dict[str, RuntimeSettingValue]:
    return {spec.key: spec.default_value for spec in list_runtime_setting_specs()}


async def get_runtime_setting_value(key: str) -> RuntimeSettingValue:
    spec = get_runtime_setting_spec(key)

    global _runtime_cache_expires_at, _runtime_cache_values
    now = time.monotonic()
    if _runtime_cache_values is not None and _runtime_cache_expires_at > now:
        return _runtime_cache_values.get(spec.key, spec.default_value)

    cache_values = _default_runtime_values()
    try:
        async with SessionFactory() as session:
            rows = await list_runtime_setting_overrides(session)
            for row in rows:
                runtime_spec = RUNTIME_SETTING_SPECS.get(row.key)
                if runtime_spec is None:
                    continue
                cache_values[row.key] = _coerce_stored_value(runtime_spec, row.value)
    except Exception:
        return spec.default_value

    _runtime_cache_values = cache_values
    _runtime_cache_expires_at = now + _CACHE_TTL_SECONDS
    return cache_values.get(spec.key, spec.default_value)
