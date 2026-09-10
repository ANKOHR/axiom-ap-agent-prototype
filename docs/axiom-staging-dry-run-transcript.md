# Axiom staging dry-run transcript

Status: **GENERATED LOCALLY**. This is a credential-free preview, not a call to Axiom and not evidence of any Axiom response.

Environment: `AXIOM_AGENT_PASSPORT` unset.

```text
PS> python -m src.main --staging-suite --dry-run --staging-report audit\review-staging-dry-run.json
Axiom staging suite (dry_run)
  allowed payment: AP=APPROVE_FOR_SUBMISSION HTTP=NOT_SENT Axiom_request_id=- replay=-
  blocked merchant: AP=APPROVE_FOR_SUBMISSION HTTP=NOT_SENT Axiom_request_id=- replay=-
  blocked amount: AP=APPROVE_FOR_SUBMISSION HTTP=NOT_SENT Axiom_request_id=- replay=-
  idempotent replay: AP=APPROVE_FOR_SUBMISSION HTTP=NOT_SENT Axiom_request_id=- replay=-
  idempotency mismatch: AP=APPROVE_FOR_SUBMISSION HTTP=NOT_SENT Axiom_request_id=- replay=-
Evidence report: audit\review-staging-dry-run.json
No network request was made and AXIOM_AGENT_PASSPORT was not read.
```

The generated report contains five scenario records with the exact redacted request shape (`passport: [REDACTED]`), `HTTP=NOT_SENT`, null response/request-ID/replay fields, and `safety.staging_requests_sent: false`. It contains no Axiom response, status, request ID, replay result, or live outcome.
