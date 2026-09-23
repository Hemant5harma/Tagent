# Trade Agent

Trade Agent is a human-confirmed trading automation backend. You provide the trade
parameters, the service validates them against broker capabilities and deterministic
risk limits, then asks for one confirmation before it submits an order. Monitoring,
stop loss/take profit, order status, and an optional deadline continue without an AI
provider.

The project has two processes:

- `trade-agent`: native Python backend, Telegram interface, durable ledger, workers,
  provider adapters, and broker abstraction.
- `trade-agent-connector`: a separate Windows-Python/MT5 process. On Ubuntu it runs
  inside Wine. It is never exposed publicly.

## Quick local start (safe fake broker)

1. Copy `.env.example` to `.env` and leave `TRADE_AGENT_BROKER_MODE=fake`.
2. Install the locked dependencies with `uv sync --locked --extra dev`.
3. For a development SQLite database, leave `TRADE_AGENT_AUTO_CREATE_SCHEMA=true`;
   for PostgreSQL, run `uv run alembic upgrade head` and omit that setting.
4. Start `uv run trade-agent`.
5. Use the HTTP API at `http://127.0.0.1:8100/docs` with the configured
   `X-Trade-Agent-Token`, or configure Telegram.

Run the complete fake-broker and connector contract suite with `uv run pytest`.
The repository CI runs that suite on both Windows and Linux. These tests do not
replace the connected MT5/Wine, market-session, backup restoration, and reboot
acceptance gates in the operations runbook.

The fake broker is only for automated tests and local demonstrations. It cannot send
orders to a broker.

## Safety model

- A Telegram owner id is required before Telegram trading commands are enabled.
- A proposal has an immutable version, expires after five minutes, and must be
  confirmed exactly once.
- Entry, SL, TP, volume, aggregate exposure, estimated stop-loss amount, symbol,
  price tick, margin, spread, and account mode are checked before every submission.
- SL/TP changes use a separate expiring confirmation and durable mutation intent.
- New entries can be paused. Cancelling pending orders and closing positions remain
  available while paused.
- The broker receives SL/TP with the order. A deadline worker cancels an unfilled
  order or closes the remaining position at the confirmed deadline.

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for the Windows and Ubuntu deployment
runbooks, provider readiness checks, and connected-demo acceptance gates; see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the boundaries, and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for contributor rules.
