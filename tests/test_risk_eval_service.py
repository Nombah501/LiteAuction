from __future__ import annotations

from app.services.risk_eval_service import evaluate_user_risk_snapshot


def test_evaluate_user_risk_snapshot_low() -> None:
    snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )

    assert snapshot.level == "LOW"
    assert snapshot.score == 0
    assert snapshot.reasons == ()


def test_evaluate_user_risk_snapshot_medium() -> None:
    snapshot = evaluate_user_risk_snapshot(
        complaints_against=1,
        open_fraud_signals=1,
        has_active_blacklist=False,
        removed_bids=1,
    )

    assert snapshot.level == "MEDIUM"
    assert snapshot.score == 63
    assert snapshot.reasons == ("OPEN_FRAUD_SIGNAL", "COMPLAINTS_AGAINST", "REMOVED_BIDS")


def test_evaluate_user_risk_snapshot_high_and_capped() -> None:
    snapshot = evaluate_user_risk_snapshot(
        complaints_against=8,
        open_fraud_signals=3,
        has_active_blacklist=True,
        removed_bids=7,
    )

    assert snapshot.level == "HIGH"
    assert snapshot.score == 100
    assert snapshot.reasons == (
        "ACTIVE_BLACKLIST",
        "OPEN_FRAUD_SIGNAL",
        "COMPLAINTS_AGAINST_3PLUS",
        "REMOVED_BIDS_3PLUS",
    )


def test_evaluate_user_risk_snapshot_applies_verified_bonus_safely() -> None:
    snapshot = evaluate_user_risk_snapshot(
        complaints_against=1,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
        is_verified_user=True,
    )

    assert snapshot.level == "LOW"
    assert snapshot.score == 5
    assert snapshot.reasons == ("COMPLAINTS_AGAINST",)


def test_evaluate_user_risk_snapshot_does_not_discount_with_open_signal() -> None:
    snapshot = evaluate_user_risk_snapshot(
        complaints_against=1,
        open_fraud_signals=1,
        has_active_blacklist=False,
        removed_bids=0,
        is_verified_user=True,
    )

    assert snapshot.level == "MEDIUM"
    assert snapshot.score == 55
    assert snapshot.reasons == ("OPEN_FRAUD_SIGNAL", "COMPLAINTS_AGAINST")
