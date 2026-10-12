# SPEC-010 — Evidence required before autonomous demo trading

The objective is to discover whether a repeatable Vantage demo trading edge exists after real execution costs—not to make the language model trade more often. No result in this spec authorizes clearing the loss breaker, resuming entries, enabling an autonomous grant, or using real money.

## Implementation update, October 11, 2026

- The authenticated, loopback-only MT5 connector now has bounded read-only historical bar and bid/ask tick routes. It validates ordered UTC-normalized timestamps and prices, rejects responses over 10,000 rows rather than truncating them, reports missing candle intervals and boundary coverage, and hashes each dataset. Tick coverage remains explicitly *unverified*: a nonempty tick response alone cannot establish that every market tick was delivered.
- The immutable `deploy/export_research_history.py` helper checks the hash before saving a versionable snapshot; it cannot overwrite an existing file and refuses non-loopback connector URLs.
- Jarvis was verified as connected to the Vantage demo account with zero positions and zero pending orders after connector rollout. A four-bar XAUUSD M15 sample from October 9, 12:00–13:00 UTC passed candle coverage checks. A five-minute XAUUSD bid/ask tick sample contained 1,678 observations; its completeness is not asserted. Both snapshots are stored under `/home/jarvis/trade-agent/data/research/`, not used as evidence of profitability.
- A 30-day XAUUSD M15 export (September 10–October 10 UTC) contains 2,016 closed candles, 21 reported gaps, and an attached Vantage instrument specification (0.01 minimum lot, 0.01 tick size, USD 1.00 tick value per lot). Sample gaps line up with the daily market break and weekends; the full gap list still needs explicit session-calendar validation, so the export is not marked complete. Its data hash is `da0dc562af0322b1383b2370f05a570ac9ec6510c14d2bbe9a9aff50e3f2f250` and full snapshot hash is `f96908c21e43ab21ab878a6f32f9f5edaf4e449db2acd386771ee3903d8b30d6`.
- This is an initial data-acquisition and bar-level screening slice. No validated full-period tick dataset, tick-accurate broker-lot replay, locked holdout result, or profitable signal exists yet. Autonomy remains shadow-only, and this update grants no order authority.

### First offline screening result—not a promotion test

`research_screen.py` is a fixed EMA/RSI/ATR comparator, not the production multi-timeframe agent. It forms signals only from earlier closed bars, enters at the next bar, sizes in Vantage lots and tick value, uses each bar's reported spread, charges explicit slippage/commission assumptions, and chooses the stop when a bar touches both SL and TP. It reports a chronological 70/30 split; the later period is exploratory, **not** a locked holdout.

On the 2,016-bar XAUUSD dataset, the optimistic zero-commission/5-point-slippage screen produced 186 closed trades, net **−$274.30**, win rate **34.4%**, profit factor **0.83**. The later 30% produced 54 trades, **−$198.02**, profit factor **0.60**. With 10-point slippage and $7 per lot per side assumed commission, the full-window result was **−$308.96** across 186 trades. These are hypothetical bar-level USD estimates, not broker-realized P/L. The data's 21 gaps and intrabar spread uncertainty remain unresolved; margin, swap and partial fills are not simulated.

**Decision:** reject this comparator as a candidate for autonomous demo execution. Do not tune its thresholds on this same later period and call the result out-of-sample. Next, validate all gaps against the broker session calendar, calibrate costs from actual Vantage deals, acquire a sufficiently long tick/quote history, and pre-register a small alternative candidate set before an untouched forward period.

### Diagnostic feedback loop

The reporting worker now materializes a read-only 30-day shadow-decision summary every minute. It distinguishes recorded WAIT, failed research, ready plans, observed model health, the last decision timestamp and top blockers on the chosen or closest-to-ready side. Tied sides contribute only shared blockers. The summary is capped at 5,000 recent runs and marks truncation. It does not count repeated stale-quote checks as trade decisions, and blocker frequencies cannot be interpreted as returns from removing a rule.

The first corrected Jarvis summary (October 11, 17:40 UTC) saw 1,028 recorded runs, 960 with plans, zero ready plans, 202 research failures and zero runs in the prior 24 hours. The closest-plan target-after-cost rule blocked 801 runs and missing closed-bar trigger blocked 512. These are historical observations, some from before the pre-model gate; the next open-market cycle is needed to confirm current behavior. The offline negative XAUUSD benchmark is published separately as an exploratory scorecard, not mixed with broker-realized P/L.

## Current baseline, observed October 10, 2026

- The Vantage demo account is flat. Entries are paused and the account-wide drawdown breaker is latched.
- Two completed agent-managed, user-confirmed XAUUSD trades lost $17.45 combined. The much larger account-wide gain came from trades marked external to this agent and must not be used as its training label or success metric.
- The shadow journal contains 3,669 decisions from October 2–9: 3,467 WAIT, 202 failed research, and no authorized autonomous order. Of 960 runs with deterministic trade plans, none had a ready plan. EURUSD spread and target-after-cost checks and XAUUSD target-after-cost checks were frequent blockers. These are observations about the current rules, not evidence that relaxing them would improve returns.
- The five-role model workflow recorded 98 factual-validation failures, 50 timeouts, 28 invalid-value failures and 25 provider-unavailable failures among the failed runs. A conversational provider health check succeeded on October 10, but that does not validate research reliability or trading skill.
- The existing `replay.py` is an OHLC technical baseline with constant spread and price-unit sizing. It is not a Vantage-lot, variable-spread, tick-accurate replay of this autonomous agent. The archived shadow window is too short to establish an out-of-sample edge.

## Immediate engineering rule

When code-generated trade plans are enabled, a missing or invalid plan must fail closed before invoking research AI. A complete plan with no ready setup records WAIT, the explicit blockers and zero model calls. Only a ready primary-timeframe plan may reach the model. This removes expensive failed analysis of trades the deterministic engine already forbids; it does not change strategy thresholds, risk limits, the pause, or broker state.

- **S1:** Planning failure or missing plans produces a distinct failed-research result and no model call or order.
- **S2:** Valid plans with no ready side produce WAIT with the archived blockers and no model call or order.
- **S3:** A ready primary-timeframe plan still reaches the existing model, confidence, quote, grant and risk gates; readiness alone cannot submit an order.
- **S4:** This change must not clear the existing pause or drawdown breaker, loosen any strategy threshold, or authorize connected execution.

## Research sequence

1. **Acquire broker-specific history.** Add a read-only, authenticated UTC-range export for Vantage MT5 closed bars and bid/ask ticks. Record symbol specification, broker server-clock normalization, available date range, missing intervals, quote spread distribution and a dataset hash. MT5 provides `copy_rates_range` and `copy_ticks_range`, but bar history is limited by what the terminal makes available; insufficient coverage must fail the study rather than be silently filled. [MT5 bar-history API](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py), [MT5 tick-history API](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py).
2. **Pre-register a small set of falsifiable candidates.** Test the current EMA/RSI plan against simple trend-breakout and pullback baselines, including a no-trade baseline. Fix decision times, session filters, stop/target rules and the parameter search space before inspecting the reserved holdout. Do not use account-wide external profit as a label, copy external discretionary entries, or search unlimited parameter combinations.
3. **Replay chronologically with costs.** Form signals from bars that had closed by decision time and enter no earlier than the next executable bid/ask quote. Use MT5 lot sizes, tick value, minimum stop distance, variable spread, commission, swaps, slippage calibrated from broker deals and latency. Test adverse gaps and ambiguous SL/TP touches pessimistically. Compare the resulting execution trace with MT5 Strategy Tester real-tick and delay modes where practical. [MT5 testing documentation](https://www.metatrader5.com/en/terminal/help/algotrading/testing).
4. **Separate discovery from proof.** Use rolling or expanding training windows, untouched later holdouts and a final locked-forward demo shadow period. Report every tried configuration, not only the winner; use an overfitting diagnostic and sensitivity to doubled costs. Backtest selection can produce false positives even when the best historical result looks strong. [Bailey et al., *The Probability of Backtest Overfitting*](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
5. **Measure an edge, not “AI confidence.”** Track net P/L, trade count, average win/loss, profit factor, drawdown, exposure time, fill rejection, slippage, data gaps and performance by month and market regime. A candidate fails if gains disappear under realistic costs or are concentrated in one interval. LLM benchmark scores are not a proxy for live trading outcomes; a live study of 21 models found that general model rankings did not imply better trading results. [LiveTradeBench](https://arxiv.org/abs/2511.03628).
6. **Promote only after review.** Keep the model as a bounded analyst or veto, not the source of unverified prices or risk rules. First prove a deterministic candidate in holdout and forward shadow data; then compare the model-assisted variant against it on the same timestamps. Autonomous *demo* execution needs a separate operator decision, a new limited grant, corrected research failure rate, no unresolved broker submissions and an explicitly reviewed breaker reset. Real-money trading remains a later, separate approval gate.

## Minimum deliverables before any promotion

- Versioned broker dataset and data-quality report with coverage, clock profile and hashes.
- Reproducible cost-aware replay with no lookahead, unit tests for bid/ask, gaps, partial fills, fees and stale data, and an MT5 Strategy Tester cross-check.
- A frozen candidate registry, recorded search count, walk-forward and untouched-holdout results, plus sensitivity to costs and parameters.
- Forward shadow journal that records every candidate and abstention, with independently reconciled broker quotes and no trades.
- A signed operator review of performance, risk, external account activity and the current $20 estimated-loss cap versus the formerly approved $10 cap. No automatic reset or limit increase.
