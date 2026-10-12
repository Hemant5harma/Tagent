# SPEC-006 verification — 2026-09-30 activation

The owner approved five total demo exposures, a fresh loss evaluation, and resuming entries, then specified $300 daily and $1,000 evaluation drawdown stops. The per-trade estimated loss cap remains $10. The full suite passed **170 tests**; Ruff passed; mypy passed on 33 source modules.

Jarvis archived the old evaluation in `spec006_authorized_reset:<account>`, marked only the existing external position as reviewed, and set a fresh equity baseline. Old unconfirmed proposal approvals were cancelled. `.env.pre-spec006` and a pre-deployment source archive preserve the prior settings/source. Broker history was retained. Backend, protection and reporting services restarted.

First read-only broker preflight encountered a moving-quote estimate above $10 and left entries paused. Automatic market protection was revised to reserve configured quote-movement tolerance plus one tick. The next broker preflight passed at **$8.60** estimated risk.

`deploy/verify_spec_006.py` confirmed DEMO, EURUSD/XAUUSD, five total exposures, $10 per-trade estimate, $300 daily stop, $1,000 drawdown stop, **entries paused=false**, breaker=false, one existing position, zero pending orders, 315 equity snapshots and 556 broker deals. History cache was 49.8 seconds old. Backend, protection, reporting, dashboard, tunnel and connector services were active.

No new order was submitted during activation. The existing XAUUSD buy remains open without SL/TP and counts as one of five exposures. Account-wide reporting includes it; agent performance does not attribute it to the agent. The breaker can latch again on a $300 daily peak decline or $1,000 evaluation drawdown. A new trade still needs one exact Telegram Confirm trade button; connected submission/fill/closure acceptance remains untested.
