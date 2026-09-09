"""AP-side staging scenarios and safe evidence-report generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .axiom_staging import (
    AXIOM_AGENT_PASSPORT_ENV,
    AxiomStagingAdapter,
    PaymentCreateParams,
    PassportMissingError,
    StagingAdapterError,
    StagingCallResult,
    StagingTransportError,
)
from .models import AgentDecision, CheckStatus, Decision
from .staging_scenarios import StagingScenario, staging_scenarios


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def commercial_decision(params: PaymentCreateParams) -> AgentDecision:
    """Make the AP-side suitability decision without reimplementing Axiom policy."""

    checks_performed = [
        "amount_minor_present_and_positive",
        "currency_present",
        "merchant_id_present",
        "merchant_ref_present",
    ]
    evidence: list[dict[str, Any]] = []
    reasons: list[str] = []

    amount_ok = isinstance(params.amount_minor, int) and not isinstance(params.amount_minor, bool) and params.amount_minor > 0
    evidence.append(
        {
            "check": "amount_minor_present_and_positive",
            "status": CheckStatus.PASS.value if amount_ok else CheckStatus.FAIL.value,
        }
    )
    if not amount_ok:
        reasons.append("amount_minor must be a positive integer before submission.")

    for field_name in ("currency", "merchant_id", "merchant_ref"):
        value = getattr(params, field_name)
        valid = isinstance(value, str) and bool(value.strip())
        evidence.append(
            {
                "check": f"{field_name}_present",
                "status": CheckStatus.PASS.value if valid else CheckStatus.FAIL.value,
            }
        )
        if not valid:
            reasons.append(f"{field_name} is required before submission.")

    decision = Decision.APPROVE_FOR_SUBMISSION if not reasons else Decision.HUMAN_REVIEW_REQUIRED
    return AgentDecision(
        decision=decision,
        reasons=reasons,
        checks_performed=checks_performed,
        evidence=evidence,
        confidence_status="DETERMINISTIC_COMMERCIAL_SUITABILITY",
    )


def _safe_adapter_error(error: StagingAdapterError) -> str:
    """Convert adapter failures to a small allow-listed message set."""

    if isinstance(error, PassportMissingError):
        return f"{AXIOM_AGENT_PASSPORT_ENV} is not set."
    if isinstance(error, StagingTransportError):
        return "Axiom staging request failed."
    return "Axiom staging adapter rejected the request locally."


class StagingEvidenceRunner:
    """Run the five ordered cases and persist only evidence-safe report data."""

    def __init__(
        self,
        adapter: AxiomStagingAdapter,
        report_path: Path,
        dry_run: bool = False,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.adapter = adapter
        self.report_path = Path(report_path)
        self.dry_run = dry_run
        self.clock = clock

    def _job_ids(self, scenarios: tuple[StagingScenario, ...]) -> dict[str, str]:
        ids: dict[str, str] = {}
        for scenario in scenarios:
            if scenario.reuse_scenario_key is not None:
                try:
                    ids[scenario.scenario_key] = ids[scenario.reuse_scenario_key]
                except KeyError:
                    raise ValueError(
                        f"Scenario {scenario.scenario_key!r} references an earlier scenario that does not exist."
                    ) from None
                continue
            slug = scenario.scenario_key.replace("_", "-")
            ids[scenario.scenario_key] = f"job-{slug}-{uuid4().hex[:12]}"
        return ids

    def _new_report(self, started_at: str) -> dict[str, Any]:
        return {
            "report_schema_version": "1.0",
            "suite_id": f"staging-suite-{uuid4().hex[:12]}",
            "started_at": started_at,
            "completed_at": None,
            "mode": "dry_run" if self.dry_run else "live",
            "endpoint": self.adapter.endpoint,
            "passport_environment_variable": AXIOM_AGENT_PASSPORT_ENV,
            "passport_included": False,
            "scenarios": [],
            "local_agent_job_to_axiom_request_id": {},
            "safety": {
                "passport_value_logged": False,
                "real_payment_execution": False,
                "staging_requests_sent": False,
            },
        }

    def _write_report(self, report: dict[str, Any]) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _record_for(
        self,
        scenario: StagingScenario,
        job_id: str,
        decision: AgentDecision,
        preview: StagingCallResult,
        call_result: StagingCallResult | None,
        error: str | None,
    ) -> dict[str, Any]:
        result = call_result or preview
        if call_result is not None:
            http_status: int | str | None = result.status_code
        elif self.dry_run:
            http_status = "NOT_SENT"
        elif decision.decision is not Decision.APPROVE_FOR_SUBMISSION:
            http_status = "NOT_SUBMITTED"
        elif error is not None:
            http_status = f"ERROR: {error}"
        else:
            http_status = None
        record: dict[str, Any] = {
            "scenario_label": scenario.label,
            "local_agent_job_id": job_id,
            "ap_agent_decision": decision.to_dict(),
            "redacted_request_body": preview.redacted_request_body,
            "http_status": http_status,
            "response_body": result.response_body if call_result is not None else None,
            "response_headers": result.response_headers if call_result is not None else {},
            "axiom_request_id": result.axiom_request_id if call_result is not None else None,
            "replay_indication": result.replayed if call_result is not None else None,
        }
        return record

    def run(self) -> dict[str, Any]:
        scenarios = staging_scenarios()
        job_ids = self._job_ids(scenarios)
        report = self._new_report(self.clock())

        for scenario in scenarios:
            job_id = job_ids[scenario.scenario_key]
            decision = commercial_decision(scenario.params)
            preview = self.adapter.preview(
                scenario.params,
                agent_job_id=job_id,
                attempt_number=scenario.attempt_number,
            )
            call_result: StagingCallResult | None = None
            error: str | None = None

            if decision.decision is Decision.APPROVE_FOR_SUBMISSION and not self.dry_run:
                try:
                    call_result = self.adapter.invoke(
                        scenario.params,
                        agent_job_id=job_id,
                        attempt_number=scenario.attempt_number,
                    )
                except StagingAdapterError as adapter_error:
                    error = _safe_adapter_error(adapter_error)

            if call_result is not None:
                report["safety"]["staging_requests_sent"] = True

            record = self._record_for(scenario, job_id, decision, preview, call_result, error)
            report["scenarios"].append(record)
            axiom_request_id = record.get("axiom_request_id")
            if axiom_request_id is not None:
                report["local_agent_job_to_axiom_request_id"].setdefault(job_id, axiom_request_id)
            self._write_report(report)

        report["completed_at"] = self.clock()
        self._write_report(report)
        return report


def render_concise_summary(report: dict[str, Any]) -> str:
    """Render useful console evidence without dumping response or credential data."""

    lines = [f"Axiom staging suite ({report['mode']})"]
    for scenario in report["scenarios"]:
        decision = scenario["ap_agent_decision"]["decision"]
        status = scenario["http_status"]
        if status is None:
            status = "NOT_SENT"
        request_id = scenario.get("axiom_request_id") or "-"
        replay = scenario.get("replay_indication")
        replay_text = "-" if replay is None else str(replay).lower() if isinstance(replay, bool) else str(replay)
        lines.append(
            f"  {scenario['scenario_label']}: AP={decision} HTTP={status} "
            f"Axiom_request_id={request_id} replay={replay_text}"
        )
    return "\n".join(lines)


__all__ = ["StagingEvidenceRunner", "commercial_decision", "render_concise_summary"]
