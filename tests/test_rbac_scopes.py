from __future__ import annotations

from app.services.rbac_service import (
    ALL_MANAGE_SCOPES,
    OPERATOR_SCOPES,
    VIEWER_SCOPES,
    resolve_allowlist_role,
)


def test_allowlist_role_scope_matrix(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "101,202,303")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "202")

    token_role, token_scopes = resolve_allowlist_role(None, via_token=True)
    assert token_role == "owner"
    assert token_scopes == ALL_MANAGE_SCOPES

    owner_role, owner_scopes = resolve_allowlist_role(101, via_token=False)
    assert owner_role == "owner"
    assert owner_scopes == ALL_MANAGE_SCOPES

    operator_role, operator_scopes = resolve_allowlist_role(202, via_token=False)
    assert operator_role == "operator"
    assert operator_scopes == OPERATOR_SCOPES

    viewer_role, viewer_scopes = resolve_allowlist_role(303, via_token=False)
    assert viewer_role == "viewer"
    assert viewer_scopes == VIEWER_SCOPES

    outside_role, outside_scopes = resolve_allowlist_role(999, via_token=False)
    assert outside_role == "viewer"
    assert outside_scopes == VIEWER_SCOPES
