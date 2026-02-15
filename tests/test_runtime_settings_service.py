from __future__ import annotations

import pytest

from app.services.runtime_settings_service import parse_runtime_setting_value


def test_parse_runtime_bool_variants() -> None:
    assert parse_runtime_setting_value("message_drafts_enabled", "true") is True
    assert parse_runtime_setting_value("message_drafts_enabled", "0") is False


def test_parse_runtime_int_with_bounds() -> None:
    assert parse_runtime_setting_value("fraud_alert_threshold", "75") == 75

    with pytest.raises(ValueError, match="Value must be >= 0"):
        parse_runtime_setting_value("fraud_alert_threshold", "-1")


def test_parse_runtime_unknown_key_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown runtime setting key"):
        parse_runtime_setting_value("unknown_key", "1")
