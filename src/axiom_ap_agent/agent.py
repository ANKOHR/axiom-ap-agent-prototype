"""Agent orchestration with an explicit reasoning/execution boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .audit import JsonlAuditLog
from .models import (
    AgentDecision,
    Decision,
    CheckStatus,
    InvoiceRequest,
    PayoutRequest,
    PermissionResult,
    SubmissionResult,
    to_jsonable,
)
from .parser import ParseResult, invoice_from_parse, parse_request
from .validator import decision_from_validation, validate_request


class WorkflowResult:
    def __init__(
        self,
        request_id: str,
        parsed: ParseResult,
        invoice: InvoiceRequest | None,
        payout_request: PayoutRequest | None,
        agent_decision: AgentDecision,
        permission: PermissionResult | None,
        submission: SubmissionResult | None,
        final_outcome: str,
        audit_record: dict[str, Any],
    ):
        self.request_id = request_id
        self.parsed = parsed
        self.invoice = invoice
        self.payout_request = payout_request
        self.agent_decision = agent_decision
        self.permission = permission
        self.submission = submission
        self.final_outcome = final_outcome
        self.audit_record = audit_record


class APAgent:
    """Interpret, validate, and prepare payout requests; never execute payments."""

    def __init__(
        self,
        *,
        suppliers: list[dict[str, Any]],
        previous_invoices: list[dict[str, Any]],
        rules: dict[str, Any],
        permissions_adapter: Any,
        audit_log: JsonlAuditLog,
        requestor_permissions: dict[str, Any] | None = None,
        enforce_local_requestor_permissions: bool = False,
    ):
        self.suppliers = suppliers
        self.previous_invoices = previous_invoices
        self.rules = rules
        self.permissions_adapter = permissions_adapter
        self.audit_log = audit_log
        self.requestor_permissions = requestor_permissions
        self.enforce_local_requestor_permissions = enforce_local_requestor_permissions

    def handle(self, raw_input: Any) -> WorkflowResult:
        request_id = f"req-{uuid4().hex}"
        timestamp = datetime.now(timezone.utc).isoformat()
        parsed = parse_request(raw_input)
        invoice = invoice_from_parse(parsed) if parsed.normalized else None

        if invoice is None:
            from .models import ValidationCheck, ValidationResult

            validation = ValidationResult(
                checks=[ValidationCheck("input_parsing", CheckStatus.FAIL, "No normalisable request object was produced.", {"errors": parsed.errors})],
                hard_block_reasons=["No normalisable request object was produced."],
            )
        else:
            validation = validate_request(
                invoice,
                self.suppliers,
                self.previous_invoices,
                self.rules,
                parse_errors=parsed.errors,
                requestor_permissions=self.requestor_permissions,
                enforce_requestor_permissions=self.enforce_local_requestor_permissions,
            )

        decision = decision_from_validation(validation)
        agent_decision = AgentDecision(
            decision=decision,
            reasons=validation.reasons or ["All configured deterministic checks passed."],
            checks_performed=[check.name for check in validation.checks],
            evidence=validation.evidence,
        )

        payout_request: PayoutRequest | None = None
        if invoice is not None:
            payout_request = PayoutRequest(
                request_id=request_id,
                supplier_name=invoice.supplier_name,
                supplier_id=invoice.supplier_id,
                invoice_number=invoice.invoice_number,
                amount=invoice.amount,
                currency=invoice.currency,
                due_date=invoice.due_date,
                bank_destination=invoice.bank_destination,
                description=invoice.description,
                requestor=invoice.requestor,
            )

        permission: PermissionResult | None = None
        submission: SubmissionResult | None = None
        if decision == Decision.APPROVE_FOR_SUBMISSION and payout_request is not None:
            permission = self.permissions_adapter.check_permission(payout_request)
            if permission.allowed:
                submission = self.permissions_adapter.submit_payout_request(payout_request, permission)
                final_outcome = submission.outcome
            else:
                final_outcome = "REJECTED_BY_AXIOM_PERMISSION"
        elif decision == Decision.HUMAN_REVIEW_REQUIRED:
            final_outcome = "HELD_FOR_HUMAN_REVIEW"
        else:
            final_outcome = "BLOCKED_BY_AGENT_VALIDATION"

        audit_record = {
            "timestamp": timestamp,
            "request_id": request_id,
            "input_received": parsed.raw_input,
            "input_format": parsed.source_format,
            "parse_errors": parsed.errors,
            "extracted_normalised_fields": invoice.to_dict() if invoice else parsed.normalized,
            "payout_request_prepared": payout_request.to_dict() if payout_request else None,
            "validation_checks": [check.to_dict() for check in validation.checks],
            "decision": agent_decision.to_dict(),
            "reasons": agent_decision.reasons,
            "evidence_used": agent_decision.evidence,
            "permission_check": permission.to_dict() if permission else {"status": "NOT_RUN", "reason": "Agent did not recommend submission."},
            "submission_result": submission.to_dict() if submission else None,
            "final_outcome": final_outcome,
            "execution_boundary": {
                "agent_can": ["interpret", "validate", "reason", "prepare_payout_request"],
                "agent_cannot": ["grant_permissions", "execute_payment", "move_real_money"],
                "permission_authority": "mock_axiom_permissions",
            },
        }
        stored_record = self.audit_log.append(audit_record)
        return WorkflowResult(request_id, parsed, invoice, payout_request, agent_decision, permission, submission, final_outcome, stored_record)
