"""Runtime configuration. Secrets are read only from the process environment."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRADE_AGENT_",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
    )

    database_url: str = "sqlite:///./data/trade_agent.db"
    auto_create_schema: bool = False
    host: str = "127.0.0.1"
    port: int = 8100
    dashboard_port: int = 8102
    dashboard_dist: str = "frontend/dist"
    dashboard_url: str = ""
    assisted_demo_accepted: bool = False
    max_quote_age_seconds: float = 15.0
    demo_daily_loss_limit: Decimal = Decimal("50")
    demo_drawdown_limit: Decimal = Decimal("300")
    telegram_bot_token: str | None = None
    telegram_owner_id: str | None = None
    http_api_token: str | None = None
    account_id: str = "demo-mt5"
    broker_mode: Literal["fake", "mt5_http"] = "fake"
    mt5_connector_url: str = "http://127.0.0.1:8101"
    mt5_connector_token: str | None = None
    trading_mode: Literal["demo", "live"] = "demo"
    timezone: str = "Asia/Kolkata"
    max_volume: Decimal = Decimal("1.00")
    max_open_positions: int = 5
    max_pending_orders: int = 5
    max_pending_submissions: int = 1
    max_total_volume: Decimal = Decimal("5.00")
    max_estimated_loss: Decimal = Decimal("500.00")
    min_free_margin_after_trade: Decimal = Decimal("500.00")
    max_spread_points: Decimal = Decimal("50")
    max_slippage_points: Decimal = Decimal("20")
    confirmation_ttl_seconds: int = 300
    monitor_interval_seconds: float = 5.0
    deadline_interval_seconds: float = 1.0
    allowed_symbols: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY"])
    ai_provider: Literal["none", "openai_compatible", "opencode", "codex"] = "none"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_protocol: Literal["chat_completions", "responses"] = "chat_completions"
    openai_response_format: Literal["json_schema", "json_object"] = "json_schema"
    openai_timeout_seconds: float = 30.0
    openai_reasoning_effort: Literal["low", "high", "max"] | None = None
    openai_max_completion_tokens: int | None = None
    opencode_url: str = "http://127.0.0.1:4096"
    opencode_model_provider: str | None = None
    opencode_model: str | None = None
    codex_command: str = "codex"
    codex_model: str | None = None

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip().upper() for item in value]
        raise ValueError("allowed_symbols must be a comma-delimited string or a list")

    def ensure_local_directories(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            location = self.database_url.removeprefix("sqlite:///")
            Path(location).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
