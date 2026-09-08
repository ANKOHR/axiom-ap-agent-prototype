from decimal import Decimal
from pathlib import Path

from src.axiom_ap_agent.models import Decision, InvoiceRequest
from src.axiom_ap_agent.parser import invoice_from_parse, parse_request
from src.axiom_ap_agent.validator import decision_from_validation, validate_request


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    import json

    data = PROJECT_ROOT / "data"
    return (
        json.loads((data / "suppliers.json").read_text()),
        json.loads((data / "previous_invoices.json").read_text()),
        json.loads((data / "rules.json").read_text()),
        json.loads((data / "permissions.json").read_text()),
    )


def _invoice(payload):
    return invoice_from_parse(parse_request(payload))


def test_duplicate_invoice_is_blocked():
    suppliers, previous, rules, _ = _config()
    validation = validate_request(_invoice({
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-1001",
        "amount": "180.00",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "maria@demo.local",
    }), suppliers, previous, rules)
    assert decision_from_validation(validation) == Decision.BLOCKED
    assert "Duplicate invoice detected." in validation.hard_block_reasons


def test_unknown_supplier_requires_human_review():
    suppliers, previous, rules, _ = _config()
    validation = validate_request(_invoice({
        "supplier_name": "Unknown Supplier",
        "invoice_number": "INV-X",
        "amount": "100.00",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "maria@demo.local",
    }), suppliers, previous, rules)
    assert decision_from_validation(validation) == Decision.HUMAN_REVIEW_REQUIRED


def test_threshold_requires_human_review():
    suppliers, previous, rules, _ = _config()
    validation = validate_request(_invoice({
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-HIGH",
        "amount": "5000.01",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "finance@demo.local",
    }), suppliers, previous, rules)
    assert decision_from_validation(validation) == Decision.HUMAN_REVIEW_REQUIRED
    assert any("threshold" in reason.lower() for reason in validation.human_review_reasons)


def test_bank_detail_mismatch_requires_human_review():
    suppliers, previous, rules, _ = _config()
    validation = validate_request(_invoice({
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-BANK",
        "amount": "100.00",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "bank_destination": {"sort_code": "11-22-33", "account_number": "12345678"},
        "requestor": "maria@demo.local",
    }), suppliers, previous, rules)
    assert decision_from_validation(validation) == Decision.HUMAN_REVIEW_REQUIRED
    assert any("bank" in reason.lower() for reason in validation.human_review_reasons)


def test_malformed_and_inconsistent_amount_is_blocked():
    suppliers, previous, rules, _ = _config()
    parsed = parse_request({
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-BAD",
        "amount": "100.00",
        "amount_minor": 9900,
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "maria@demo.local",
    })
    validation = validate_request(invoice_from_parse(parsed), suppliers, previous, rules, parse_errors=parsed.errors)
    assert decision_from_validation(validation) == Decision.BLOCKED
    assert "Amount fields are inconsistent." in validation.hard_block_reasons


def test_malformed_amount_input_is_blocked():
    suppliers, previous, rules, _ = _config()
    parsed = parse_request({
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-MALFORMED",
        "amount": "not-a-number",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "maria@demo.local",
    })
    validation = validate_request(invoice_from_parse(parsed), suppliers, previous, rules, parse_errors=parsed.errors)
    assert decision_from_validation(validation) == Decision.BLOCKED
    assert any(check.name == "input_parsing" and check.status.value == "FAIL" for check in validation.checks)


def test_optional_local_requestor_policy_can_be_enabled():
    suppliers, previous, rules, permissions = _config()
    validation = validate_request(
        _invoice({
            "supplier_name": "Northstar Office Supplies",
            "supplier_id": "SUP-001",
            "invoice_number": "INV-LOCAL-POLICY",
            "amount": "100.00",
            "currency": "GBP",
            "due_date": "2026-09-30",
            "requestor": "blocked@demo.local",
        }),
        suppliers,
        previous,
        rules,
        requestor_permissions=permissions,
        enforce_requestor_permissions=True,
    )
    assert decision_from_validation(validation) == Decision.HUMAN_REVIEW_REQUIRED
