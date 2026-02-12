from __future__ import annotations

from types import SimpleNamespace

from app.services.complaint_service import render_complaint_text
from app.services.fraud_service import render_fraud_signal_text


def test_render_complaint_text_includes_resolution_and_moderator() -> None:
    view = SimpleNamespace(
        complaint=SimpleNamespace(id=10, status="RESOLVED", reason="fraud", resolution_note="Снята топ-ставка"),
        auction=SimpleNamespace(id="auc-1"),
        reporter=SimpleNamespace(username="reporter", tg_user_id=1001),
        target_user=SimpleNamespace(username="suspect", tg_user_id=1002),
        target_bid=SimpleNamespace(id="bid-1", amount=123),
        resolver_user=SimpleNamespace(username="moderator", tg_user_id=1003),
    )

    text = render_complaint_text(view)

    assert "Жалоба #10" in text
    assert "Решение: Снята топ-ставка" in text
    assert "Модератор: @moderator" in text


def test_render_fraud_signal_text_includes_resolution_and_moderator() -> None:
    view = SimpleNamespace(
        signal=SimpleNamespace(
            id=55,
            status="CONFIRMED",
            score=88,
            reasons={"rules": [{"code": "RAPID_BIDDING", "detail": "x", "score": 20}]},
            resolution_note="Пользователь заблокирован",
        ),
        auction=SimpleNamespace(id="auc-2"),
        user=SimpleNamespace(username="suspect", tg_user_id=2002),
        bid=SimpleNamespace(id="bid-2", amount=321),
        resolver_user=SimpleNamespace(username="moderator", tg_user_id=2001),
    )

    text = render_fraud_signal_text(view)

    assert "Фрод-сигнал #55" in text
    assert "Решение: Пользователь заблокирован" in text
    assert "Модератор: @moderator" in text
