"""Small, redaction-safe adapter for the Axiom staging invoke endpoint."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AXIOM_STAGING_URL = "https://api-staging.axiomgo.ai/v1/invoke"
AXIOM_AGENT_PASSPORT_ENV = "AXIOM_AGENT_PASSPORT"
AXIOM_ACTION = "payment.create"
REDACTED = "[REDACTED]"

FORBIDDEN_BODY_FIELDS = frozenset(
    {
        "request_id",
        "trace_id",
        "correlation_id",
        "client_request_id",
        "agent_job_id",
    }
)

_REQUEST_BODY_FIELDS = frozenset({"action", "params", "passport"})
_PAYMENT_PARAM_FIELDS = frozenset({"amount_minor", "currency", "merchant_id", "merchant_ref"})
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

_USEFUL_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "date",
        "request-id",
        "x-request-id",
        "x-axiom-request-id",
        "replay",
        "x-replayed",
        "x-idempotent-replayed",
        "idempotency-key",
        "retry-after",
        "server",
        "location",
    }
)

_REPLAY_KEYS = (
    "replayed",
    "replay",
    "idempotent_replay",
    "idempotency_replayed",
    "x-idempotent-replayed",
)
_REQUEST_ID_KEYS = ("request_id", "requestId")


class StagingAdapterError(RuntimeError):
    """Base class for safe, user-facing staging adapter failures."""


class PassportMissingError(StagingAdapterError):
    """Raised when the short-lived runtime passport was not supplied."""


class StagingTransportError(StagingAdapterError):
    """Raised when a staging request could not be sent."""


class RequestShapeError(StagingAdapterError):
    """Raised when a payment proposal cannot be represented safely."""


@dataclass(frozen=True)
class PaymentCreateParams:
    """The four non-secret parameters accepted by ``payment.create``."""

    amount_minor: int
    currency: str
    merchant_id: str
    merchant_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "merchant_id": self.merchant_id,
            "merchant_ref": self.merchant_ref,
        }


@dataclass(frozen=True)
class HttpResponse:
    """Transport-neutral response data used by the adapter and its tests."""

    status_code: int
    headers: Mapping[str, Any]
    body: bytes


class StagingTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> HttpResponse:
        """Send one POST and return its response without applying policy."""


@dataclass(frozen=True)
class StagingCallResult:
    """Safe evidence from one invocation; the runtime passport is never a field."""

    status_code: int | None
    response_body: Any
    response_headers: dict[str, Any]
    axiom_request_id: str | None
    replayed: bool | str | None
    redacted_request_body: dict[str, Any]
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "http_status": self.status_code,
            "response_body": self.response_body,
            "response_headers": self.response_headers,
            "axiom_request_id": self.axiom_request_id,
            "replay_indication": self.replayed,
            "redacted_request_body": self.redacted_request_body,
            "idempotency_key": self.idempotency_key,
        }


def _contains_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in ("passport", "authorization", "token", "secret"))


def redact_secrets(value: Any, secret: str | None = None) -> Any:
    """Recursively redact secret-shaped fields and an exact runtime secret value."""

    if isinstance(value, Mapping):
        safe_mapping: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe_key = redact_secrets(key_text, secret)
            safe_mapping[str(safe_key)] = REDACTED if _contains_sensitive_key(key_text) else redact_secrets(item, secret)
        return safe_mapping
    if isinstance(value, list):
        return [redact_secrets(item, secret) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, REDACTED)
    return value


def _coerce_params(params: PaymentCreateParams | Mapping[str, Any]) -> PaymentCreateParams:
    if isinstance(params, PaymentCreateParams):
        candidate = params.to_dict()
    elif isinstance(params, Mapping):
        candidate = {str(key): value for key, value in params.items()}
    else:
        raise RequestShapeError("payment.create params must be a mapping.")

    unexpected = set(candidate) - _PAYMENT_PARAM_FIELDS
    missing = _PAYMENT_PARAM_FIELDS - set(candidate)
    if unexpected or missing:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if unexpected:
            details.append(f"unexpected fields: {', '.join(sorted(unexpected))}")
        raise RequestShapeError("Invalid payment.create params (" + "; ".join(details) + ").")

    amount_minor = candidate["amount_minor"]
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise RequestShapeError("amount_minor must be a positive integer.")

    values: dict[str, Any] = {}
    for field_name in ("currency", "merchant_id", "merchant_ref"):
        value = candidate[field_name]
        if not isinstance(value, str) or not value.strip():
            raise RequestShapeError(f"{field_name} must be a non-empty string.")
        values[field_name] = value

    return PaymentCreateParams(amount_minor=amount_minor, **values)


def generate_idempotency_key(agent_job_id: str, attempt_number: int) -> str:
    """Create the exact key format agreed for the staging trial."""

    if not isinstance(agent_job_id, str) or not _JOB_ID_PATTERN.fullmatch(agent_job_id):
        raise RequestShapeError("agent_job_id must be a short identifier without whitespace.")
    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number <= 0:
        raise RequestShapeError("attempt_number must be a positive integer.")
    return f"henry-ap-{agent_job_id}-{attempt_number}"


def build_request_body(params: PaymentCreateParams | Mapping[str, Any], passport: str) -> dict[str, Any]:
    """Build the private wire body; callers must not persist or print its result."""

    payment_params = _coerce_params(params)
    if not isinstance(passport, str) or not passport:
        raise RequestShapeError("passport must be a non-empty runtime value.")

    body = {
        "action": AXIOM_ACTION,
        "params": payment_params.to_dict(),
        "passport": passport,
    }
    if set(body) != _REQUEST_BODY_FIELDS or FORBIDDEN_BODY_FIELDS.intersection(body):
        raise RequestShapeError("Generated request body does not match the staging contract.")
    if FORBIDDEN_BODY_FIELDS.intersection(body["params"]):
        raise RequestShapeError("Generated request params contain a forbidden field.")
    return body


def redacted_request_body(params: PaymentCreateParams | Mapping[str, Any]) -> dict[str, Any]:
    """Return the evidence-safe form of the exact wire shape."""

    payment_params = _coerce_params(params)
    return {
        "action": AXIOM_ACTION,
        "params": payment_params.to_dict(),
        "passport": REDACTED,
    }


def _find_nested_value(value: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        lowered_keys = {str(key).lower(): key for key in value}
        for wanted in keys:
            actual_key = lowered_keys.get(wanted.lower())
            if actual_key is not None:
                return value[actual_key]
        for item in value.values():
            found = _find_nested_value(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_nested_value(item, keys)
            if found is not None:
                return found
    return None


def parse_response_body(raw_body: bytes | str | None, secret: str | None = None) -> Any:
    """Parse and redact JSON or text returned by staging."""

    if raw_body is None:
        return None
    if isinstance(raw_body, bytes):
        text = raw_body.decode("utf-8", errors="replace")
    else:
        text = str(raw_body)
    if not text:
        return None
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        parsed = text
    return redact_secrets(parsed, secret)


def safe_response_headers(headers: Mapping[str, Any], secret: str | None = None) -> dict[str, Any]:
    """Keep useful response metadata while excluding request credentials."""

    safe: dict[str, Any] = {}
    for key, value in headers.items():
        key_text = str(key)
        if key_text.lower() in _USEFUL_RESPONSE_HEADERS:
            safe[key_text] = redact_secrets(value, secret)
    return safe


def extract_axiom_request_id(
    body: Any,
    headers: Mapping[str, Any],
    secret: str | None = None,
) -> str | None:
    """Extract only an Axiom request identifier, never a local job identifier."""

    body_value = _find_nested_value(body, _REQUEST_ID_KEYS)
    if body_value is not None and not isinstance(body_value, (dict, list)):
        return redact_secrets(str(body_value), secret)
    for key, value in headers.items():
        if str(key).lower() in {"request-id", "x-request-id", "x-axiom-request-id"}:
            return redact_secrets(str(value), secret)
    return None


def _normalise_replay_value(value: Any) -> bool | str | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value
    if value is None:
        return None
    return str(value)


def extract_replay_indication(body: Any, headers: Mapping[str, Any]) -> bool | str | None:
    """Capture an explicit replay signal when Axiom returns one."""

    value = _find_nested_value(body, _REPLAY_KEYS)
    if value is not None:
        return _normalise_replay_value(value)
    for key, header_value in headers.items():
        if str(key).lower() in {"replay", "x-replayed", "x-idempotent-replayed"}:
            return _normalise_replay_value(header_value)
    return None


class UrllibTransport:
    """Minimal standard-library HTTP transport for the staging endpoint."""

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=int(response.status),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as error:
            response_headers = dict(error.headers.items()) if error.headers is not None else {}
            try:
                response_body = error.read()
            except Exception:
                response_body = b""
            return HttpResponse(status_code=int(error.code), headers=response_headers, body=response_body)
        except (URLError, TimeoutError, OSError):
            raise StagingTransportError("Axiom staging request failed.") from None


class AxiomStagingAdapter:
    """Invoke ``payment.create`` without duplicating Axiom's policy decisions."""

    def __init__(
        self,
        endpoint: str = AXIOM_STAGING_URL,
        transport: StagingTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint = endpoint
        self.transport = transport or UrllibTransport()
        self.timeout = timeout

    def preview(
        self,
        params: PaymentCreateParams | Mapping[str, Any],
        agent_job_id: str,
        attempt_number: int = 1,
    ) -> StagingCallResult:
        """Validate and render a safe preview without reading the passport or using a network."""

        payment_params = _coerce_params(params)
        key = generate_idempotency_key(agent_job_id, attempt_number)
        return StagingCallResult(
            status_code=None,
            response_body=None,
            response_headers={},
            axiom_request_id=None,
            replayed=None,
            redacted_request_body=redacted_request_body(payment_params),
            idempotency_key=key,
        )

    def invoke(
        self,
        params: PaymentCreateParams | Mapping[str, Any],
        agent_job_id: str,
        attempt_number: int = 1,
    ) -> StagingCallResult:
        """Send one staging request and return only redacted evidence."""

        payment_params = _coerce_params(params)

        passport = os.environ.get(AXIOM_AGENT_PASSPORT_ENV)
        if not isinstance(passport, str) or not passport.strip():
            raise PassportMissingError(f"{AXIOM_AGENT_PASSPORT_ENV} is not set.")
        if isinstance(agent_job_id, str) and passport in agent_job_id:
            raise RequestShapeError("agent_job_id must not contain the runtime passport.")

        key = generate_idempotency_key(agent_job_id, attempt_number)
        redacted_body = redact_secrets(redacted_request_body(payment_params), secret=passport)

        wire_body = build_request_body(payment_params, passport)
        encoded_body = json.dumps(wire_body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": key,
        }
        try:
            response = self.transport.post(
                self.endpoint,
                headers=request_headers,
                body=encoded_body,
                timeout=self.timeout,
            )
        except Exception:
            # Do not let an arbitrary transport exception echo the in-memory body.
            raise StagingTransportError("Axiom staging request failed.") from None

        parsed_body = parse_response_body(response.body, secret=passport)
        response_headers = safe_response_headers(response.headers, secret=passport)
        return StagingCallResult(
            status_code=response.status_code,
            response_body=parsed_body,
            response_headers=response_headers,
            axiom_request_id=extract_axiom_request_id(parsed_body, response_headers, secret=passport),
            replayed=extract_replay_indication(parsed_body, response_headers),
            redacted_request_body=redacted_body,
            idempotency_key=key,
        )


__all__ = [
    "AXIOM_ACTION",
    "AXIOM_AGENT_PASSPORT_ENV",
    "AXIOM_STAGING_URL",
    "FORBIDDEN_BODY_FIELDS",
    "HttpResponse",
    "PaymentCreateParams",
    "AxiomStagingAdapter",
    "PassportMissingError",
    "RequestShapeError",
    "StagingAdapterError",
    "StagingCallResult",
    "StagingTransportError",
    "build_request_body",
    "extract_axiom_request_id",
    "extract_replay_indication",
    "generate_idempotency_key",
    "parse_response_body",
    "redact_secrets",
    "redacted_request_body",
    "safe_response_headers",
]
