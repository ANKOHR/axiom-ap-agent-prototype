from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.axiom_ap_agent.axiom_staging import (
    AXIOM_STAGING_URL,
    FORBIDDEN_BODY_FIELDS,
    REDACTED,
    AxiomStagingAdapter,
    HttpResponse,
    PassportMissingError,
    RequestShapeError,
    StagingTransportError,
    build_request_body,
    generate_idempotency_key,
)


def _runtime_secret() -> str:
    return f"runtime-only-{uuid4().hex}"


def _assert_equal_without_echo(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise AssertionError(message)


def _assert_not_present(value: str, secret: str, message: str) -> None:
    if secret in value:
        raise AssertionError(message)


class RecordingTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        return self.response


def _params() -> dict[str, object]:
    return {
        "amount_minor": 2500,
        "currency": "GBP",
        "merchant_id": "merchant.acme-supplies.test",
        "merchant_ref": "inv-allowed-001",
    }


def test_staging_request_shape_headers_key_and_request_id_mapping(monkeypatch) -> None:
    secret = _runtime_secret()
    monkeypatch.setenv("AXIOM_AGENT_PASSPORT", secret)
    transport = RecordingTransport(
        HttpResponse(
            status_code=200,
            headers={"Content-Type": "application/json", "X-Request-Id": "axiom-req-001"},
            body=b'{"status":"accepted","request_id":"axiom-req-001","provider_dispatch":"test-mode"}',
        )
    )
    adapter = AxiomStagingAdapter(transport=transport)

    result = adapter.invoke(_params(), agent_job_id="job-allowed", attempt_number=1)
    sent = transport.calls[0]
    wire_body = json.loads(sent["body"])

    _assert_equal_without_echo(sent["url"], AXIOM_STAGING_URL, "the staging endpoint was not used")
    _assert_equal_without_echo(
        set(wire_body), {"action", "params", "passport"}, "the top-level request shape changed"
    )
    _assert_equal_without_echo(wire_body["action"], "payment.create", "the action changed")
    _assert_equal_without_echo(wire_body["params"], _params(), "the payment params changed")
    _assert_equal_without_echo(wire_body["passport"], secret, "the runtime passport was not sent as supplied")
    _assert_equal_without_echo(
        sent["headers"],
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": "henry-ap-job-allowed-1",
        },
        "the required request headers were not used",
    )
    _assert_equal_without_echo(result.status_code, 200, "the HTTP status was not preserved")
    _assert_equal_without_echo(result.axiom_request_id, "axiom-req-001", "Axiom request_id was not mapped")
    _assert_equal_without_echo(result.redacted_request_body["passport"], REDACTED, "passport was not redacted")

    serialised_evidence = json.dumps(result.to_dict(), sort_keys=True)
    _assert_not_present(serialised_evidence, secret, "the runtime passport leaked into adapter evidence")


def test_forbidden_fields_are_absent_from_generated_body() -> None:
    body = build_request_body(_params(), passport=_runtime_secret())
    body_json = json.dumps(body, sort_keys=True)
    assert not FORBIDDEN_BODY_FIELDS.intersection(body)
    assert not FORBIDDEN_BODY_FIELDS.intersection(body["params"])
    for forbidden_field in FORBIDDEN_BODY_FIELDS:
        assert forbidden_field not in body_json


def test_idempotency_key_generation() -> None:
    assert generate_idempotency_key("job-abc", 2) == "henry-ap-job-abc-2"


def test_preview_does_not_read_passport_or_use_transport(monkeypatch) -> None:
    monkeypatch.delenv("AXIOM_AGENT_PASSPORT", raising=False)
    transport = RecordingTransport(HttpResponse(200, {}, b"{}"))
    adapter = AxiomStagingAdapter(transport=transport)

    result = adapter.preview(_params(), agent_job_id="job-preview", attempt_number=1)

    assert result.status_code is None
    assert result.redacted_request_body["passport"] == REDACTED
    assert transport.calls == []


def test_missing_passport_fails_without_sending(monkeypatch) -> None:
    monkeypatch.delenv("AXIOM_AGENT_PASSPORT", raising=False)
    transport = RecordingTransport(HttpResponse(200, {}, b"{}"))
    adapter = AxiomStagingAdapter(transport=transport)

    with pytest.raises(PassportMissingError) as raised:
        adapter.invoke(_params(), agent_job_id="job-missing", attempt_number=1)

    assert "AXIOM_AGENT_PASSPORT" in str(raised.value)
    assert transport.calls == []


def test_runtime_passport_cannot_become_part_of_local_job_key(monkeypatch) -> None:
    secret = _runtime_secret()
    monkeypatch.setenv("AXIOM_AGENT_PASSPORT", secret)
    transport = RecordingTransport(HttpResponse(200, {}, b"{}"))

    with pytest.raises(RequestShapeError) as raised:
        AxiomStagingAdapter(transport=transport).invoke(
            _params(), agent_job_id=secret, attempt_number=1
        )

    _assert_not_present(str(raised.value), secret, "the runtime passport leaked into a job-key error")
    assert transport.calls == []


def test_response_and_headers_redact_secret_echoes(monkeypatch) -> None:
    secret = _runtime_secret()
    monkeypatch.setenv("AXIOM_AGENT_PASSPORT", secret)
    response = HttpResponse(
        status_code=200,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
            "X-Request-Id": secret,
        },
        body=json.dumps(
            {
                "request_id": "axiom-req-redaction",
                "passport": secret,
                "debug_echo": f"received {secret}",
            }
        ).encode(),
    )
    result = AxiomStagingAdapter(transport=RecordingTransport(response)).invoke(
        _params(), agent_job_id="job-redaction", attempt_number=1
    )

    serialised_evidence = json.dumps(result.to_dict(), sort_keys=True)
    _assert_not_present(serialised_evidence, secret, "the runtime passport leaked into redacted evidence")
    assert result.response_body["passport"] == REDACTED
    assert result.response_body["debug_echo"] == f"received {REDACTED}"
    assert "Authorization" not in result.response_headers
    assert result.response_headers["X-Request-Id"] == REDACTED


def test_transport_exception_cannot_echo_secret(monkeypatch) -> None:
    secret = _runtime_secret()
    monkeypatch.setenv("AXIOM_AGENT_PASSPORT", secret)

    class ExplodingTransport:
        def post(self, url, headers, body, timeout):
            raise RuntimeError(secret)

    with pytest.raises(StagingTransportError) as raised:
        AxiomStagingAdapter(transport=ExplodingTransport()).invoke(
            _params(), agent_job_id="job-error", attempt_number=1
        )

    _assert_not_present(str(raised.value), secret, "the runtime passport leaked into an exception")
    assert raised.value.__cause__ is None
