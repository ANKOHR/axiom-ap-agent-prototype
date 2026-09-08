"""Mock Axiom permissions adapter.

This module is deliberately separate from the agent. It is the only component
that can grant or reject the mocked submission action, and it never moves real
money.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .models import PermissionResult, PayoutRequest, SubmissionResult


class MockAxiomPermissions:
    """Small in-memory stand-in for a permissioned Axiom API."""

    def __init__(self, policies: dict[str, dict]):
        self.policies = policies
        self._issued_tokens: dict[str, str] = {}

    def check_permission(self, payout_request: PayoutRequest) -> PermissionResult:
        requestor = payout_request.requestor or ""
        policy = self.policies.get(requestor)
        checks: list[dict] = []
        if policy is None:
            checks.append({"name": "requestor_policy", "status": "FAIL", "detail": "No Axiom mock policy exists for the requestor."})
            return PermissionResult(False, "check_permission", payout_request.request_id, "Requestor is not recognised by the Axiom mock.", checks)

        can_submit = bool(policy.get("can_submit", False))
        checks.append({"name": "can_submit", "status": "PASS" if can_submit else "FAIL", "configured": can_submit})
        if not can_submit:
            return PermissionResult(False, "check_permission", payout_request.request_id, "Axiom mock policy denies submission for this requestor.", checks)

        allowed_currencies = [str(item).upper() for item in policy.get("currencies", [])]
        currency_allowed = (payout_request.currency or "").upper() in allowed_currencies
        checks.append({"name": "currency_scope", "status": "PASS" if currency_allowed else "FAIL", "currency": payout_request.currency, "allowed": allowed_currencies})
        if not currency_allowed:
            return PermissionResult(False, "check_permission", payout_request.request_id, "Axiom mock policy does not allow this currency.", checks)

        try:
            max_amount = Decimal(str(policy.get("max_amount")))
        except (InvalidOperation, TypeError):
            max_amount = Decimal("0")
        amount_allowed = payout_request.amount is not None and payout_request.amount <= max_amount
        checks.append({"name": "amount_limit", "status": "PASS" if amount_allowed else "FAIL", "amount": payout_request.amount, "max_amount": max_amount})
        if not amount_allowed:
            return PermissionResult(False, "check_permission", payout_request.request_id, "Axiom mock policy rejects the amount for this requestor.", checks)

        token = f"mock-permission-{uuid4().hex}"
        self._issued_tokens[token] = payout_request.request_id
        checks.append({"name": "permission_token", "status": "PASS", "issued": True})
        return PermissionResult(True, "check_permission", payout_request.request_id, "Axiom mock permission granted.", checks, token)

    def submit_payout_request(self, payout_request: PayoutRequest, permission: PermissionResult) -> SubmissionResult:
        """Accept only an approved, request-bound permission token.

        The returned reference is a mock record identifier, not a bank or
        payment-provider transaction identifier.
        """

        if not permission.allowed:
            return SubmissionResult(False, "PERMISSION_REJECTED", "Submission was refused because permission was not granted.")
        if permission.request_id != payout_request.request_id:
            return SubmissionResult(False, "PERMISSION_REQUEST_MISMATCH", "Permission was issued for a different request.")
        if not permission.permission_token or self._issued_tokens.pop(permission.permission_token, None) != payout_request.request_id:
            return SubmissionResult(False, "INVALID_PERMISSION_TOKEN", "Permission token was missing, reused, or not issued by this adapter.")

        reference = f"mock-axiom-payout-{uuid4().hex[:12]}"
        return SubmissionResult(
            True,
            "ACCEPTED_BY_AXIOM_MOCK",
            "Mock Axiom accepted the payout request for simulated downstream handling; no money moved.",
            reference,
            {"mock_only": True, "request_id": payout_request.request_id},
        )
