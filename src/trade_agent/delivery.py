"""Per-delivery identity; never derived from model output."""

from contextvars import ContextVar

current_update_id: ContextVar[str | None] = ContextVar("current_update_id", default=None)
