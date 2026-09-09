from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from src.axiom_ap_agent.axiom_staging import AxiomStagingAdapter, HttpResponse
from src.axiom_ap_agent.staging_runner import StagingEvidenceRunner
from src.axiom_ap_agent.staging_scenarios import staging_scenarios


def _runtime_secret() -> str:
    return f"runner-runtime-only-{uuid4().hex}"


class ScenarioTransport:
    """A contract-test double for server outcomes, not a local policy implementation."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fingerprints: dict[str, str] = {}

    def post(self, url, headers, body, timeout):
        request = json.loads(body)
        params = request["params"]
        key = headers["Idempotency-Key"]
        fingerprint = json.dumps(params, sort_keys=True)
        self.calls.append({"url": url, "headers": dict(headers), "params": params})

        if key in self.fingerprints:
            if fingerprint == self.fingerprints[key]:
                return HttpResponse(
                    status_code=200,
                    headers={"Content-Type": "application/json", "X-Idempotent-Replayed": "true"},
                    body=b'{"status":"accepted","request_id":"axiom-req-allowed","replayed":true}',
                )
            return HttpResponse(
                status_code=409,
                headers={"Content-Type": "application/json", "X-Request-Id": "axiom-req-allowed"},
                body=b'{"error":"idempotency_mismatch","request_id":"axiom-req-allowed"}',
            )

        self.fingerprints[key] = fingerprint
        if params["merchant_id"] == "merchant.blocked-supplier.test":
            return HttpResponse(
                status_code=403,
                headers={"Content-Type": "application/json", "X-Request-Id": "axiom-req-blocked-merchant"},
                body=b'{"error":"payment_policy_violation","reason":"merchant not allowed","provider_dispatch":"none"}',
            )
        if params["amount_minor"] == 7500 and params["merchant_ref"] == "inv-blocked-amount-001":
            return HttpResponse(
                status_code=403,
                headers={"Content-Type": "application/json", "X-Request-Id": "axiom-req-blocked-amount"},
                body=b'{"error":"payment_policy_violation","reason":"limit exceeded","provider_dispatch":"none"}',
            )
        return HttpResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=b'{"status":"accepted","request_id":"axiom-req-allowed","provider_dispatch":"test-mode"}',
        )


def _fixed_clock():
    values = iter(("2026-09-09T10:00:00Z", "2026-09-09T10:05:00Z"))
    return lambda: next(values, "2026-09-09T10:05:00Z")


def test_staging_scenarios_match_the_supplied_trial_values() -> None:
    scenarios = staging_scenarios()

    assert [scenario.params.to_dict() for scenario in scenarios] == [
        {
            "amount_minor": 2500,
            "currency": "GBP",
            "merchant_id": "merchant.acme-supplies.test",
            "merchant_ref": "inv-allowed-001",
        },
        {
            "amount_minor": 2500,
            "currency": "GBP",
            "merchant_id": "merchant.blocked-supplier.test",
            "merchant_ref": "inv-blocked-merchant-001",
        },
        {
            "amount_minor": 7500,
            "currency": "GBP",
            "merchant_id": "merchant.acme-supplies.test",
            "merchant_ref": "inv-blocked-amount-001",
        },
        {
            "amount_minor": 2500,
            "currency": "GBP",
            "merchant_id": "merchant.acme-supplies.test",
            "merchant_ref": "inv-allowed-001",
        },
        {
            "amount_minor": 7500,
            "currency": "GBP",
            "merchant_id": "merchant.acme-supplies.test",
            "merchant_ref": "inv-allowed-001",
        },
    ]
    assert scenarios[3].reuse_scenario_key == "allowed"
    assert scenarios[4].reuse_scenario_key == "allowed"


def test_live_runner_records_all_five_outcomes_and_keeps_ap_separate(monkeypatch, tmp_path: Path) -> None:
    secret = _runtime_secret()
    monkeypatch.setenv("AXIOM_AGENT_PASSPORT", secret)
    transport = ScenarioTransport()
    report_path = tmp_path / "staging-evidence.json"
    runner = StagingEvidenceRunner(
        adapter=AxiomStagingAdapter(transport=transport),
        report_path=report_path,
        clock=_fixed_clock(),
    )

    report = runner.run()
    records = report["scenarios"]

    assert [record["scenario_label"] for record in records] == [
        "allowed payment",
        "blocked merchant",
        "blocked amount",
        "idempotent replay",
        "idempotency mismatch",
    ]
    assert [record["http_status"] for record in records] == [200, 403, 403, 200, 409]
    assert all(record["ap_agent_decision"]["decision"] == "APPROVE_FOR_SUBMISSION" for record in records)
    assert records[1]["response_body"]["error"] == "payment_policy_violation"
    assert records[2]["response_body"]["reason"] == "limit exceeded"
    assert records[3]["replay_indication"] is True
    assert records[4]["response_body"]["error"] == "idempotency_mismatch"
    assert records[0]["idempotency_key"] == records[3]["idempotency_key"] == records[4]["idempotency_key"]
    assert records[0]["local_agent_job_id"] == records[3]["local_agent_job_id"] == records[4]["local_agent_job_id"]
    assert report["local_agent_job_to_axiom_request_id"][records[0]["local_agent_job_id"]] == "axiom-req-allowed"
    assert report["passport_included"] is False
    assert report["safety"]["passport_value_logged"] is False
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    if secret in report_text:
        raise AssertionError("the runtime passport leaked into the evidence report")
    assert len(transport.calls) == 5


def test_dry_run_generates_five_redacted_previews_without_passport_or_network(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AXIOM_AGENT_PASSPORT", raising=False)
    transport = ScenarioTransport()
    report_path = tmp_path / "staging-dry-run.json"
    report = StagingEvidenceRunner(
        adapter=AxiomStagingAdapter(transport=transport),
        report_path=report_path,
        dry_run=True,
        clock=_fixed_clock(),
    ).run()

    assert report["mode"] == "dry_run"
    assert len(report["scenarios"]) == 5
    assert all(record["http_status"] == "NOT_SENT" for record in report["scenarios"])
    assert all(record["submission"] == "PREVIEW_ONLY" for record in report["scenarios"])
    assert all(record["redacted_request_body"]["passport"] == "[REDACTED]" for record in report["scenarios"])
    assert all(record["ap_agent_decision"]["decision"] == "APPROVE_FOR_SUBMISSION" for record in report["scenarios"])
    assert report["local_agent_job_to_axiom_request_id"] == {}
    assert transport.calls == []
    assert "AXIOM_AGENT_PASSPORT" in json.dumps(report)
    assert report_path.exists()
