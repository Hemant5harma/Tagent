# A practical plan for our AI trading agent: Vantage demo first

Prepared: 2026-09-30. Updated: 2026-09-30. Status: first-release implementation deployed for read-only use; connected execution acceptance is still pending. No autonomous trading or real-money authorization.

## First-release specification and rollout status (supersedes the historical baseline below)

The approved first release is **describe a trade in Telegram → grouped clarification → deterministic validation → one exact-action confirmation → broker submission → read-only mobile dashboard**. Approval expires after five minutes. Modified terms, stale quotes, expired approval, wrong account, or risk violations require stopping, not silently changing the order. Unknown outcomes reconcile; they must never trigger blind replacement orders.

### Approved boundaries

- Actual Vantage MT5 **demo** account ending 4634, not the fake broker. Live-money and autonomous entries/exits remain disabled.
- EURUSD only; maximum $10 estimated stop-loss risk; one combined position or pending entry; $50 daily loss stop; $300 evaluation drawdown stop. These are account-wide safety gates, so external positions also prevent new entries. Estimated risk is not a guaranteed maximum loss: gaps, slippage and separately charged costs can exceed it.
- The owner accepted these limits and a minimum-size connected smoke test. A subsequent upgrade authorization explicitly required **preserving all existing positions and keeping entries paused**. That condition remains in force. No execution smoke orders were placed during the upgrade.
- Explicit consent permits Telegram text plus a limited demo snapshot to the existing Cloudflare GLM 5.3 provider. This includes balance, equity, free margin, positions/orders, timestamps and masked account ID. Broker passwords, Telegram tokens and other credentials are excluded.
- Dashboard is view-only. Trade entry, cancellation, closing and SL/TP changes require exact-action confirmation in the private owner-authorized Telegram channel. No discretionary AI exits.

### Implemented interaction

Greetings and common account questions use normal responses. Other non-trade questions use GLM with timestamped evidence and no execution tools. Missing trade fields are requested together; fields are not invented. Isolated drafts reset after completion/cancellation, and `/new` or `/cancel_draft` clears draft state and pending entry confirmations without cancelling broker orders.

Supported commands: `/help`, `/account`, `/positions`, `/orders`, `/status`, `/pause`, `/new`, `/cancel_draft`, `/dashboard`, `/close <position-id>`, `/cancel_order <order-id>`, and the existing `/protection` command. Entry proposal summaries show demo identity, exact trade fields, quote, estimated risk, margin and expiry. Confirm trade/Cancel buttons are the authorization boundary; conversational “yes” is not authorization.

Telegram updates and offsets are committed to PostgreSQL before acknowledgement, with a durable reply outbox. The entry proposal is tied uniquely to the source update. Durable execution/mutation intents and the connector journal handle retries and uncertain outcomes. Replay can repeat a notification after a lost delivery acknowledgement, but cannot authorize a second broker submission. Execution acceptance is not proof of a completed fill or closure; broker reconciliation supplies subsequent state.

### Dashboard and metric contract

React/TypeScript/Vite dashboard uses warm cream, charcoal and terracotta without Claude branding. Phones use trade cards and Overview/Trades/Agent/System bottom navigation; desktop uses a sidebar. Authentication, loading, empty, stale and disconnected states are explicit. Five-second visible-tab polling has backoff and a fifteen-second stale-data indicator; requests read cached broker snapshots, not MT5 directly.

- Overview: balance/equity/free margin, open P&L, realized winning/losing/net P&L, closed-lifecycle win rate, profit factor, sampled drawdown, equity and closed-trade P&L charts, UTC period and attribution filters.
- Trades: proposals, pending orders, positions, closed lifecycle cards and ledger timeline. Broker IDs, prices, sizes, SL/TP and available costs are shown. No dashboard trade buttons.
- Agent: sanitized activity, observed model status/latency and execution-outcome counts. Instruction accuracy is **Not evaluated**; prediction accuracy is **Not available in assisted mode**. User-directed trading does not establish model predictive ability.
- System: broker, database/dashboard, Telegram, protection and observed model health; entry pause, risk-block reason, unprotected-position count and history coverage. Credentials, hidden reasoning and raw ledger payloads are excluded.

Win rate counts profitable fully closed balanced lifecycles after attributable profit/commission/swap/fees, divided by all eligible closed lifecycles; breakevens and sample size are separate. Partial closures/open lifecycles and ambiguous reversals do not enter this metric. Deposits/withdrawals are excluded from trading profit. Unattributed adjustments are explicitly separate. Agent-managed attribution requires ledger provenance; account-wide results are separately selected. No fake broker data was merged into Vantage history.

Initial history import is thirty days with overlapping incremental synchronization. “Complete” means the requested broker import succeeded, not that lifetime account history or all historical equity exists. Equity begins at the first recorded snapshot; sampled cash-flow-adjusted account drawdown is not a reconstructed historical intraday maximum. Individual timestamps are Asia/Kolkata; reporting windows are UTC. Closed-trade cards/charts are capped at the latest 200 selected lifecycles and labeled as a sample; aggregate metrics use all selected eligible records.

### Access and deployment

Jarvis `/home/jarvis/trade-agent` runs PostgreSQL plus independently supervised backend/Telegram, protection, reporting, dashboard and tunnel services. Backend listens only on 127.0.0.1:8100, connector on 8101, public dashboard service on 8102. Only 8102 is tunneled. The public service runs under a separate OS account and restricted database role, with no broker/model/Telegram credentials and no trading routes. Approval tokens and raw execution/ledger payloads are not granted to its DB role.

Request `/dashboard` in the authorized Telegram chat for the current temporary Cloudflare address and a single-use code. Codes expire after five minutes and are hashed; five failed attempts trigger a fifteen-minute global limit. Login creates a Secure/HttpOnly/SameSite=Strict twelve-hour cookie. No trading credentials enter the browser. Temporary URLs can change and are not an uptime guarantee. Quick Tunnels do not support SSE, so this release polls. [Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

The stopped legacy Vantage SQLite ledger and environment were backed up under the remote `backups/` directory. The original SQLite file remains. Migration into initially empty PostgreSQL verified per-table counts. No fake-broker ledger was imported. Restore/reboot drills are still pending; retaining backups is not proof of a tested restore.

### Broker clock correction — operational requirement

Observed on 2026-09-30: raw MT5 quote timestamps were approximately three hours ahead of synchronized host UTC. Vantage documents GMT+2 standard/GMT+3 daylight-saving server time. The connector now uses an **explicit +10,800-second profile**, normalizes quotes/deals/position/order timestamps to UTC, preserves raw deal timestamps, and converts history bounds and outbound expiration times consistently. Existing imported deals were reimported/upserted with corrected timestamps.

The installed profile is restricted to 2026-03-09 through **2026-10-31 00:00 UTC**; an operator must verify and update it before the daylight-saving transition. Dates outside that window fail closed rather than silently guessing an offset. This is a deliberate limitation, not automatic timezone handling. Broker-held SL/TP do not depend on the connector clock. [Vantage server-time documentation](https://www.vantagemarkets.com/en/academy/forex-trading-time/).

### Acceptance evidence and remaining gates

- 64 backend/connector tests pass: confirmation replay, expiry/stale quote/wrong account, draft isolation, parser outage, manual close confirmation/recovery with exact approved volume, auth/code expiry/attempt limits, partial lifecycle/cost accounting, clock normalization and existing lifecycle tests. Telegram routing regressions include the owner's exact screenshot question, account questions during a draft, educational conversation, and text that must not authorize an order. Unsupported attachments and malformed/expired callbacks are consumed safely instead of blocking the durable inbox.
- Ruff and mypy pass over 28 source modules; frontend production build succeeds. Five browser tests pass at 360, 390, 430 and 1440px, including all sections, no horizontal overflow, keyboard focus, login, stale and disconnected states. Screenshots were visually reviewed. The test uses clearly separated fixture data; connected authentication checks use the actual deployment.
- Connected read-only checks: actual demo identity; fresh normalized EURUSD quote; PostgreSQL history import; public unauthenticated rejection, one-use code replay rejection, secure cookie, authenticated snapshot, logout, and absence of public trading routes all passed.
- Actual GLM 5.3 conversational answer and structured extraction succeeded without broker mutations. A measured chat response took approximately three seconds; this is an observation, not a latency guarantee.
- Live read-only facade check on Jarvis: “do we have any open trades ?” returned the seven then-open XAUUSD positions directly from MT5, without using the structured parser. The balance question returned the actual demo account; the configured Telegram bot identity and polling mode were verified. Broker mutation methods were explicitly disabled during this check.
- The account contained externally opened XAUUSD positions without SL. None were opened, closed, or modified by this upgrade. The new loss circuit breaker latched and entries remain paused. The live dashboard is the source for changing counts and account values.
- **Still pending:** connected minimum-size entry/SL/TP/cancellation/closure; lost-response/process-kill acceptance against the live demo bridge; reboot/restore drills; complete comparison to an independently exported broker statement; provider/Telegram/tunnel outage drills; labeled instruction-quality evaluation. Do not call the system fully accepted or ready for unattended trading.

Next gate: review the existing external exposure and latched loss limits with the owner. Do not clear the latch, change those positions, or resume entries without a separate explicit decision. Then perform controlled connected demo acceptance and preserve evidence. Later strategy-testing and live-money gates below remain mandatory.

## Historical baseline and later-phase roadmap

The original observations below describe the pre-upgrade baseline, not current deployment status. They are retained to preserve the strategy-testing and live-money gates.

## 1. The outcome we actually want

You should be able to open Telegram, talk normally to your agent, ask what is happening in the market and your account, review understandable trade ideas, and eventually let a tested strategy operate within limits you explicitly approved.

The first account is your **Vantage MT5 demo**, not the application's fake broker. MT5 is the trading platform; Vantage provides the broker account. The server should handle MT5 connectivity continuously, so you can use Telegram from your phone.

There are three separate things to prove:

1. **Useful assistant:** it understands ordinary conversation and explains facts accurately.
2. **Reliable trading system:** it executes only authorized actions, recovers from failures, and respects limits.
3. **Strategy with evidence:** its results remain credible after costs, independent testing, and uncertainty analysis.

A working login proves none of the last two. A friendly model, profitable screenshot, or high win rate does not establish a trading edge. No architecture can guarantee profits or eliminate losses. Our goal is a system whose behavior is understandable, measurable, recoverable, and constrained.

**Recommendation:** improve this project rather than rewrite it. Build a conversational AI analyst around a deterministic trading engine. Start with one account, one instrument, and one transparent strategy. Add autonomy only after explicit acceptance gates.

## 2. Where this project stands

Evidence basis: local code and documentation inspected for this plan, plus the last recorded Jarvis deployment checks from this conversation. Those remote checks are historical session evidence, not a fresh uptime check performed for this document.

| Area | What we have | What it does not yet prove |
| --- | --- | --- |
| Broker connection | Last recorded deployment used `mt5_http`, Vantage demo login ending `4634`, and `VantageMarkets-Demo`; account data, quotes, and preflight were checked | A complete submitted-order lifecycle, disconnect recovery, or profitable strategy |
| Jarvis deployment | Ubuntu/Wine MT5 connector, persistent services, private network access | Full reboot acceptance or production-grade unattended reliability |
| Telegram | Owner-restricted bot, account/status commands, proposal/confirmation structure | Natural conversation, durable recovery of every incoming message, or assistant quality |
| AI | Existing GLM 5.3 provider configuration, used for structured parsing | Market awareness, reliable tool use, or predictive skill |
| Safety foundation | Independent risk checks, execution intents, journal, reconciliation, notification outbox | Correct behavior under every crash, concurrent request, or partial fill |
| Storage | Last recorded deployment uses a dedicated SQLite demo ledger | PostgreSQL production coordination, durable conversation checkpoints, or tested restores |
| Acceptance | A checklist exists in `ACCEPTANCE_RECORD.md` | Its connected checks remain unrecorded in that file; it is not a signed-off acceptance report |

The earlier [intelligence roadmap](INTELLIGENCE_ROADMAP.md) has useful foundations. This plan refines its delivery order using current code inspection and the later connection work. It does not overwrite it. Supporting every provider and both operating systems is not on the first release's critical path: accept the actual Jarvis deployment and selected GLM provider first.

### Confirmed code-level gaps to address

These are observations from the current source, not claims that every failure has already happened in production.

- `src/trade_agent/workflow.py`: one persistent owner thread carries previous parsed trade fields forward. Explicit draft lifecycle/reset boundaries are needed. A parser exception still flows into the decision node, so error/stale-state behavior also needs tests.
- `src/trade_agent/db.py` and `service.py`: incoming Telegram records store ID/status/owner, not the complete recoverable message payload.
- `src/mt5_connector/app.py`: mutation journal keys include an operation prefix, while the reconciliation endpoint looks up the raw supplied key. `service.py` requests reconciliation with the raw mutation key. Align this contract and test uncertain outcomes.
- `src/trade_agent/worker.py`: synchronous broker reconciliation and notification delivery share a monitoring loop. Slow calls or notification failures can delay deadline processing.
- `src/trade_agent/risk.py`: existing checks are useful, but this function does not enforce quote age, daily equity-loss limits, or portfolio loss-at-stop across positions and reserved orders. Summing lots is not a substitute for monetary exposure.
- `src/trade_agent/workflow.py`: SQLite configuration selects in-memory conversation checkpoints. Restart persistence is therefore different from the durability of the trade ledger.
- Account/process locks and connector journal locking need deployment-wide coordination and single-writer guarantees, not assumptions about a single Python process.

## 3. What we should build—and what we should postpone

### Keep the existing stack

Keep Python, FastAPI, the MT5 connector, Telegram, the independent risk engine, and LangGraph for conversation orchestration. Reuse the configured GLM provider after task-specific evaluation. Add PostgreSQL for durable state and coordination before unattended operation.

Use clear boundaries inside a small system, not a large collection of microservices. Separate operating-system processes where failure isolation matters: conversation/API, execution/reconciliation, and notification delivery. One repository and a small number of services are sufficient.

QuantConnect's framework separates signal generation, portfolio construction, risk, and execution. That separation is a useful engineering reference, not evidence that its default models or a particular strategy will suit us. We should borrow the design principle without introducing a full platform migration. [Source: QuantConnect framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)

### Give each component a limited job

| Component | Allowed responsibility | Must not do |
| --- | --- | --- |
| Telegram interface | Authenticate you, show data and proposals, receive explicit approvals | Treat arbitrary text as trade authorization |
| AI assistant | Explain, ask questions, request read-only evidence, draft proposals | Read secrets, run shell commands, call unrestricted MT5, increase limits |
| Market-data layer | Collect quotes/bars/specifications/history with timestamps | Invent missing data or silently substitute another broker's prices |
| Strategy module | Produce reproducible signals from approved rules and closed data | Rewrite its own rules while trading |
| Risk/authorization engine | Compute size, enforce policy, approve/reject exact actions | Delegate numerical safety or permission decisions to an LLM |
| Execution worker | Persist intent, submit, track outcomes, reconcile uncertainty | Blindly retry an order because an HTTP response was lost |
| Protection worker | Enforce approved exits and monitor broker protection | Depend on the AI provider or Telegram availability |
| Ledger/evaluation | Record events, costs, decisions, releases, and outcomes | Rewrite historical results to hide unsuccessful experiments |

**MCP is optional, not the missing foundation.** A later MCP wrapper can expose the same typed read-only and proposal tools. It must not expose unrestricted `order_send`. Reusing Hermes's model provider does not mean giving a general-purpose Hermes agent shell access to the trading account.

Postpone agent swarms, multiple “expert” models, reinforcement learning, self-modifying strategies, copy trading, multi-broker support, a large dashboard, and high-frequency trading. None is necessary for the first useful demo release.

## 4. The Telegram experience we should deliver first

These are **proposed behaviors**, not claims about commands already implemented.

| You say | Expected response |
| --- | --- |
| “Hi” / “What can you do?” | Friendly explanation, current mode/account, examples; no trade parser error |
| “Show my account” | Broker/server, masked account ID, demo/live flag, balance, equity, positions, freshness |
| “Analyze EURUSD” | Timestamped market snapshot, computed indicators, strategy conditions, limitations; no order |
| “Why didn't you trade?” | Exact reason: no signal, stale data, spread, risk limit, paused mode, or missing approval |
| “Explain this trade” | Entry rationale, invalidation, estimated risk/costs, strategy version, and what is uncertain |
| “Prepare a trade risking $10” | Clarify missing intent; calculate a draft under policy; show an expiring confirmation |
| “Stop new trades” | Deterministic entry pause, acknowledged without waiting for an LLM |
| “Close my position” | Exact account/ticket/quantity confirmation; never silently close unrelated positions |
| “How did we do today?” | Broker-reconciled realized/unrealized P&L, costs, drawdown, decisions and errors |

First command set: `/help`, `/account`, `/status`, `/positions`, `/orders`, `/mode`, `/pause`, `/new`, `/cancel_draft`, `/report`. Existing commands should remain compatible. Name draft cancellation differently from cancellation of a broker order.

The first conversational release is **read-only**. It can be useful before order execution acceptance is complete. It must clearly label facts, strategy signals, hypotheses, and missing evidence. It should say “I don't have a news feed connected” rather than imply it has checked current news.

For any later trade confirmation, display: account and demo/live status, symbol, direction, order type, quantity, entry/reference quote, SL/TP, estimated monetary loss at SL, margin, costs assumptions, expiry, and policy/strategy versions. Confirmation binds to that exact proposal/version and owner. Recheck current price, account, and risk immediately before submission; materially changed terms require a new confirmation.

Ordinary conversation such as “yes,” forwarded messages, old buttons, or model-generated approval text must not bypass the authorization boundary. Conversation memory may remember explanations; it must not silently reuse an old order's size or stops.

### AI quality requirements

- Use typed read-only tools for account data, quotes, completed bars, symbol specifications, history, indicators, and risk calculations. Add a separate proposal tool only after execution gates pass.
- Compute all financial values in code; do not rely on model arithmetic or self-reported confidence percentages.
- Preserve evidence timestamps/IDs and concise decision summaries. Do not require storing hidden model reasoning.
- Build at least 150 representative evaluation cases: greetings, follow-ups, incomplete trades, changed drafts, malicious instructions, wrong accounts, stale data, model outages, and misleading numerical responses.
- Release target: at least 95% correct routing/useful responses on the held-out conversational set, and zero critical authorization or financial-field errors in the safety suite. These are engineering gates on finite tests, not guarantees of future correctness.
- Acknowledge requests promptly, target under two seconds for acknowledgement under normal load, and enforce bounded model timeouts. If the model is unavailable, account/status/pause/protection must still work.
- Limit tool calls, tokens, and daily AI spend. Cache reusable analysis by evidence snapshot. Never call an LLM for every tick.

External pages and messages are untrusted data. Tool allowlists, least privilege, and human approval are necessary even with a good system prompt; a second “guardrail model” is not an authorization system. [Source: OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

## 5. Market data and the first strategy

### Data before intelligence

Start with EURUSD, subject to the exact symbol/specification available on this Vantage account. Use hourly completed bars for strategy decisions; current bid/ask is still required for execution. This is a scope choice, not a claim that EURUSD or hourly trading is profitable.

Store UTC timestamps, retrieval time, broker/server identity, symbol mapping, prices, spread, and data-quality flags. Validate ordering, duplicates, missing bars, session closures, price anomalies, and enough indicator warm-up history. Do not fabricate prices to fill a gap. Store historical data immutably with a dataset version/hash.

MT5 history availability is limited by terminal history/settings, timestamps require careful UTC handling, and bar index zero is the current bar. Consequently, history coverage must be measured and unfinished bars excluded from completed-bar signals. [Sources: MT5 history and UTC](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py), [MT5 current-bar indexing](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py)

Begin collecting broker quotes and executions now during implementation. For research, seek several years covering different conditions; if available MT5 history is inadequate, record that limitation and evaluate a suitable licensed historical source. Do not quietly replace missing broker data with a convenient feed and claim equivalent execution results.

A news/calendar filter is a later explicit integration with source timestamps, licensing, coverage, and outage policy. Until that exists, the assistant is not “news aware.” If a strategy requires that feed and it fails, that strategy cannot enter new trades.

### First research candidate—not a promised money-making strategy

Use one simple, versioned trend/breakout hypothesis, for example a completed-bar channel breakout with an ATR-based initial stop, fixed risk sizing, and a defined exit/time limit. Before testing, write down the exact lookbacks, entry timing, stop/target rules, session rules, maximum holding period, and all costs. Choose a small predeclared parameter set, not thousands of trials until an attractive chart appears.

The purpose is to establish an interpretable end-to-end baseline. Reject it if evidence is weak. The plan does not assume the first candidate has an edge.

Compare three things on the same point-in-time inputs:

1. No-trade/cash baseline.
2. Deterministic strategy without AI affecting trades.
3. The identical strategy plus an optional AI veto/filter, initially in shadow mode only.

Initially, AI explains signals rather than creating discretionary entries. Keep an AI filter only if out-of-sample evidence shows an incremental benefit after its additional costs and latency. Record rejected signals too, so the comparison is not cherry-picked. Historical LLM tests can be contaminated by model training knowledge of later events; mask dates/identities where practical and treat prospective shadow results as essential, not optional.

## 6. How we test for an actual edge

### Reproducible research protocol

1. Freeze the hypothesis, cost model, parameter search budget, and evaluation criteria before looking at final results.
2. Split data chronologically into development, validation/walk-forward windows, and a final untouched holdout. Never randomly shuffle market time series.
3. Use one shared signal implementation for replay and forward execution. Features at decision time may use only information then available. If labels/holding periods overlap test boundaries, purge those overlaps and use an appropriate separation gap.
4. Model bid/ask, commissions, swaps/financing, instrument specifications, lot rounding, rejected orders, delays, and slippage. Execute after the signal becomes knowable—not at an earlier candle price.
5. Resolve ambiguous intrabar SL/TP ordering using finer data or a conservative assumption; do not automatically choose the profitable outcome.
6. Record every tested variant, including failures. Lock the final candidate before forward observation. A meaningful change creates a new version and requires new independent validation.
7. Compare backtest decisions with prospective shadow/demo decisions and explain discrepancies in inputs, timing, or fills.

Lookahead bias can produce convincing but unusable backtests. Freqtrade provides a practical reference for detecting it; we borrow its testing ideas, not assume its tooling directly supports our MT5 project. [Source: Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)

Backtest and actual execution can differ because of data timing, fill assumptions, fees, slippage, and broker behavior. Calibrate the simulator with our recorded observations rather than believing a backtest equity curve. [Source: QuantConnect reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation)

### Evidence we review

- Net expectancy per trade, net return, realized and mark-to-market drawdown, profit factor, turnover, exposure, losing streaks, and tail losses.
- Costs as a share of gross returns, entry/exit slippage, rejected/unknown orders, and protection latency.
- Performance by walk-forward window and market condition, not just a full-period average.
- Uncertainty intervals using dependence-aware methods such as a suitable block bootstrap. Trade counts are not counts of independent observations.
- Sensitivity to modest parameter changes and worse execution. Predeclare stress scenarios, including doubled variable execution costs and delayed entries.
- Multiple-testing adjustment where applicable. The Deflated Sharpe Ratio is one reference, but its inputs and assumptions must be supported; do not report a decorative number from insufficient evidence. [Source: Bailey and López de Prado](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)

**Proposed promotion standard:** positive net results on untouched holdout and forward evidence, acceptable predeclared drawdown, no dependence on one lucky window, and a positive lower bound for net expectancy under the chosen 95% dependence-aware confidence procedure. Review cost-stress results against the same predeclared viability criteria. These are proposed project rules, not universal investment standards; freeze them before testing and do not loosen them after disappointing results.

Plan for at least **eight weeks and 100 closed strategy trades** before a live-readiness review, whichever takes longer. This is only an initial observation floor, not statistical proof or an automatic pass. A low-frequency strategy may need many months. Do not increase trade frequency just to reach the count. If evidence is inconclusive, remain in demo or reject the strategy.

## 7. Proposed demo risk policy

These are deliberately small experimental limits to review before activation. They are not current configuration claims and must not be copied automatically to a real-money account.

Use an explicit experimental risk capital of the lesser of current equity and **$10,000**, even if the demo account displays a much larger virtual balance. Freeze the reference budget for the evaluation period; do not let short-term gains automatically increase limits.

| Control | Initial proposal |
| --- | --- |
| Account/mode | Exact allowlisted Vantage account, broker/server fingerprint, demo flag required |
| Instruments | EURUSD only, broker symbol mapping verified |
| New position risk | At most 0.10% of experimental capital; $10 at a $10,000 reference budget |
| Aggregate committed risk | At most 0.30%, including positions, pending orders, and unresolved/reserved submissions; a ceiling, not a target |
| Concurrent exposure | One strategy position or entry order initially; unknown submissions reserve capacity |
| Daily loss stop | 0.50% of reference capital; $50 at $10,000; no further entries until reviewed |
| Evaluation drawdown stop | 3% of the tracked experimental-equity high-water mark; pause and investigate |
| Trading frequency | At most three new entries per day; no rapid re-entry after an error or stop |
| Prohibited behavior | Martingale, loss-chasing, averaging down, unbounded grids, increasing limits, removing/widening stops to evade risk |

Compute daily loss from change in equity including open P&L, commissions and swaps, adjusted for deposits/withdrawals, using an explicit 00:00 UTC boundary. Also track intraday peak-to-trough equity loss so a profitable morning cannot hide a large afternoon loss. Define experimental equity as the reference budget plus cumulative net strategy P&L, including marked open positions; use its high-water mark for the evaluation drawdown rather than the much larger broker demo balance. Neither a process restart nor a day boundary resets an unresolved safety incident or the evaluation drawdown limit.

For the controlled demo evaluation, do not mix manual trades into the same account. If external trades appear, include their exposure conservatively, pause new strategy entries, and request review; never silently take ownership of them.

Size from entry-to-stop loss in account currency using verified broker calculation/specifications, with costs and adverse-execution allowance. Round size down. If the minimum lot exceeds the risk budget, skip the trade. Do not assume one lot or one pip has the same value across instruments.

Add tested quote-age, spread, margin, session, and clock-drift thresholds. Recheck at submission. Ordinary entry restrictions must not accidentally prevent an authorized risk-reducing exit, although exits still require valid broker data and reconciliation.

Every entry must have broker-held protective orders when supported; verify their presence after execution. Missing protection triggers the approved emergency policy and an alert. A stop loss is not a guaranteed maximum loss: gaps, liquidity, and execution failures can exceed it. Daily/drawdown circuit breakers likewise cannot guarantee the final loss amount.

## 8. Execution and recovery: the non-negotiable work

### A durable order lifecycle

Track proposal, authorization, reserved exposure, submission, unknown outcome, broker order, individual deals, positions, modifications, and closure as distinct states/entities. Store their relationships, not a single ambiguous “trade ID.”

Persist the execution intent before contacting MT5. Submit through one authorized writer. If the response is lost, mark the outcome unknown, keep the exposure reservation, and reconcile using journal plus broker order/deal/position history. An empty open-order list does not prove that nothing executed. Handle partial fills, fill/cancel races, and netting versus hedging explicitly.

An MT5 preflight success is not an execution guarantee. [Source: MetaQuotes order_check](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py)

Use PostgreSQL transactional claims/reservations and unique constraints for competing requests. Do not hold database transactions open across an LLM call. An expired worker lease does not prove an old broker call stopped; do not start a second writer until fencing/reconciliation makes that safe. Keep the connector explicitly single-writer, with process-level exclusion as well as in-process locks.

Do not promise distributed exactly-once execution. Aim for durable delivery, idempotent handling where possible, and conservative resolution of uncertain external effects. LangGraph checkpoints are not a substitute for the execution ledger: replayed workflows may rerun unfinished side effects. [Source: LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)

### Message durability and failure isolation

Store the complete authorized Telegram update and processing state before advancing the polling acknowledgement; use a durable outbox for responses. Handle duplicates and crash recovery. Store only necessary fields with restricted access/retention, and redact secrets from logs. Telegram confirms updates when polling advances past their ID, and its server-side retention is bounded; an in-memory queue is not enough. [Source: Telegram Bot API](https://core.telegram.org/bots/api#getupdates)

Separate execution/protection from AI and notification workloads. Use monotonic scheduling for intervals and UTC for persisted deadlines. Broker calls need bounded timeouts; alerting must not block protective work. Retry mutations only after resolving the previous attempt's outcome.

| Failure | Required behavior |
| --- | --- |
| Model/provider unavailable | Explain limitation; no new AI proposal; existing protection and direct status commands continue |
| Stale/missing prices or wrong account | Block new entries; show reason; do not fabricate freshness |
| Lost response after submission | Mark unknown, reserve exposure, reconcile; do not send a replacement blindly |
| Telegram outage | Trading state/protection remain independent; queue bounded alerts, expose separate health checks |
| Database unavailable | Block new entries; broker-held stops remain; alert through an independent health path; acknowledge that new persisted mutations are unavailable |
| Terminal/network outage | Pause entries, reconnect with backoff, reconcile before resuming; local software cannot guarantee exits while disconnected |
| Backend restart/reboot | Recover intents/deadlines and broker state before permitting entries |
| Risk limit breached | Pause new entries; apply the previously approved cancel/exit policy to managed exposure only; require reviewed reset |

Distinguish `/pause` (no new entries), cancellation of managed pending orders, and emergency flattening. Flattening is destructive and must have explicit user authorization or a precisely preauthorized emergency policy. It is not equivalent to stopping a service, and its fills are not guaranteed.

## 9. Jarvis deployment and security

- Keep the backend private over Tailscale and retain authenticated API access. Telegram long polling does not require a public inbound backend URL. A stable private address does not make the host highly available.
- Keep the current Ubuntu/Wine setup for measured demo validation. Before real money, evaluate a dedicated native Windows MT5 connector host as an alternative that removes the Wine layer; it still needs reboot, reconnect, patch, and recovery tests. Do not migrate merely for appearances or assume Windows alone solves reliability.
- Separate assistant and broker execution operating-system identities. The assistant cannot read broker/connector credentials or invoke the execution endpoint directly. Separate demo and eventual live secrets, databases, service identities, and account allowlists.
- Rotate credentials/tokens previously shared in chat through a controlled setup before unattended use; do not include them in this document, Git, command history, or model prompts. Do not delete or recreate Telegram bots unnecessarily.
- Restrict connector networking to loopback on one host, or an authenticated encrypted tailnet path with narrow ACLs if hosts are split. Do not publish an unrestricted MT5 HTTP endpoint.
- Pin tested dependencies, record deployed release hashes, use planned updates, and provide a rollback runbook. Rollback must reconcile existing intents rather than restore stale state and replay orders.
- Add off-host encrypted backups and a tested restore procedure. Initial targets: database recovery-point objective no worse than five minutes and recovery-time objective under one hour; broker history must reconcile anything not restored. Local backup files alone do not meet this objective.
- Monitor data age, connector/account identity, unknown intents, unprotected exposure, worker heartbeat, DB failures, clock drift, disk space, outbox backlog, provider latency and spend. Add an independent host/service alert path, because an offline Jarvis cannot reliably report its own failure through Telegram.
- Before live consideration, complete one full host reboot drill, one restore drill, and a multi-session soak including market closure/reopening. Record evidence and remaining limitations.

## 10. Delivery sequence: useful quickly, money safety separately

Effort estimates below are planning ranges for focused implementation and testing, not promises. Market observation cannot be compressed by coding faster. Phases can overlap only where their dependencies permit.

| Phase | Deliverable | Approximate effort | Exit gate |
| --- | --- | --- | --- |
| A: Baseline and truthful status | Capture deployment identity/configuration without secrets; distinguish fake/demo/live; record known gaps and pause semantics | 0.5–1 day | Repeatable account/quote health check; no claim of execution acceptance |
| B: Useful Telegram assistant | Normal conversation, read-only account/tools, help, clear modes, explicit draft boundaries, bounded GLM calls | 2–4 days | Read-only assistant evaluations pass; greetings and questions never create orders |
| C: Assisted demo execution | Fix recovery contracts, durable inbox/state, coordination, risk additions, isolated workers; complete connected lifecycle tests | 4–8 additional days | Every required assisted-demo safety test passes; you approve each trade |
| D: Research and shadow operation | Data collection/replay, one frozen candidate, costs, experiment tracking, daily reports | 5–10 days, partly alongside C | Reproducible results and prospective shadow agreement; no automatic orders yet |
| E: Bounded autonomous demo | Approved strategy and limits, circuit breakers, monitoring, acceptance record | 2–4 days after C/D gates, then extended observation | Explicit activation; incident-free operation plus continuing performance review |
| F: Live-readiness review | Independent evidence review, security/recovery acceptance, jurisdiction/account checks, user-approved capital and loss budget | No fixed date | All engineering/economic gates pass; separate explicit real-money authorization |

The shortest useful milestone is Phase B—not waiting months to talk to your agent. A reasonable first target is a conversational read-only demo assistant within the first few focused development days. Assisted orders follow only after safety acceptance. Autonomous demo is a later, deliberate mode change.

### Concrete implementation backlog

| Priority / ID | Likely code area | Testable completion criterion |
| --- | --- | --- |
| P0-01: identity and mode | `config.py`, `broker.py`, connector service, Telegram status | Wrong login/server/demo flag blocks entries; no silent fake-broker fallback |
| P0-02: mutation reconciliation | `src/mt5_connector/app.py`, journal, backend service | Same operation ID resolves before/after lost responses for cancel/modify/close |
| P0-03: draft isolation | `workflow.py`, `app.py`, Telegram handlers | New/completed/cancelled drafts cannot leak size/SL/TP; parser outage cannot revive old intent |
| P0-04: durable coordination | `db.py`, `service.py`, DB migrations | PostgreSQL concurrent confirmations preserve limits and do not duplicate submission |
| P0-05: durable Telegram inbox | `telegram.py`, `db.py`, handlers | Crash before/after acknowledgement recovers payload and response without duplicate action |
| P0-06: risk and data quality | `risk.py`, market-data domain, connector calculations | Stale quotes, bad currency conversion, minimum-lot excess, daily loss, and reserved exposure fail closed |
| P0-07: isolated protection | `worker.py`, service entry points, deployment units | Slow AI/Telegram does not delay risk work; uncertain exit never causes blind duplicate close |
| P1-01: conversation/tools | `ai.py`, `workflow.py`, new typed tool layer | Held-out routing/evidence tests pass; execution credentials absent from model context |
| P1-02: market data/research | New data, strategy, replay and evaluation modules | Closed-bar point-in-time replay matches forward signal logic and costs are explicit |
| P1-03: acceptance/reporting | Tests, deployment runbooks, acceptance records | Broker-reconciled daily reports, reboot/restore evidence, release hash and incident log |

Use small reviewable changes and run the existing test suite after each change, plus targeted fault tests. Preserve current local work and the separate fake-broker ledger as test data; never merge fake fills into broker performance results.

### Connected demo test matrix

Test only after explicit demo-test activation, in an active market session with a tiny approved test budget. Account read/preflight tests do not place orders; lifecycle tests do and must be clearly labeled.

- Identity, fresh quote, symbol specs, calculated loss/margin, rejection boundaries.
- Market buy/sell with verified broker SL/TP; pending limit/stop placement and cancellation.
- Protection modification, full/partial closure, duplicate confirmation, expired confirmation.
- Lost response immediately before/after submission; delayed history; partial fills and fill/cancel races using deterministic fault injection where broker reproduction is impractical.
- Restart with open exposure and overdue deadlines; terminal disconnect; Telegram/provider outage.
- Wrong account, external positions, netting/hedging fixtures, concurrent commands, out-of-date quote.
- Full host reboot, backup restore, reconciliation with broker history, and notification replay.

Record broker ticket/deal evidence, expected/actual outcomes, timestamps, release version, and redacted logs. Unit tests alone cannot sign off connected execution; broker demos also cannot reproduce every pathological failure, so both forms of testing are required.

## 11. When—and whether—we move to real money

Demo and live results are not equivalent. Vantage itself notes that demo execution can differ from live execution. [Source: Vantage demo conditions](https://www.vantagemarkets.com/quick-question/demo-account-realistic-spreads/)

Live consideration requires all of the following:

1. Engineering acceptance is complete, with no unresolved high-severity execution or authorization issue.
2. The locked strategy passes the predeclared research/forward-evidence standard; uncertainty is explicitly reviewed.
3. The account's broker entity, product permissions, local legal/regulatory requirements, funding rules, and automation terms are checked for your actual residence. A working offshore demo login does not establish eligibility to trade real funds.
4. You choose an amount you can afford to lose and explicit per-trade, daily, and total loss limits. We must not infer these from virtual demo equity.
5. Credentials are rotated and isolated, backup/recovery and independent alerts are tested, and you know how to inspect/close exposure directly in MT5 if our backend fails.
6. You explicitly authorize a separate live deployment after seeing its exact account fingerprint and policy. “I feel confident” or an AI recommendation is not sufficient authorization by itself.

The first live stage, if reached, is a **small, user-approved, human-confirmed pilot**, not full autonomy. Measure actual fills/costs against demo assumptions before considering any increase. Increasing capital or autonomy requires another review. If the strategy fails to demonstrate an edge, the correct result is to remain an assistant/demo system—not to launch anyway.

## 12. Decisions we need from you—not secrets

Research and read-only assistant implementation can proceed with the proposed defaults. Before enabling demo orders, confirm:

- EURUSD-only initially, or your preferred instrument (which requires its own data/risk tests).
- The experimental capital and small risk limits in section 7.
- Human approval for every initial demo order.
- Whether the demo account will be reserved exclusively for this agent.
- Allowed trading sessions and the behavior you want for pending orders/open positions at a stop condition.
- A monthly infrastructure/data/model budget and a separate emergency-alert destination.

Before live consideration, we additionally need your jurisdiction, actual capital budget, acceptable loss, account terms, and explicit authorization. Passwords/tokens should be entered through protected configuration, not pasted into another plan or model prompt.

## 13. Costs and scope discipline

Do not buy extra models or a new platform before the baseline works. Reuse Jarvis, the existing model provider, Telegram, and the connector for the demo milestone. Track actual model usage and hosting/storage costs. A separate Windows host, independent monitoring, backups, or licensed historical/news data may add cost later; quote those options at decision time rather than invent a fixed price now.

Spend engineering effort first on clear conversation, clean data, deterministic risk, recovery tests, and honest reports. More indicators, more agents, and a prettier dashboard do not repair incorrect order state or establish profitability.

## 14. Research references and how they influenced this plan

Primary documentation/papers reviewed on 2026-09-30. Their design principles inform the recommendations; they do not certify our implementation or any strategy.

1. [QuantConnect Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview): separate signal, allocation, risk, and execution responsibilities.
2. [QuantConnect live/backtest reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation): compare actual behavior with modeled inputs, timing, fees, and fills.
3. [MetaQuotes order_check](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py): preflight is not a guarantee of execution.
4. [MetaQuotes copy_rates_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py): UTC and history-coverage constraints.
5. [MetaQuotes copy_rates_from_pos](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py): current-bar indexing and completed-bar discipline.
6. [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/): explicitly test information leakage in backtests.
7. [Bailey and López de Prado: The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf): selection bias, multiple testing, and non-normal returns undermine naive performance claims.
8. [LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api): replay/resumption and idempotent side-effect design.
9. [Telegram Bot API](https://core.telegram.org/bots/api#getupdates): polling acknowledgement and update durability boundaries.
10. [OWASP LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html): least privilege and layered authorization beyond prompts.
11. [Vantage demo conditions](https://www.vantagemarkets.com/quick-question/demo-account-realistic-spreads/): demo execution is indicative, not identical to real execution.

## Bottom line

Build a useful assistant first, a provably better-tested execution system next, and evaluate a strategy separately. Start interacting through Telegram early. Start with explicitly confirmed Vantage demo trades only after safety checks. Earn autonomous-demo permission through testing, and consider real money only after independent evidence and a separate decision—not because a deadline has arrived.
