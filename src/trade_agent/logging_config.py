"""Structured process logging with deployment-secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from trade_agent.config import Settings


class JsonRedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [value for value in secrets if value and value != "change-me"]

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    secrets = [
        settings.telegram_bot_token or "",
        settings.http_api_token or "",
        settings.mt5_connector_token or "",
        settings.openai_api_key or "",
    ]
    handler = logging.StreamHandler()
    handler.setFormatter(JsonRedactingFormatter(secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
