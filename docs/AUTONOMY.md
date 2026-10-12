# Multi-agent demo testing

The existing manual trade workflow remains available. Autonomous trades have a
separate origin, decision journal and performance filter. Automation starts off.
There is no promise of profitable or perfect performance.

## What is implemented

Closed broker candles and numerical EMA20/EMA50, RSI and ATR feed five research
roles: technical analyst, macro analyst, bull, bear and judge. The research uses
your selected OpenAI-compatible, OpenCode or Codex profile. No Ollama is needed.
TradingAgents bull/bear prompts and debate flow are adapted from pinned upstream
source retained with its Apache license in `third_party/TradingAgents`.
This is an integration into our execution ledger, not a replacement trading app.

The current registered strategy is `tradingagents-mt5-v4`: buy requires EMA20 above
EMA50 and RSI 50–75; sell requires EMA20 below EMA50 and RSI 25–50. The judge can
abstain. Confidence is a model score, not a calibrated probability of profit.
Code calculates bounded lots, ATR-based SL/TP and a holding deadline. Models
cannot submit orders, change risk caps or modify existing manual exposure.

Research revision v2 calculates candle counts, window extrema, candle ranges,
EURUSD pip distances, price/EMA relationships and recent local pivot lows in
code. Reports display these verified facts; AI commentary stays qualitative.
Numerical prose, unsupported timeframe assertions and selected unverified pivot
claims receive one correction attempt, then cause a factual-validation WAIT.
Rejected reports are not passed into the debate. Position P/L is excluded from
model context. These guards reduce factual errors; they do not prove that an AI
interpretation or directional prediction is correct. News remains headline-only,
with no complete event calendar or consensus/surprise data. Historical v1 runs
remain in the journal; inspect the strategy revision and run timestamp.

Revision v3 adds optional `TRADE_AGENT_AUTONOMY_TRADE_PLANS_ENABLED=true`.
Each run archives closed H1/H4 candles alongside its entry timeframe. Plans require
aligned price/EMA context, registered RSI, and a closed-bar breakout or pullback
reclaim. Missing, future or stale context prevents readiness. Stops use confirmed
local swings/recent range, ATR and broker minimum distances. Targets respect the
nearest opposing pivot; otherwise they are explicitly labelled reward/risk
projections. Spread, adverse slippage, tick rounding, maximum stop distance,
minimum volume and risk caps are checked. Commissions, swaps and gaps are not
modeled. A setup labelled ready is not an order or proof of profitability.

Plans are shown even when AI abstains and expire at the next entry-timeframe bar.
When planning is enabled, missing/invalid plans now fail before model research;
plans with no ready side record a code-owned WAIT and their blockers without
paying for five AI calls. Only a ready primary-timeframe plan reaches research.
This does not relax setup rules or demonstrate profitability. The separate
[strategy research gate](../specs/010-evidence-based-strategy-research/spec.md)
defines the broker-history, cost, holdout and forward-shadow evidence needed
before any autonomous demo promotion.
The execution path rechecks the quote, expiry, entry drift, stop distances and
reward/risk before sizing. AI approval, confidence threshold, grant and all
existing risk controls remain required. Changing the planning flag invalidates
an existing grant.

Revision v4 adds `TRADE_AGENT_AUTONOMY_COMPARISON_ENABLED=true` for shadow testing.
It compares M5 (600 candles), M15 (200) and M30 (100), with H1 trend context:
50 hours of candles for each entry timeframe, excluding session gaps. Each frame
shows directional entry, SL, TP, risk and rejection reasons. Comparisons update
on the primary M15 research cycle, not on every M5 candle. The primary M15
decision remains separate; a ready comparison does not authorize an order.
Demo execution is blocked while comparison is enabled. Jarvis keeps shadow mode,
paused entries and no execution grant. Disable comparison before any separately
authorized demo execution evaluation.

During market closure, a separately labelled historical preview can display
archived-cutoff plans. It is not a fresh signal, AI decision or profitability
backtest. Fresh quotes are still required for scheduled research.

The model sees full-window computed facts and an explicitly labelled recent
24-candle slice; full candles remain archived. Transient timeouts, JSON/schema
errors and factual guard failures get one bounded retry per role. Per-role
attempt counts, duration and completion status are saved in `research_health`.

Unavailable market data is a per-symbol health observation, not a new WAIT
decision. Repeated checks update that observation and preserve its outage start;
fresh data clears it automatically. No AI or order path runs without fresh data.
The website and `/autonomy` show last quote/check times and explicit shadow,
pause and grant state. Older missing-data ledger rows remain archived but are
excluded from the recent research journal. Failed research is marked separately
from an intentional market WAIT, and historical plans are labelled expired.

Every closed bar is claimed once per account/strategy/symbol/timeframe. A shadow
run consumes that bar too: switching to demo waits for the next bar. Interrupted,
blocked and unknown runs are not automatically retried. Inspect the authoritative
proposal/intent ledger when reconciling an uncertain submission.

## Prepare these items

1. A broker **USD demo account**, logged into the MT5 terminal, with automated
   trading allowed. Use the exact broker symbol names in the allowlist. Load at
   least 200 M15 candles for each symbol in the terminal. Chart history can be
   limited and market closures can cause a stale-data WAIT.
2. The existing connector URL/token and account identifier. Keep connector and
   broker secrets separate from the model process. Do not use live mode.
3. PostgreSQL for connected execution and a database backup before migration.
   SQLite/fake broker is only for development. Existing database schema needs
   `alembic upgrade head`; `create_all` does not upgrade existing tables.
4. Your existing working AI profile and its authentication. Select
   `TRADE_AGENT_AI_PROVIDER=openai_compatible`, `opencode` or `codex`, and keep
   its existing model/provider settings. `none` produces WAIT. Each research run
   can make five model calls; your provider's quotas and costs still apply.
5. Configured `TRADE_AGENT_TELEGRAM_OWNER_ID` and a strong HTTP API token, plus
   the existing independent protection/reconciliation worker. Connected entries
   still require accepted demo deployment, fresh quotes, synchronized broker
   history and passing daily loss/drawdown limits.

## Install and start shadow testing

From the project root, using the project Python environment:

```powershell
uv sync --locked --extra dev --extra postgres
uv run alembic upgrade head
```

Keep your current environment and AI profile. Add settings from `.env.example`:

```dotenv
TRADE_AGENT_AUTONOMY_MODE=shadow
TRADE_AGENT_AUTONOMY_TIMEFRAME=M15
TRADE_AGENT_AUTONOMY_BARS=200
TRADE_AGENT_AUTONOMY_RISK_AMOUNT=5
TRADE_AGENT_AUTONOMY_MAX_VOLUME=0.10
TRADE_AGENT_AUTONOMY_MIN_CONFIDENCE=0.70
TRADE_AGENT_AUTONOMY_CONTEXT_ENABLED=false
```

Run the existing connector first for MT5; then use separate terminals/services:

```powershell
uv run python -m trade_agent.main
uv run python -m trade_agent.worker
uv run python -m trade_agent.autonomy
```

For current dashboard and Telegram performance snapshots, also keep the existing
reporting service running: `uv run python -m trade_agent.reporting`. Protection,
reporting and research have separate responsibilities.

On Jarvis, `deploy/systemd/trade-agent-autonomy.service` is a service template for
the existing `/home/jarvis/trade-agent` installation. Verify those paths, preserve
the protected existing environment, migrate the ledger, and deploy the updated
backend plus read-only dashboard source/assets. Begin with shadow mode. Installing
the template alone does not authorize demo orders.

## Monitor through Telegram and the website

The private owner-only Telegram bot supports:

- `/autonomy`: API configuration, observed worker heartbeat, strategy, symbols,
  pause status and current grant expiry.
- `/performance all`: compare manual and autonomous realized results for the last
  seven UTC days, with open position counts and broker/history freshness.
- `/performance manual` or `/performance autonomous`: one decision origin.
- `/decisions`: five latest autonomous runs, including WAIT and blocked reasons.
- `/positions`, `/orders`, `/account`, `/status`: existing account monitoring.
- `/dashboard`: existing private website login link/code.

Website Overview compares both origins side by side and retains scope filters.
Agent displays reports/evidence; System displays autonomy worker state and its
observed mode/phase. A heartbeat is written every ten seconds independently of
model calls and becomes stale after 35 seconds. No heartbeat means unverified,
not running. A fresh heartbeat alone does not prove broker or model readiness.
Graceful shutdown records stopped; abrupt shutdown eventually shows stale.

Demo submission/uncertainty and blocked research notifications use the existing
durable Telegram outbox. The protection worker delivers that outbox when its bot
token is configured. Routine shadow and WAIT results remain in the journal.

The protection worker must keep running independently of research. Dashboard
deployment uses process environment settings (its entry point does not read
`.env`); keep your existing dashboard service configuration. To rebuild it:

```powershell
cd frontend
npm ci
npm run build
```

Authenticated API docs are at `http://127.0.0.1:8100/docs`. Every `/v1` request
needs `X-Trade-Agent-Token`. Use the actual configured owner id:

- `GET /v1/autonomy/status`: mode, strategy, pause status and current grant.
- `POST /v1/autonomy/run` with `{"owner_id":"100","symbol":"EURUSD","mode":"shadow"}`:
  immediate one-bar analysis; shadow never submits an order.
- `GET /v1/autonomy/runs`: archived candles, evidence, five reports and decisions.
- Dashboard Agent tab: journal/evidence summaries and manual/autonomous filters.

Fake broker candles are explicitly synthetic. Independent fake-broker processes
have separate in-memory books: they demonstrate logic, not shared MT5 execution.

## Enable bounded demo execution

After reviewing shadow reports, set `TRADE_AGENT_AUTONOMY_MODE=demo` for both the
API and research worker and restart them. Keep `TRADE_AGENT_TRADING_MODE=demo`.
Create a grant through `POST /v1/autonomy/grants`, for example:

```json
{
  "owner_id": "100",
  "symbols": ["EURUSD"],
  "expires_at": "REPLACE_WITH_FUTURE_UTC_TIMESTAMP",
  "max_risk_amount": "5",
  "max_volume": "0.10",
  "max_trades_per_day": 3
}
```

Replace the timestamp with an ISO timestamp ending in `Z`, within the next 24
hours. Limits cannot exceed deployment settings. A new grant revokes older
account grants. Changing model profile, strategy or timeframe requires a new
grant. The daily quota includes reserved/unknown attempts. Each demo order
passes the same current quote, spread, margin, exposure and stop-risk checks as
manual entries, with additional grant checks immediately before submission.
The stop-risk cap is an estimate; gaps and execution costs can exceed it.

Revoke with `POST /v1/autonomy/revoke` and `{"owner_id":"100"}`. Global pause
also blocks entries. To disable scheduled research, set mode `off` and restart
the worker. Leave protection/reconciliation running for existing positions.

## Free data and evaluation

- OHLC, quotes and tick volume come from the current MT5 broker; no extra market
  data subscription is introduced. Broker access/account requirements still apply.
- Indicators are calculated locally without a data vendor or model call.
- Optional `TRADE_AGENT_AUTONOMY_CONTEXT_ENABLED=true` fetches public Fed, ECB,
  BLS and BEA RSS headlines. Source failures are recorded as unavailable.
- Optional free FRED API key in `TRADE_AGENT_FRED_API_KEY` adds current-vintage
  DFF rate observations. It is not historical point-in-time macro evidence.

These feeds provide partial official context. There is no complete live economic
calendar, consensus forecasts, comprehensive FX news or social sentiment feed.
Technical-only decisions acknowledge absent optional context. Free data does not
make model inference or hosting free.

Review trade count, realized net P/L, fees, win rate, profit factor, average
wins/losses, unknown submissions and WAIT reasons by origin. Drawdown/equity
remain account-wide; shared-account equity is not independent strategy equity.
Historical manual trades are not reclassified as autonomous.

Save the `snapshot` field from a run to `snapshot.json`, then:

```powershell
python -m trade_agent.replay snapshot.json --output replay.json --spread 0.0001 --slippage 0.00002
```

Replay evaluates `ema-rsi-atr-baseline-v1`, with next-open fills, adverse gaps,
SL-first ambiguous candles and configurable costs. It uses price units, not MT5
lots, and excludes swaps, variable liquidity and margin. This is a technical
baseline, not historical performance of the LLM agents. Forward shadow/demo
records are needed to measure the actual configured model workflow.

Before connected demo testing, verify the actual model responds, candles/quotes
are fresh, PostgreSQL migration succeeds, history sync is complete and the
protection worker survives restart. Connected model/MT5/PostgreSQL acceptance
has not been performed by this implementation session.

Jarvis follow-up on 2026-10-02: migration, fresh broker candles, all five model
roles, official RSS, read-only website role, public login and desktop/mobile
views passed connected **shadow** verification. Research now runs as
`trade-agent-autonomy.service`; no demo execution grant was created. See SPEC-010
and SPEC-011 verification records for the deployment evidence and remaining gates.

Jarvis follow-up on 2026-10-04: v4 M5/M15/M30 comparison deployed with H1
context. The full suite passed 260 tests, followed by six comparison tests
including the additional pipeline archival check. Frontend build and live
desktop/mobile comparison checks passed. Native broker history supplied all
600/200/100 candles for EURUSD and XAUUSD. Historical preview cutoff is
2026-10-02 20:45 UTC; no direction qualified. Quotes remain stale during the
weekend, so fresh v4 AI research has not yet been verified against an open
market. All seven services were active; mode shadow, entries paused, no grant.
Backup: `/home/jarvis/trade-agent/backups/timeframe-comparison-20261004-20261004T062720Z`.

## Research diagnosis, October 11, 2026

The read-only diagnostic summary now aggregates the last 30 days of recorded
shadow decisions once a minute. It counts outcomes, the nearest-to-ready plan's
blockers, model failures, and the last decision time. When the two directions
are equally close, only their shared blockers count; this prevents an
opposite-side RSI rule from appearing to block every market decision. Failed
market-data checks are separate from decision counts. The summary is not an
AI accuracy score or a reason to loosen a rule.

Jarvis's initial query covered 1,028 recorded decisions: 826 WAIT and 202
research-failed; 960 had structured plans, none ready. The nearest-plan target
after spread/slippage failed in 801 runs, followed by missing entry trigger in
512. These runs predate the current pre-model trade-plan gate; zero occurred in
the preceding 24 hours at the time of the query. Always check timestamps and
code version before treating these as current behavior.

The separate, reproducible XAUUSD M15 bar-level screen lost money under both
cost assumptions and is published only as an exploratory benchmark. See
`specs/010-evidence-based-strategy-research/spec.md` for data hashes, limits,
and the promotion gate. The dashboard source has an experiment scorecard and
diagnostic panel; the public dashboard has an isolated root-owned deployment
and must be updated through its approved publication path. Neither panel can
approve or submit orders.
