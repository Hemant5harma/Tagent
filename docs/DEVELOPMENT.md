# Development guide

The repository is deliberately split by responsibility, not by framework:

| Area | Files | Owns |
| --- | --- | --- |
| Domain contracts | `src/trade_agent/domain.py`, `risk.py` | Typed requests, broker-neutral concepts, and deterministic acceptance rules |
| Application workflow | `service.py`, `worker.py`, `workflow.py`, `app.py` | Drafts, confirmation, ledger transitions, deadlines, recovery, and channel-neutral operations |
| Infrastructure adapters | `db.py`, `broker.py`, `src/mt5_connector/` | Database persistence and isolated broker connectivity |
| User and AI interfaces | `api.py`, `telegram.py`, `ai.py` | HTTP, Telegram, and structured parsing only |
| Composition | `config.py`, `main.py` | Environment configuration and process lifecycle |

Dependency direction is inward: interfaces call application services; application
services use domain contracts and adapter protocols; adapters do not import an AI
provider or Telegram. The MT5 connector is a separate process and is the only
place that imports the `MetaTrader5` package.

## Adding a broker or venue

Implement `BrokerAdapter` in `broker.py`, including capability discovery,
preflight, idempotent submission, mutation, and reconciliation. Add contract
tests that run the same lifecycle suite as `FakeBroker`. Never put risk policy
inside an adapter, and never expose broker credentials to an AI provider.

## Adding a channel or model provider

Channels receive only `ApplicationFacade` operations. AI providers implement
`AIBackend.parse_trade` and return `TradeProposalParse`; they cannot import a
broker or `TradeService`. Convert the parse to `TradeInstruction`, then invoke
the existing validation and confirmation flow.

## Database changes

Update `db.py`, generate a reviewed Alembic revision, test upgrade and
downgrade on PostgreSQL and SQLite where supported, and keep runtime startup
from creating production schemas automatically. The execution ledger and
outbox are authoritative; LangGraph checkpoints are never execution records.

## Test expectations

Use `FakeBroker` for ordinary tests. Every change affecting order lifecycle
must cover duplicate confirmation, broker timeout/unknown outcome, restart
reconciliation, and a risk-reducing exit while entries are paused. Connected
MT5 tests belong to the deployment acceptance gate, never the default suite.
