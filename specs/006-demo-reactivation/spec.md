# SPEC-006 — Authorized demo reactivation

Owner explicitly accepted changing the three blockers following the offered scope, and increased demo capacity to five total exposures. The owner specified raising the $50 daily loss stop to $300 and the $300 evaluation drawdown stop to $1,000; the $10 estimated risk cap per new trade remains. Preserve existing exposure; never submit an order during rollout.

- R1: Configurable combined position + pending-order cap, default one, requested deployed value five. Keep individual count limits consistent and existing pending-submission reservation checks.
- R2: Record currently reviewed external position/order identifiers separately from agent attribution. Reconciliation must not re-pause solely because these exact existing identifiers remain present. New unknown identifiers must still pause; reviewed exposure still counts toward all risk limits.
- R3: While paused, archive prior loss evaluation and create a new USD-demo baseline at current equity, applying the explicitly approved $300 daily-peak decline/$1,000 evaluation drawdown limits. Archive review evidence and record an audit event; no reset on ordinary Telegram text.
- R4: Invalidate old unconfirmed entry proposals before resuming. Verify demo account, history freshness, limits, reviewed exposure, and unresolved execution intents. Keep entries paused on any activation failure.
- R5: Preserve existing positions/SL/TP, $10 estimated per-trade risk, exact confirmation, symbol allowlist and other gates. A subsequent loss breach may pause again. No broker orders during activation.
