"""The five exact scenario payloads supplied for the Axiom staging trial."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .axiom_staging import PaymentCreateParams


@dataclass(frozen=True)
class StagingScenario:
    label: str
    scenario_key: str
    params: PaymentCreateParams
    expected: dict[str, Any]
    reuse_scenario_key: str | None = None
    attempt_number: int = 1


def staging_scenarios() -> tuple[StagingScenario, ...]:
    """Return the ordered live-test cases without credentials or runtime state."""

    allowed_params = PaymentCreateParams(
        amount_minor=2500,
        currency="GBP",
        merchant_id="merchant.acme-supplies.test",
        merchant_ref="inv-allowed-001",
    )
    return (
        StagingScenario(
            label="allowed payment",
            scenario_key="allowed",
            params=allowed_params,
            expected={
                "http_status": 200,
                "status": "accepted",
                "provider_dispatch": "test-mode",
            },
        ),
        StagingScenario(
            label="blocked merchant",
            scenario_key="blocked_merchant",
            params=PaymentCreateParams(
                amount_minor=2500,
                currency="GBP",
                merchant_id="merchant.blocked-supplier.test",
                merchant_ref="inv-blocked-merchant-001",
            ),
            expected={
                "http_status": 403,
                "error": "payment_policy_violation",
                "reason_contains": "merchant not allowed",
                "provider_dispatch": "none",
            },
        ),
        StagingScenario(
            label="blocked amount",
            scenario_key="blocked_amount",
            params=PaymentCreateParams(
                amount_minor=7500,
                currency="GBP",
                merchant_id="merchant.acme-supplies.test",
                merchant_ref="inv-blocked-amount-001",
            ),
            expected={
                "http_status": 403,
                "error": "payment_policy_violation",
                "reason_contains": "limit exceeded",
                "provider_dispatch": "none",
            },
        ),
        StagingScenario(
            label="idempotent replay",
            scenario_key="replay",
            params=allowed_params,
            reuse_scenario_key="allowed",
            expected={
                "http_status": 200,
                "replay": True,
                "duplicate_provider_dispatch": False,
            },
        ),
        StagingScenario(
            label="idempotency mismatch",
            scenario_key="mismatch",
            params=PaymentCreateParams(
                amount_minor=7500,
                currency="GBP",
                merchant_id="merchant.acme-supplies.test",
                merchant_ref="inv-allowed-001",
            ),
            reuse_scenario_key="allowed",
            expected={
                "error": "idempotency_mismatch",
            },
        ),
    )


__all__ = ["StagingScenario", "staging_scenarios"]
