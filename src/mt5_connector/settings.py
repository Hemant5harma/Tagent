"""Connector-only process settings.

Secrets are read only in this process.  They are intentionally not exposed by
health or broker API responses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _optional_int(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _int(value: str | None, name: str, default: int) -> int:
    parsed = _optional_int(value, name)
    return default if parsed is None else parsed


@dataclass(frozen=True, slots=True)
class ConnectorSettings:
    token: str
    journal_path: Path
    host: str = "127.0.0.1"
    port: int = 8101
    terminal_path: str | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None
    timeout_ms: int = 60_000
    portable: bool = False
    magic: int = 2_026_092_201
    deviation_points: int = 20
    # Explicit, verified broker clock profile; never infer from a potentially stale tick.
    server_time_offset_seconds: int = 0
    server_time_valid_from: str | None = None
    server_time_valid_until: str | None = None

    @classmethod
    def from_env(cls) -> ConnectorSettings:
        token = os.environ.get("MT5_CONNECTOR_TOKEN", "")
        if not token.strip() or token == "change-me":
            raise RuntimeError("MT5_CONNECTOR_TOKEN must be set to a non-default secret")

        journal = Path(
            os.environ.get("MT5_CONNECTOR_JOURNAL_PATH", "./data/mt5_connector_requests.jsonl")
        ).expanduser()
        return cls(
            token=token,
            journal_path=journal,
            host=os.environ.get("MT5_CONNECTOR_HOST", "127.0.0.1"),
            port=_int(os.environ.get("MT5_CONNECTOR_PORT"), "MT5_CONNECTOR_PORT", 8101),
            terminal_path=os.environ.get("MT5_CONNECTOR_TERMINAL_PATH") or None,
            login=_optional_int(os.environ.get("MT5_CONNECTOR_LOGIN"), "MT5_CONNECTOR_LOGIN"),
            password=os.environ.get("MT5_CONNECTOR_PASSWORD") or None,
            server=os.environ.get("MT5_CONNECTOR_SERVER") or None,
            timeout_ms=_int(
                os.environ.get("MT5_CONNECTOR_TIMEOUT_MS"), "MT5_CONNECTOR_TIMEOUT_MS", 60_000
            ),
            portable=os.environ.get("MT5_CONNECTOR_PORTABLE", "false").lower()
            in {"1", "true", "yes"},
            magic=_int(os.environ.get("MT5_CONNECTOR_MAGIC"), "MT5_CONNECTOR_MAGIC", 2_026_092_201),
            deviation_points=_int(
                os.environ.get("MT5_CONNECTOR_DEVIATION_POINTS"),
                "MT5_CONNECTOR_DEVIATION_POINTS",
                20,
            ),
            server_time_offset_seconds=_int(
                os.environ.get("MT5_CONNECTOR_SERVER_TIME_OFFSET_SECONDS"),
                "MT5_CONNECTOR_SERVER_TIME_OFFSET_SECONDS",
                0,
            ),
            server_time_valid_from=os.environ.get("MT5_CONNECTOR_SERVER_TIME_VALID_FROM"),
            server_time_valid_until=os.environ.get("MT5_CONNECTOR_SERVER_TIME_VALID_UNTIL"),
        )
