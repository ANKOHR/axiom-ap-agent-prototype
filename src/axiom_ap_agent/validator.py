"""Deterministic validation rules for invoice and payment requests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .models import CheckStatus, InvoiceRequest, ValidationCheck, ValidationResult


def _normalise_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _canonical_bank(bank: dict[str, str] | None) -> dict[str, str] | None:
    if not bank:
        return None
    canonical: dict[str, str] = {}
    for key, value in bank.items():
        item = str(value).strip().upper()
        if key in {"sort_code", "account_number", "routing_number"}:
            item = "".join(character for character in item if character.isalnum())
        canonical[key] = item
    return canonical


def _find_supplier(invoice: InvoiceRequest, suppliers: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    supplier_list = list(suppliers)
    if invoice.supplier_id:
        for supplier in supplier_list:
            if _normalise_text(supplier.get("supplier_id")) == _normalise_text(invoice.supplier_id):
                return supplier, "supplier_id"
        return None, "supplier_id"
    for supplier in supplier_list:
        if _normalise_text(supplier.get("supplier_name")) == _normalise_text(invoice.supplier_name):
            return supplier, "supplier_name"
    return None, "supplier_name"


def _add(
    checks: list[ValidationCheck],
    name: str,
    status: CheckStatus,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    checks.append(ValidationCheck(name, status, message, evidence or {}))


def validate_request(
    invoice: InvoiceRequest,
    suppliers: list[dict[str, Any]],
    previous_invoices: list[dict[str, Any]],
    rules: dict[str, Any],
    *,
    parse_errors: list[str] | None = None,
    requestor_permissions: dict[str, Any] | None = None,
    enforce_requestor_permissions: bool = False,
) -> ValidationResult:
    checks: list[ValidationCheck] = []
    hard_blocks: list[str] = []
    human_review: list[str] = []

    if parse_errors:
        message = " ".join(parse_errors)
        _add(checks, "input_parsing", CheckStatus.FAIL, message, {"errors": parse_errors})
        hard_blocks.append(f"Input parsing failed: {message}")
    else:
        _add(checks, "input_parsing", CheckStatus.PASS, "Input was parsed into the normalised request model.")

    required_fields = list(rules.get("required_fields", []))
    missing = [field for field in required_fields if getattr(invoice, field, None) in (None, "")]
    if missing:
        _add(
            checks,
            "required_fields",
            CheckStatus.FAIL,
            "Required fields are missing.",
            {"missing": missing, "required": required_fields},
        )
        human_review.append(f"Required field(s) missing: {', '.join(missing)}.")
    else:
        _add(checks, "required_fields", CheckStatus.PASS, "All required fields are present.", {"required": required_fields})

    if invoice.amount is None:
        _add(checks, "amount_format", CheckStatus.FAIL, "Amount is missing or malformed.", {"raw_amount": invoice.raw_amount})
        hard_blocks.append("Amount is missing or malformed.")
    else:
        if not invoice.amount.is_finite() or invoice.amount <= Decimal("0"):
            _add(
                checks,
                "amount_format",
                CheckStatus.FAIL,
                "Amount must be a finite positive number.",
                {"amount": invoice.amount},
            )
            hard_blocks.append("Amount must be a finite positive number.")
        elif abs(invoice.amount.as_tuple().exponent) > int(rules.get("max_decimal_places", 2)):
            _add(
                checks,
                "amount_format",
                CheckStatus.FAIL,
                "Amount has more decimal places than the configured currency precision.",
                {"amount": invoice.amount, "max_decimal_places": rules.get("max_decimal_places", 2)},
            )
            hard_blocks.append("Amount has unsupported decimal precision.")
        else:
            _add(checks, "amount_format", CheckStatus.PASS, "Amount is finite, positive, and correctly formatted.", {"amount": invoice.amount})

        if invoice.amount_minor is not None and invoice.amount.is_finite():
            expected_minor = int((invoice.amount * 100).to_integral_value())
            if invoice.amount_minor != expected_minor:
                _add(
                    checks,
                    "amount_consistency",
                    CheckStatus.FAIL,
                    "The major-unit and minor-unit amounts disagree.",
                    {"amount": invoice.amount, "amount_minor": invoice.amount_minor, "expected_minor": expected_minor},
                )
                hard_blocks.append("Amount fields are inconsistent.")
            else:
                _add(checks, "amount_consistency", CheckStatus.PASS, "Amount fields agree.", {"amount_minor": expected_minor})
        else:
            _add(checks, "amount_consistency", CheckStatus.SKIPPED, "No amount_minor field was supplied.")

    currency = (invoice.currency or "").upper()
    supported_currencies = [str(item).upper() for item in rules.get("supported_currencies", [])]
    if not currency or currency not in supported_currencies:
        _add(
            checks,
            "currency_validation",
            CheckStatus.FAIL,
            "Currency is missing or not supported by the configured policy.",
            {"currency": currency or None, "supported": supported_currencies},
        )
        hard_blocks.append("Currency is missing or unsupported.")
    else:
        _add(checks, "currency_validation", CheckStatus.PASS, "Currency is supported.", {"currency": currency})

    if invoice.due_date:
        try:
            date.fromisoformat(invoice.due_date)
        except ValueError:
            _add(checks, "due_date_format", CheckStatus.FAIL, "Due date must be an ISO date (YYYY-MM-DD).", {"due_date": invoice.due_date})
            hard_blocks.append("Due date is malformed.")
        else:
            _add(checks, "due_date_format", CheckStatus.PASS, "Due date is a valid ISO date.", {"due_date": invoice.due_date})
    else:
        _add(checks, "due_date_format", CheckStatus.SKIPPED, "Due date was not supplied.")

    known_supplier, matched_by = _find_supplier(invoice, suppliers)
    if known_supplier is None:
        _add(
            checks,
            "supplier_allowlist",
            CheckStatus.WARN,
            "Supplier was not found in the known-supplier allowlist.",
            {"supplier_name": invoice.supplier_name, "supplier_id": invoice.supplier_id, "matched_by": matched_by},
        )
        human_review.append("Supplier is unknown or not on the allowlist.")
    else:
        name_matches = _normalise_text(invoice.supplier_name) == _normalise_text(known_supplier.get("supplier_name"))
        id_matches = not invoice.supplier_id or _normalise_text(invoice.supplier_id) == _normalise_text(known_supplier.get("supplier_id"))
        if not name_matches or not id_matches:
            _add(
                checks,
                "supplier_identity_consistency",
                CheckStatus.WARN,
                "Supplier name and identifier do not consistently identify the known supplier.",
                {
                    "provided_name": invoice.supplier_name,
                    "provided_id": invoice.supplier_id,
                    "known_name": known_supplier.get("supplier_name"),
                    "known_id": known_supplier.get("supplier_id"),
                },
            )
            human_review.append("Supplier identity fields are inconsistent.")
        else:
            _add(
                checks,
                "supplier_allowlist",
                CheckStatus.PASS,
                "Supplier is known and identity fields are consistent.",
                {"supplier_id": known_supplier.get("supplier_id"), "matched_by": matched_by},
            )

    duplicate = False
    for previous in previous_invoices:
        same_invoice = _normalise_text(previous.get("invoice_number")) == _normalise_text(invoice.invoice_number)
        same_supplier = False
        if known_supplier is not None:
            same_supplier = _normalise_text(previous.get("supplier_id")) == _normalise_text(known_supplier.get("supplier_id"))
        same_supplier = same_supplier or (
            _normalise_text(previous.get("supplier_name")) == _normalise_text(invoice.supplier_name)
        )
        if same_invoice and same_supplier:
            duplicate = True
            duplicate_evidence = previous
            break
    if duplicate:
        _add(checks, "duplicate_invoice", CheckStatus.FAIL, "Invoice number already exists for this supplier.", {"matched_record": duplicate_evidence})
        hard_blocks.append("Duplicate invoice detected.")
    else:
        _add(checks, "duplicate_invoice", CheckStatus.PASS, "No matching prior invoice was found.", {"invoice_number": invoice.invoice_number})

    if invoice.amount is not None and invoice.amount.is_finite() and currency in supported_currencies:
        threshold_raw = rules.get("approval_thresholds", {}).get(currency)
        try:
            threshold = Decimal(str(threshold_raw))
        except (InvalidOperation, TypeError):
            threshold = None
        if threshold is not None and invoice.amount > threshold:
            _add(
                checks,
                "approval_threshold",
                CheckStatus.WARN,
                "Amount exceeds the agent's automatic-submission threshold.",
                {"amount": invoice.amount, "threshold": threshold, "currency": currency},
            )
            human_review.append(f"Amount exceeds the automatic approval threshold of {threshold} {currency}.")
        elif threshold is not None:
            _add(checks, "approval_threshold", CheckStatus.PASS, "Amount is within the automatic-submission threshold.", {"amount": invoice.amount, "threshold": threshold, "currency": currency})
        else:
            _add(checks, "approval_threshold", CheckStatus.SKIPPED, "No valid threshold is configured for this currency.")
    else:
        _add(checks, "approval_threshold", CheckStatus.SKIPPED, "Threshold check requires a valid amount and currency.")

    if known_supplier is None or invoice.bank_destination is None:
        _add(
            checks,
            "bank_detail_change",
            CheckStatus.SKIPPED,
            "Bank-detail comparison requires a known supplier and a supplied destination.",
        )
    else:
        expected_bank = _canonical_bank(known_supplier.get("bank_destination"))
        provided_bank = _canonical_bank(invoice.bank_destination)
        mismatched_fields = [
            key
            for key, value in (provided_bank or {}).items()
            if (expected_bank or {}).get(key) != value
        ]
        if not mismatched_fields:
            _add(
                checks,
                "bank_detail_change",
                CheckStatus.PASS,
                "Supplied bank details match the known supplier record for every supplied field.",
                {"matched": True, "compared_fields": sorted((provided_bank or {}).keys())},
            )
        else:
            action = str(rules.get("bank_detail_change_action", "HUMAN_REVIEW")).upper()
            status = CheckStatus.FAIL if action == "BLOCKED" else CheckStatus.WARN
            _add(
                checks,
                "bank_detail_change",
                status,
                "Supplied bank details differ from the known supplier record.",
                {"expected": expected_bank, "provided": provided_bank, "mismatched_fields": mismatched_fields, "configured_action": action},
            )
            if action == "BLOCKED":
                hard_blocks.append("Supplier bank details changed and policy blocks automatic handling.")
            else:
                human_review.append("Supplier bank details differ from the known record.")

    if enforce_requestor_permissions:
        policy = (requestor_permissions or {}).get(invoice.requestor or "")
        if not policy:
            _add(checks, "requestor_permissions", CheckStatus.WARN, "Requestor has no configured local permission policy.", {"requestor": invoice.requestor})
            human_review.append("Requestor has no configured local permission policy.")
        elif not policy.get("can_submit", False):
            _add(checks, "requestor_permissions", CheckStatus.WARN, "Requestor is not allowed to submit under the optional local policy.", {"requestor": invoice.requestor})
            human_review.append("Requestor is not allowed to submit under the optional local policy.")
        else:
            _add(checks, "requestor_permissions", CheckStatus.PASS, "Requestor passes the optional local policy.", {"requestor": invoice.requestor})
    else:
        _add(
            checks,
            "requestor_permissions",
            CheckStatus.SKIPPED,
            "Execution permission is intentionally delegated to the Axiom permissions boundary.",
            {"requestor": invoice.requestor, "enforced_locally": False},
        )

    return ValidationResult(checks, hard_blocks, human_review)


def decision_from_validation(validation: ValidationResult):
    from .models import Decision

    if validation.hard_block_reasons:
        return Decision.BLOCKED
    if validation.human_review_reasons:
        return Decision.HUMAN_REVIEW_REQUIRED
    return Decision.APPROVE_FOR_SUBMISSION
