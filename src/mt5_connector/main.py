"""Connector process entry point, intended for native Windows Python or Wine."""

from __future__ import annotations

import json
import logging
import os
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
    settings.journal_path.parent.mkdir(parents=True, exist_ok=True)
    process_lock = open(str(settings.journal_path) + ".lock", "a+b")
    process_lock.write(b"0")
    process_lock.flush()
    process_lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(process_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # Linux-only branch
    except OSError as exc:
        process_lock.close()
        raise RuntimeError("Another connector owns this execution journal") from exc
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
