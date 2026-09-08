import json
from pathlib import Path

from src.axiom_ap_agent.models import PayoutRequest
from src.axiom_ap_agent.permissions import MockAxiomPermissions


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _permissions():
    return json.loads((PROJECT_ROOT / "data" / "permissions.json").read_text())


def _payout(request_id="req-test", requestor="maria@demo.local", amount="320.00"):
    from decimal import Decimal

    return PayoutRequest(request_id, "Northstar Office Supplies", "SUP-001", "INV-X", Decimal(amount), "GBP", "2026-09-30", None, "Test", requestor)


def test_allowed_requestor_gets_permission_and_can_submit():
    adapter = MockAxiomPermissions(_permissions())
    payout = _payout()
    permission = adapter.check_permission(payout)
    assert permission.allowed is True
    result = adapter.submit_payout_request(payout, permission)
    assert result.accepted is True
    assert result.outcome == "ACCEPTED_BY_AXIOM_MOCK"


def test_amount_limit_rejects_even_when_agent_prepared_request():
    adapter = MockAxiomPermissions(_permissions())
    payout = _payout(requestor="junior@demo.local", amount="3500.00")
    permission = adapter.check_permission(payout)
    assert permission.allowed is False
    assert "amount" in permission.reason.lower()
    result = adapter.submit_payout_request(payout, permission)
    assert result.accepted is False
    assert result.outcome == "PERMISSION_REJECTED"


def test_permission_token_is_one_time_and_request_bound():
    adapter = MockAxiomPermissions(_permissions())
    payout = _payout()
    permission = adapter.check_permission(payout)
    first = adapter.submit_payout_request(payout, permission)
    second = adapter.submit_payout_request(payout, permission)
    assert first.accepted is True
    assert second.outcome == "INVALID_PERMISSION_TOKEN"
