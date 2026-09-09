"""Command-line demo for the AP/payout-agent prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .agent import APAgent, WorkflowResult
from .audit import JsonlAuditLog
from .axiom_staging import AxiomStagingAdapter
from .permissions import MockAxiomPermissions
from .staging_runner import StagingEvidenceRunner, render_concise_summary


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PACKAGE_ROOT / "data"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_agent(audit_file: Path) -> APAgent:
    suppliers = _load_json(DATA_ROOT / "suppliers.json")
    previous_invoices = _load_json(DATA_ROOT / "previous_invoices.json")
    rules = _load_json(DATA_ROOT / "rules.json")
    permissions = _load_json(DATA_ROOT / "permissions.json")
    return APAgent(
        suppliers=suppliers,
        previous_invoices=previous_invoices,
        rules=rules,
        permissions_adapter=MockAxiomPermissions(permissions),
        audit_log=JsonlAuditLog(audit_file),
        requestor_permissions=permissions,
        enforce_local_requestor_permissions=False,
    )


def demo_cases() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "CASE 1 - normal approved supplier",
            {
                "supplier_name": "Northstar Office Supplies",
                "supplier_id": "SUP-001",
                "invoice_number": "INV-2001",
                "amount": "320.00",
                "amount_minor": 32000,
                "currency": "GBP",
                "due_date": "2026-09-30",
                "bank_destination": {"account_name": "Northstar Office Supplies", "sort_code": "20-12-33", "account_number": "12345678"},
                "description": "September stationery order",
                "requestor": "maria@demo.local",
            },
        ),
        (
            "CASE 2 - unknown supplier",
            {
                "supplier_name": "Mystery Components Ltd",
                "invoice_number": "INV-2002",
                "amount": "450.00",
                "currency": "GBP",
                "due_date": "2026-09-30",
                "description": "Unrecognised supplier request",
                "requestor": "maria@demo.local",
            },
        ),
        (
            "CASE 3 - duplicate invoice",
            {
                "supplier_name": "Northstar Office Supplies",
                "supplier_id": "SUP-001",
                "invoice_number": "INV-1001",
                "amount": "180.00",
                "currency": "GBP",
                "due_date": "2026-09-30",
                "description": "Repeated invoice number",
                "requestor": "maria@demo.local",
            },
        ),
        (
            "CASE 4 - changed bank details",
            {
                "supplier_name": "Northstar Office Supplies",
                "supplier_id": "SUP-001",
                "invoice_number": "INV-2004",
                "amount": "275.00",
                "currency": "GBP",
                "due_date": "2026-09-30",
                "bank_destination": {"account_name": "Northstar Office Supplies", "sort_code": "11-22-33", "account_number": "12345678"},
                "description": "Destination differs from supplier record",
                "requestor": "maria@demo.local",
            },
        ),
        (
            "CASE 5 - agent approval, Axiom permission rejection",
            {
                "supplier_name": "Northstar Office Supplies",
                "supplier_id": "SUP-001",
                "invoice_number": "INV-2005",
                "amount": "3500.00",
                "currency": "GBP",
                "due_date": "2026-09-30",
                "description": "Within agent threshold but above requestor limit",
                "requestor": "junior@demo.local",
            },
        ),
    ]


def _print_result(label: str, result: WorkflowResult) -> None:
    checks = result.audit_record["validation_checks"]
    counts: dict[str, int] = {}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    check_summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"\n{label}")
    print(f"  request_id: {result.request_id}")
    print(f"  agent_decision: {result.agent_decision.decision.value}")
    print(f"  checks: {check_summary}")
    for reason in result.agent_decision.reasons:
        print(f"  reason: {reason}")
    if result.permission is not None:
        print(f"  axiom_mock_permission: {'ALLOWED' if result.permission.allowed else 'REJECTED'}")
        print(f"  axiom_mock_reason: {result.permission.reason}")
    else:
        print("  axiom_mock_permission: NOT_RUN")
    print(f"  final_outcome: {result.final_outcome}")


def run_demo(audit_file: Path) -> None:
    agent = build_agent(audit_file)
    print("AP/payout-agent deterministic demo")
    print(f"Structured audit JSONL: {audit_file}")
    for label, case in demo_cases():
        _print_result(label, agent.handle(case))
    print("\nNo real payment API was called and no money moved.")


def run_staging_suite(report_path: Path, dry_run: bool = False) -> int:
    runner = StagingEvidenceRunner(
        adapter=AxiomStagingAdapter(),
        report_path=report_path,
        dry_run=dry_run,
    )
    report = runner.run()
    print(render_concise_summary(report))
    print(f"Evidence report: {report_path}")
    if dry_run:
        print("No network request was made and AXIOM_AGENT_PASSPORT was not read.")
    has_error = any(
        isinstance(record.get("http_status"), str) and record["http_status"].startswith("ERROR:")
        for record in report["scenarios"]
    )
    return 1 if not dry_run and has_error else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AP/payout-agent prototype.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="run the five built-in demonstration cases")
    mode.add_argument("--request", type=Path, help="read one JSON or simple-text request from a file")
    mode.add_argument("--staging-suite", action="store_true", help="run the five Axiom staging trial scenarios")
    parser.add_argument("--audit-file", type=Path, default=Path("audit/audit.jsonl"), help="append structured JSONL audit records here")
    parser.add_argument(
        "--staging-report",
        type=Path,
        default=Path("audit/staging-evidence.json"),
        help="write the staging suite evidence report here",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the staging suite without reading the passport or using the network",
    )
    args = parser.parse_args()

    if args.dry_run and not args.staging_suite:
        parser.error("--dry-run can only be used with --staging-suite")

    if args.staging_suite:
        staging_report = args.staging_report
        if not staging_report.is_absolute():
            staging_report = Path.cwd() / staging_report
        exit_code = run_staging_suite(staging_report, dry_run=args.dry_run)
        if exit_code:
            raise SystemExit(exit_code)
        return

    audit_file = args.audit_file
    if not audit_file.is_absolute():
        audit_file = Path.cwd() / audit_file
    if args.demo:
        run_demo(audit_file)
        return

    agent = build_agent(audit_file)
    result = agent.handle(args.request)
    _print_result("SINGLE REQUEST", result)
    print(json.dumps(result.audit_record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
