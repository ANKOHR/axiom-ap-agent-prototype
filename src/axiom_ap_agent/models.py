"""Small, serialisable domain objects used by the AP-agent workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


class Decision(str, Enum):
    APPROVE_FOR_SUBMISSION = "APPROVE_FOR_SUBMISSION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIPPED = "SKIPPED"


def to_jsonable(value: Any) -> Any:
    """Convert domain values to JSON-safe primitives without losing amounts."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


@dataclass
class InvoiceRequest:
    supplier_name: str | None = None
    supplier_id: str | None = None
    invoice_number: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    due_date: str | None = None
    bank_destination: dict[str, str] | None = None
    description: str | None = None
    requestor: str | None = None
    amount_minor: int | None = None
    raw_amount: Any = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class PayoutRequest:
    request_id: str
    supplier_name: str | None
    supplier_id: str | None
    invoice_number: str | None
    amount: Decimal | None
    currency: str | None
    due_date: str | None
    bank_destination: dict[str, str] | None
    description: str | None
    requestor: str | None
    source: str = "ap_agent_prototype"

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class ValidationCheck:
    name: str
    status: CheckStatus
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    deterministic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class ValidationResult:
    checks: list[ValidationCheck] = field(default_factory=list)
    hard_block_reasons: list[str] = field(default_factory=list)
    human_review_reasons: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [*self.hard_block_reasons, *self.human_review_reasons]

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return [
            {
                "check": check.name,
                "status": check.status.value,
                "evidence": check.evidence,
            }
            for check in self.checks
        ]

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "checks": self.checks,
                "hard_block_reasons": self.hard_block_reasons,
                "human_review_reasons": self.human_review_reasons,
            }
        )


@dataclass
class AgentDecision:
    decision: Decision
    reasons: list[str]
    checks_performed: list[str]
    evidence: list[dict[str, Any]]
    confidence_status: str = "DETERMINISTIC_RULE_EVALUATION"

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class PermissionResult:
    allowed: bool
    action: str
    request_id: str
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    permission_token: str | None = None
    authority: str = "mock_axiom_permissions"

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class SubmissionResult:
    accepted: bool
    outcome: str
    reason: str
    external_reference: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)
