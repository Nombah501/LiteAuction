from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.admin_queue_presets_service import (
    QUEUE_CONTEXT_TO_QUEUE_KEY,
    _normalize_name,
    _normalize_queue_context,
    _tolerant_columns,
    resolve_queue_preset_state,
    update_preset,
)
from app.web.auth import AdminAuthContext


def _auth(*, role: str = "owner", tg_user_id: int | None = 101) -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram" if tg_user_id is not None else "token",
        role=role,
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=tg_user_id,
    )


@dataclass
class _PresetRow:
    id: int
    owner_subject_key: str
    queue_context: str
    name: str
    density: str
    columns_json: dict
    filters_json: dict
    sort_json: dict


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, scalars):
        self._scalars = list(scalars)
        self.executed = []
        self.flushed = False

    async def scalar(self, _statement):
        if not self._scalars:
            return None
        return self._scalars.pop(0)

    async def execute(self, statement):
        self.executed.append(statement)
        return _ScalarResult([])

    async def flush(self):
        self.flushed = True


def test_queue_context_mapping_is_complete() -> None:
    assert QUEUE_CONTEXT_TO_QUEUE_KEY == {
        "moderation": "complaints",
        "appeals": "appeals",
        "risk": "signals",
        "feedback": "trade_feedback",
    }


def test_name_validation_enforces_1_to_40_characters() -> None:
    assert _normalize_name("  Incident Focus  ") == "Incident Focus"
    with pytest.raises(ValueError, match="1-40"):
        _normalize_name("")
    with pytest.raises(ValueError, match="1-40"):
        _normalize_name("x" * 41)


def test_queue_context_can_be_derived_from_queue_key() -> None:
    assert _normalize_queue_context(queue_key="complaints") == "moderation"
    assert _normalize_queue_context(queue_context="risk") == "risk"
    with pytest.raises(ValueError, match="Unknown queue context"):
        _normalize_queue_context(queue_key="unknown")


def test_tolerant_columns_skips_stale_values_and_emits_notice() -> None:
    columns, notices = _tolerant_columns(
        {
            "visible": ["id", "unknown"],
            "order": ["id", "unknown", "status"],
            "pinned": ["unknown", "id"],
        },
        allowed_columns=("id", "status", "created"),
    )
    assert columns == {
        "visible": ["id"],
        "order": ["id", "status", "created"],
        "pinned": ["id"],
    }
    assert notices


@pytest.mark.asyncio
async def test_update_rejects_non_owner_non_admin_mutation() -> None:
    row = _PresetRow(
        id=12,
        owner_subject_key="tg:999",
        queue_context="moderation",
        name="A",
        density="standard",
        columns_json={"visible": ["id"], "order": ["id"], "pinned": []},
        filters_json={},
        sort_json={},
    )
    session = _Session([row])

    with pytest.raises(PermissionError, match="Forbidden"):
        await update_preset(
            session,
            auth=_auth(role="moderator", tg_user_id=101),
            queue_context="moderation",
            preset_id=12,
            density="standard",
            columns_payload={"visible": ["id"], "order": ["id"], "pinned": []},
            allowed_columns=("id",),
        )


@pytest.mark.asyncio
async def test_resolve_uses_first_entry_default_then_selection() -> None:
    default_preset = _PresetRow(
        id=7,
        owner_subject_key="tg:100",
        queue_context="moderation",
        name="Default",
        density="compact",
        columns_json={"visible": ["id"], "order": ["id", "status"], "pinned": ["id"]},
        filters_json={},
        sort_json={},
    )
    session = _Session([
        None,  # selection
        type("DefaultRef", (), {"preset_id": 7})(),  # default row
        default_preset,  # default preset
    ])

    result = await resolve_queue_preset_state(
        session,
        auth=_auth(role="owner", tg_user_id=101),
        queue_context="moderation",
        allowed_columns=("id", "status"),
    )

    assert result["source"] == "first_entry_default"
    assert result["active_preset"]["id"] == 7
    assert result["state"]["density"] == "compact"
    assert session.executed, "expected selection upsert execution"
