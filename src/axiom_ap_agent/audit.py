"""Append-only structured JSONL audit output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import to_jsonable


class JsonlAuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        safe_record = to_jsonable(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_record, sort_keys=True) + "\n")
        return safe_record

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
