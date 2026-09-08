"""Input parsing and normalisation for structured and simple text invoices."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import InvoiceRequest, to_jsonable


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier_name": ("supplier_name", "supplier", "vendor", "vendor_name"),
    "supplier_id": ("supplier_id", "vendor_id"),
    "invoice_number": ("invoice_number", "invoice_no", "invoice", "number"),
    "amount": ("amount", "total", "total_amount", "payment_amount"),
    "currency": ("currency", "currency_code"),
    "due_date": ("due_date", "payment_due", "date_due"),
    "bank_destination": ("bank_destination", "bank_details", "bank", "payment_destination"),
    "description": ("description", "reference", "memo", "payment_reference"),
    "requestor": ("requestor", "requester", "requested_by", "submitted_by"),
    "amount_minor": ("amount_minor", "amount_in_minor_units"),
}


@dataclass
class ParseResult:
    raw_input: Any
    normalized: dict[str, Any]
    source_format: str
    errors: list[str]


def _lookup(data: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in data.items()}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_amount(value: Any) -> tuple[Decimal | None, str | None, str | None]:
    """Return amount, embedded currency, and parse error."""

    if value is None or isinstance(value, bool):
        return None, None, None
    if isinstance(value, (int, float, Decimal)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return None, None, f"Amount {value!r} is not numeric."
        return amount, None, None

    text = str(value).strip()
    if not text:
        return None, None, "Amount is empty."

    embedded_currency: str | None = None
    match = re.fullmatch(
        r"(?:(?P<prefix>[A-Za-z]{3})\s*)?(?P<number>[+-]?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<suffix>[A-Za-z]{3})?",
        text,
    )
    if not match:
        return None, None, f"Amount {text!r} has an unsupported format."
    prefix = match.group("prefix")
    suffix = match.group("suffix")
    if prefix and suffix and prefix.upper() != suffix.upper():
        return None, None, "Amount contains conflicting currency prefixes."
    embedded_currency = (prefix or suffix or "").upper() or None
    number = match.group("number").replace(",", "")
    try:
        return Decimal(number), embedded_currency, None
    except InvalidOperation:
        return None, embedded_currency, f"Amount {text!r} is not numeric."


def _parse_bank_destination(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = {str(key).strip().lower(): item for key, item in value.items()}
        result: dict[str, str] = {}
        for canonical, aliases in {
            "account_name": ("account_name", "name"),
            "sort_code": ("sort_code", "sortcode"),
            "account_number": ("account_number", "account"),
            "iban": ("iban",),
            "routing_number": ("routing_number", "routing"),
        }.items():
            for alias in aliases:
                if alias in raw and _text_value(raw[alias]) is not None:
                    result[canonical] = _text_value(raw[alias]) or ""
                    break
        return result or None

    text = str(value).strip()
    if not text:
        return None
    result: dict[str, str] = {}
    for part in re.split(r"[;,]", text):
        if "=" not in part:
            continue
        key, item = part.split("=", 1)
        key = key.strip().lower().replace(" ", "_")
        item = item.strip()
        alias_map = {
            "name": "account_name",
            "account_name": "account_name",
            "sortcode": "sort_code",
            "sort_code": "sort_code",
            "account": "account_number",
            "account_number": "account_number",
            "iban": "iban",
            "routing": "routing_number",
            "routing_number": "routing_number",
        }
        if key in alias_map and item:
            result[alias_map[key]] = item
    return result or None


def _parse_text_invoice(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    key_map = {
        "supplier": "supplier_name",
        "supplier name": "supplier_name",
        "supplier id": "supplier_id",
        "invoice": "invoice_number",
        "invoice number": "invoice_number",
        "invoice no": "invoice_number",
        "amount": "amount",
        "currency": "currency",
        "due date": "due_date",
        "bank": "bank_destination",
        "bank details": "bank_destination",
        "description": "description",
        "reference": "description",
        "requestor": "requestor",
        "requester": "requestor",
    }
    for line in text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        canonical = key_map.get(key.strip().lower())
        if canonical:
            data[canonical] = value.strip()
    return data


def parse_request(payload: Any) -> ParseResult:
    """Parse a dict, JSON string, JSON path, or simple ``Field: value`` text."""

    errors: list[str] = []
    source_format = "structured_json"
    data: dict[str, Any]

    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, Path):
        try:
            text = payload.read_text(encoding="utf-8")
        except OSError as exc:
            return ParseResult(payload, {}, "file", [f"Could not read input file: {exc}"])
        return parse_request(text)
    elif isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ParseResult(payload, {}, "text", ["Input is empty."])
        if text.startswith("{"):
            source_format = "structured_json_text"
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                return ParseResult(payload, {}, source_format, [f"Invalid JSON: {exc.msg}."])
            if not isinstance(decoded, dict):
                return ParseResult(payload, {}, source_format, ["JSON input must be an object."])
            data = decoded
        else:
            source_format = "simple_text"
            data = _parse_text_invoice(text)
    else:
        return ParseResult(payload, {}, "unknown", ["Input must be a JSON object or text invoice."])

    normalized: dict[str, Any] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        normalized[canonical] = _lookup(data, aliases)

    amount, embedded_currency, amount_error = _parse_amount(normalized.get("amount"))
    if amount_error:
        errors.append(amount_error)
    normalized["amount"] = amount
    if not normalized.get("currency") and embedded_currency:
        normalized["currency"] = embedded_currency
    if normalized.get("currency") is not None:
        normalized["currency"] = str(normalized["currency"]).strip().upper()
    normalized["amount_minor"] = normalized.get("amount_minor")
    if normalized["amount_minor"] is not None:
        try:
            normalized["amount_minor"] = int(normalized["amount_minor"])
        except (TypeError, ValueError):
            errors.append("amount_minor must be an integer when supplied.")
            normalized["amount_minor"] = None

    for field_name in ("supplier_name", "supplier_id", "invoice_number", "due_date", "description", "requestor"):
        normalized[field_name] = _text_value(normalized.get(field_name))
    normalized["bank_destination"] = _parse_bank_destination(normalized.get("bank_destination"))
    normalized["raw_amount"] = to_jsonable(_lookup(data, FIELD_ALIASES["amount"]))

    return ParseResult(to_jsonable(payload), normalized, source_format, errors)


def invoice_from_parse(result: ParseResult) -> InvoiceRequest:
    """Build a domain object even when some fields are missing for auditability."""

    amount = result.normalized.get("amount")
    return InvoiceRequest(
        supplier_name=result.normalized.get("supplier_name"),
        supplier_id=result.normalized.get("supplier_id"),
        invoice_number=result.normalized.get("invoice_number"),
        amount=amount if isinstance(amount, Decimal) else None,
        currency=result.normalized.get("currency"),
        due_date=result.normalized.get("due_date"),
        bank_destination=result.normalized.get("bank_destination"),
        description=result.normalized.get("description"),
        requestor=result.normalized.get("requestor"),
        amount_minor=result.normalized.get("amount_minor"),
        raw_amount=result.normalized.get("raw_amount"),
    )
