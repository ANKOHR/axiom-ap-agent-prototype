import json
from pathlib import Path

from src.axiom_ap_agent.agent import APAgent
from src.axiom_ap_agent.audit import JsonlAuditLog
from src.axiom_ap_agent.permissions import MockAxiomPermissions


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _agent(tmp_path):
    data = PROJECT_ROOT / "data"
    load = lambda name: json.loads((data / name).read_text())
    return APAgent(
        suppliers=load("suppliers.json"),
        previous_invoices=load("previous_invoices.json"),
        rules=load("rules.json"),
        permissions_adapter=MockAxiomPermissions(load("permissions.json")),
        audit_log=JsonlAuditLog(tmp_path / "audit.jsonl"),
    )


def _normal_case():
    return {
        "supplier_name": "Northstar Office Supplies",
        "supplier_id": "SUP-001",
        "invoice_number": "INV-WORKFLOW-1",
        "amount": "320.00",
        "amount_minor": 32000,
        "currency": "GBP",
        "due_date": "2026-09-30",
        "bank_destination": {"sort_code": "20-12-33", "account_number": "12345678"},
        "requestor": "maria@demo.local",
    }


def test_normal_workflow_is_accepted_and_audited(tmp_path):
    agent = _agent(tmp_path)
    result = agent.handle(_normal_case())
    assert result.agent_decision.decision.value == "APPROVE_FOR_SUBMISSION"
    assert result.permission.allowed is True
    assert result.final_outcome == "ACCEPTED_BY_AXIOM_MOCK"
    records = JsonlAuditLog(tmp_path / "audit.jsonl").read_all()
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == result.request_id
    assert record["input_received"]["invoice_number"] == "INV-WORKFLOW-1"
    assert record["permission_check"]["authority"] == "mock_axiom_permissions"
    assert record["execution_boundary"]["agent_cannot"] == ["grant_permissions", "execute_payment", "move_real_money"]


def test_agent_approval_can_still_be_rejected_by_axiom(tmp_path):
    agent = _agent(tmp_path)
    case = _normal_case()
    case.update({"invoice_number": "INV-WORKFLOW-2", "amount": "3500.00", "amount_minor": 350000, "requestor": "junior@demo.local"})
    result = agent.handle(case)
    assert result.agent_decision.decision.value == "APPROVE_FOR_SUBMISSION"
    assert result.permission.allowed is False
    assert result.final_outcome == "REJECTED_BY_AXIOM_PERMISSION"


def test_human_review_does_not_call_permission_boundary(tmp_path):
    agent = _agent(tmp_path)
    result = agent.handle({
        "supplier_name": "Mystery Components Ltd",
        "invoice_number": "INV-WORKFLOW-3",
        "amount": "100.00",
        "currency": "GBP",
        "due_date": "2026-09-30",
        "requestor": "maria@demo.local",
    })
    assert result.agent_decision.decision.value == "HUMAN_REVIEW_REQUIRED"
    assert result.permission is None
    assert result.final_outcome == "HELD_FOR_HUMAN_REVIEW"
