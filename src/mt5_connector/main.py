"""Connector process entry point, intended for native Windows Python or Wine."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import uvicorn

from mt5_connector.app import create_app
from mt5_connector.settings import ConnectorSettings


class _ConnectorFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [value for value in secrets if value]

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        return json.dumps(payload, separators=(",", ":"))


def main() -> None:
    settings = ConnectorSettings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(_ConnectorFormatter([settings.token, settings.password or ""]))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
