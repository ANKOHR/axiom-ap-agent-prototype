# Axiom staging trial readiness

Status: **READY FOR THE GOVERNED LIVE STAGING TEST**. This package is an asynchronous codebase review, not a staging result. No Axiom request was made while preparing it, and no real Axiom passport was supplied or read.

## Executive summary for Bashir

The repository contains a focused `payment.create` adapter for `POST /v1/invoke`, an AP-side commercial-suitability decision, safe idempotency/request-ID handling, and a sequential five-case evidence runner. All offline checks pass; the only live gate is Axiom issuing the short-lived `AXIOM_AGENT_PASSPORT` for the agreed one-hour window, after which Axiom's actual response and replay semantics must be recorded.

## Technical staging-readiness report

### Wire contract

`src/axiom_ap_agent/axiom_staging.py` is the only staging transport boundary. It posts to:

```text
POST https://api-staging.axiomgo.ai/v1/invoke
```

The private runtime body is constructed as exactly:

```json
{
  "action": "payment.create",
  "params": {
    "amount_minor": 2500,
    "currency": "GBP",
    "merchant_id": "merchant.acme-supplies.test",
    "merchant_ref": "inv-allowed-001"
  },
  "passport": "[runtime value supplied by the environment]"
}
```

The adapter sends `Content-Type: application/json`, `Accept: application/json`, and `Idempotency-Key: henry-ap-<agent-job-id>-<attempt-number>`. The request body is checked against the exact top-level shape and the four allowed payment parameters. `request_id`, `trace_id`, `correlation_id`, `client_request_id`, and `agent_job_id` are rejected if they appear in the body or params.

### Decision and authority boundary

`staging_runner.commercial_decision()` checks only whether the four payment fields are present and structurally suitable for submission. It does not check merchant allowlists, delegated-authority limits, or Axiom payment policy. Every supplied staging case is therefore passed to the adapter with `APPROVE_FOR_SUBMISSION` in the local contract test.

The `ScenarioTransport` in `tests/test_staging_runner.py` is explicitly a test double. Its 200/403/409 responses are simulated contract fixtures, not Axiom observations and not production policy code. The existing `MockAxiomPermissions` remains confined to the original offline demo.

### Idempotency and request-ID mapping

`generate_idempotency_key()` enforces the agreed prefix, local job ID, and positive attempt number. The runner gives the allowed, replay, and mismatch cases the same local job ID, attempt number, and resulting key; the replay repeats the exact params and the mismatch changes only `amount_minor` to `7500`.

The adapter extracts an Axiom `request_id` from the response body or approved response headers. The runner keeps the local `agent_job_id` in the evidence report and populates `local_agent_job_to_axiom_request_id`; it never puts the local ID into the Axiom body. The per-scenario Axiom ID is null in a dry-run and will remain null if Axiom does not return one.

### Passport safety

- The production adapter reads `AXIOM_AGENT_PASSPORT` only inside `AxiomStagingAdapter.invoke()`, at request time. Construction and `preview()` do not read it.
- The runtime passport is not a field on `StagingCallResult`, the evidence record, or the report envelope; the request representation contains only `passport: [REDACTED]`. No real passport is present in any fixture.
- `redact_secrets()` recursively removes sensitive fields and exact runtime secret values from response bodies, approved response headers, request representations, and adapter output.
- Transport failures are converted to an allow-listed message with no exception chaining, preventing arbitrary exception text from echoing the request body.
- The console summary contains scenario, AP decision, status, Axiom ID, and replay indication only; it does not print request bodies or responses.
- The repository and reachable Git history contain no strong credential-shaped value. The only credential-related source text is safety logic, generated-value test scaffolding, placeholders, and the synthetic offline permission-token implementation.
- `audit/*` is ignored except `audit/.gitkeep`; generated staging evidence cannot be added accidentally by a normal `git add .`.

### Five requested scenarios

The values below are the exact values encoded in `src/axiom_ap_agent/staging_scenarios.py`. Any live outcome shown as an expectation is from Bashir's trial specification; it is not claimed as observed here.

| Scenario | Exact params | Local evidence | Live check still required |
| --- | --- | --- | --- |
| allowed payment | `2500 GBP`, `merchant.acme-supplies.test`, `inv-allowed-001` | Dry-run: AP approves, request not sent. Contract test simulates 200 accepted/test-mode. | Axiom's actual status/body/headers, request ID, and test-mode dispatch. |
| blocked merchant | `2500 GBP`, `merchant.blocked-supplier.test`, `inv-blocked-merchant-001` | Dry-run: AP approves, request not sent. Contract test simulates 403 policy violation. | Axiom's independent merchant-policy response and no provider dispatch. |
| blocked amount | `7500 GBP`, `merchant.acme-supplies.test`, `inv-blocked-amount-001` | Dry-run: AP approves, request not sent. Contract test simulates 403 policy violation. | Axiom's independent amount-policy response and no provider dispatch. |
| idempotent replay | Exact repeat of the allowed params, same local job/key | Contract test simulates a replay indication and no duplicate dispatch. | Axiom's replay indication and confirmation that execution was not duplicated. |
| idempotency mismatch | Same allowed key, but `amount_minor: 7500`; merchant/ref remain `merchant.acme-supplies.test` / `inv-allowed-001` | Contract test simulates `idempotency_mismatch`; dry-run sends nothing. | Axiom's actual mismatch status/body, expected by the trial plan to be `409`. |

### Evidence report

`StagingEvidenceRunner._record_for()` emits exactly these nine fields for every scenario:

```text
scenario_label
local_agent_job_id
ap_agent_decision
redacted_request_body
http_status
response_body
response_headers
axiom_request_id
replay_indication
```

The report envelope also contains non-secret run metadata (`mode`, endpoint, timestamps, environment-variable name, scenario list, local-to-Axiom mapping, and safety flags). `passport_included` is explicitly `false`. `audit/staging-evidence.json` is the default live output and is intentionally ignored by Git.

## Requirement-to-evidence map

| Staging requirement | Exact implementation/test/file | Evidence status |
| --- | --- | --- |
| `POST https://api-staging.axiomgo.ai/v1/invoke` | `AXIOM_STAGING_URL`, `UrllibTransport.post()`, and `AxiomStagingAdapter.invoke()` in [`src/axiom_ap_agent/axiom_staging.py`](../src/axiom_ap_agent/axiom_staging.py) | Implemented; no live call made. |
| Action `payment.create` | `AXIOM_ACTION`, `PaymentCreateParams`, `build_request_body()`; `test_staging_request_shape_headers_key_and_request_id_mapping` in [`tests/test_staging_adapter.py`](../tests/test_staging_adapter.py) | Implemented and offline-tested. |
| Exact four `params` fields and types | `_coerce_params()` and `PaymentCreateParams.to_dict()` in [`axiom_staging.py`](../src/axiom_ap_agent/axiom_staging.py) | Implemented and offline-tested. |
| Required JSON/Accept/idempotency headers | `request_headers` in `AxiomStagingAdapter.invoke()` plus the request-shape test | Implemented and offline-tested. |
| Idempotency key `henry-ap-<agent-job-id>-<attempt-number>` | `generate_idempotency_key()` plus `test_idempotency_key_generation`; replay/mismatch reuse assertions in [`tests/test_staging_runner.py`](../tests/test_staging_runner.py) | Implemented and simulated end-to-end with a transport double. |
| No forbidden body fields | `FORBIDDEN_BODY_FIELDS`, exact-body assertions in `build_request_body()`, and `test_forbidden_fields_are_absent_from_generated_body` | Implemented and tested. |
| Keep local `agent_job_id`; map returned Axiom `request_id` | `StagingEvidenceRunner._job_ids()`, `extract_axiom_request_id()`, and `local_agent_job_to_axiom_request_id` report field | Implemented; mapping is tested with simulated response IDs. |
| AP agent makes commercial-suitability decision | `commercial_decision()` in [`src/axiom_ap_agent/staging_runner.py`](../src/axiom_ap_agent/staging_runner.py) | Implemented; all five local decisions are `APPROVE_FOR_SUBMISSION`. |
| Do not duplicate Axiom delegated-authority/payment-policy logic | Staging runner imports only staging adapter/scenario/domain types; it does not invoke `validator.py` or `permissions.py`. Boundary assertion is in `test_live_runner_records_all_five_outcomes_and_keeps_ap_separate` | Implemented; policy outcomes remain Axiom-side in the live path. |
| Passport only from `AXIOM_AGENT_PASSPORT` at runtime | Single production read at `AxiomStagingAdapter.invoke()`; `preview()` is credential-free | Implemented and tested with environment deletion. |
| Passport absent must fail safely before network | `PassportMissingError`, `_safe_adapter_error()`, CLI nonzero return, and `test_live_cli_without_passport_exits_nonzero_after_safe_report` | Verified in a fresh process with a network sentinel: five safe errors, exit 1, zero calls. |
| Passport not persisted/printed/logged or included in evidence | `redact_secrets()`, `safe_response_headers()`, generic transport error, console summary, `passport_included: false`; redaction tests in [`tests/test_staging_adapter.py`](../tests/test_staging_adapter.py) and runner report assertions | Implemented and offline-tested; no live secret existed to audit. |
| Exact allowed/blocked/replay/mismatch scenarios | [`src/axiom_ap_agent/staging_scenarios.py`](../src/axiom_ap_agent/staging_scenarios.py) and `test_staging_scenarios_match_the_supplied_trial_values` | Exact values verified. |
| Capture requested evidence for every scenario | `StagingEvidenceRunner._record_for()` and required-record-field assertion in `test_live_runner_records_all_five_outcomes_and_keeps_ap_separate` | Implemented; dry-run has null/not-sent placeholders by design. |
| Execute all five in the live window without automatic retries | `python -m src.main --staging-suite`; ordered `StagingEvidenceRunner.run()` loop; no retry code | Ready; live execution remains intentionally unperformed. |
| Generate concise JSON evidence | `--staging-report`, `_write_report()`, report schema/safety fields, and dry-run runner test | Implemented; generated reports are Git-ignored. |
| Preserve existing synthetic tests and offline demo | [`tests/`](../tests/), `python -m src.main --demo`, and the original `APAgent`/`MockAxiomPermissions` path | 25 tests pass; offline demo completed with no real API. |

## Verification run in this review

All commands were run from the repository root with `AXIOM_AGENT_PASSPORT` absent unless a test created an in-memory random sentinel. No command below contacted Axiom.

| Check | Result |
| --- | --- |
| `python -m pytest -q` | **PASS — 25 passed in 0.51s** |
| `python -m compileall -q src tests` | **PASS** |
| `python -m src.main --demo --audit-file audit\review-offline-demo.jsonl` | **PASS — five synthetic cases; explicitly reports no real payment API and no money moved** |
| `python -m src.main --staging-suite --dry-run --staging-report audit\review-staging-dry-run.json` | **PASS — five AP approvals, five `NOT_SENT`, no passport read, no network** |
| Fresh no-passport live-path guard with `urlopen` replaced by a fail-fast sentinel | **PASS — exit 1, five `AXIOM_AGENT_PASSPORT is not set.` errors, zero transport calls, `staging_requests_sent: false`** |
| `git diff --check` and strong credential-pattern scan over current files and reachable history | **PASS — no strong credential-shaped values found** |

The dry-run transcript is kept separately in [`docs/axiom-staging-dry-run-transcript.md`](axiom-staging-dry-run-transcript.md). It is generated local evidence only; it contains no Axiom response, HTTP status, request ID, or live replay claim.

## Small fixes made for reviewability

No production code changes were needed. This review package makes two documentation-only changes:

1. Renamed the README staging heading to the requested `Axiom staging trial readiness`.
2. Added this report and a clean, explicit dry-run transcript, linked from the README landing page.

The generated files under `audit/` were not added to the package. They remain ignored, and only `audit/.gitkeep` is tracked.

## Live handoff

The remaining gate is Axiom-side access, not local implementation:

- Bashir supplies the short-lived passport and confirms the one-hour window.
- The live run must establish Axiom's actual status codes, response bodies, useful headers, returned `request_id` values, replay signal, and provider-dispatch behavior.
- Those values must be copied from the generated local evidence report after the run; none are inferred from the contract-test double.

Use a hidden PowerShell prompt so the passport is not typed into the visible command line:

```powershell
$securePassport = Read-Host "Paste the short-lived Axiom staging passport" -AsSecureString
$passportPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassport)
try {
    $env:AXIOM_AGENT_PASSPORT = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passportPtr)
    python -m src.main --staging-suite --staging-report audit\staging-evidence.json
}
finally {
    if ($passportPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passportPtr)
    }
    Remove-Variable passportPtr,securePassport -ErrorAction SilentlyContinue
    Remove-Item Env:AXIOM_AGENT_PASSPORT -ErrorAction SilentlyContinue
}
```

Do not rerun a surprising case blindly. Preserve `audit\staging-evidence.json`, inspect the response and key/request-ID relationships, and share only the redacted report—not the passport or raw terminal contents.

## Final asynchronous conclusion

Already demonstrable without Axiom access: the exact request shape, required headers, forbidden-field exclusion, AP/Axiom decision separation, runtime-only passport boundary, secret redaction safeguards, local-to-Axiom ID mapping logic, exact five-case sequencing, idempotency-key reuse rules, safe no-passport failure, offline demo, tests, compile check, and redacted JSON report format.

Not demonstrable until the live window: any real Axiom HTTP status, response body/header, `request_id`, replay marker, delegated-authority decision, provider-dispatch result, or confirmation of the expected `409` mismatch response. Those are intentionally left as live evidence rather than fabricated locally.
