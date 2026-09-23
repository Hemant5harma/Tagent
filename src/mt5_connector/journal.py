"""A small fsync-backed idempotency journal for order submission.

An unfinished ``started`` record is deliberately treated as unknown on restart.
That is safer than submitting a second order when a terminal result was lost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class JournalDecision:
    kind: Literal["new", "completed", "unknown"]
    result: dict[str, Any] | None = None


class IdempotencyConflict(ValueError):
    """The caller reused a key for a different order."""


class JSONLRequestJournal:
    """Append-only, single-process durable idempotency records."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        line_number = 0
        try:
            with self._path.open("r", encoding="utf-8") as journal:
                for line in journal:
                    line_number += 1
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    key = entry.get("key")
                    fingerprint = entry.get("fingerprint")
                    status = entry.get("status")
                    if (
                        not isinstance(key, str)
                        or not isinstance(fingerprint, str)
                        or status not in {"started", "completed"}
                    ):
                        raise ValueError("invalid record")
                    self._records[key] = entry
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"idempotency journal is invalid near line {line_number}") from exc

    def _append(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(self._path, flags, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def begin(self, key: str, fingerprint: str) -> JournalDecision:
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused for a different request")
                if existing["status"] == "completed":
                    result = existing.get("result")
                    if isinstance(result, dict):
                        return JournalDecision("completed", result)
                return JournalDecision("unknown")
            entry = {
                "key": key,
                "fingerprint": fingerprint,
                "status": "started",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._append(entry)
            self._records[key] = entry
            return JournalDecision("new")

    def complete(self, key: str, fingerprint: str, result: dict[str, Any]) -> None:
        with self._lock:
            existing = self._records.get(key)
            if existing is None or existing["fingerprint"] != fingerprint:
                raise RuntimeError("cannot complete an unknown idempotency key")
            entry = {
                "key": key,
                "fingerprint": fingerprint,
                "status": "completed",
                "result": result,
                "created_at": existing.get("created_at"),
                "completed_at": datetime.now(UTC).isoformat(),
            }
            self._append(entry)
            self._records[key] = entry

    def lookup(self, key: str) -> dict[str, Any] | None:
        """Return a copy of the latest durable record without exposing internal state."""
        with self._lock:
            record = self._records.get(key)
            return dict(record) if record is not None else None
