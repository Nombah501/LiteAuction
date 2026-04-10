from app.web.main import (
    _parse_complaint_sla_health_filter,
    _parse_complaint_aging_bucket_filter,
    _parse_signal_sla_health_filter,
    _parse_signal_aging_bucket_filter,
)


def test_parse_complaint_sla_health_filter_valid_values():
    for value in ("all", "healthy", "warning", "critical", "overdue", "no_sla"):
        assert _parse_complaint_sla_health_filter(value) == value


def test_parse_complaint_sla_health_filter_invalid():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _parse_complaint_sla_health_filter("invalid")


def test_parse_complaint_aging_bucket_filter_valid_values():
    for value in ("all", "fresh", "aging", "stale", "critical", "overdue", "unknown"):
        assert _parse_complaint_aging_bucket_filter(value) == value


def test_parse_complaint_aging_bucket_filter_invalid():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _parse_complaint_aging_bucket_filter("bad_value")


def test_parse_signal_sla_health_filter_valid_values():
    for value in ("all", "healthy", "warning", "critical", "overdue", "no_sla"):
        assert _parse_signal_sla_health_filter(value) == value


def test_parse_signal_aging_bucket_filter_valid_values():
    for value in ("all", "fresh", "aging", "stale", "critical", "overdue", "unknown"):
        assert _parse_signal_aging_bucket_filter(value) == value
