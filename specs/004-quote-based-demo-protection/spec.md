# SPEC-004 — Quote-based demo protection and XAUUSD

User requested XAUUSD support and automatic price-based SL/TP followed by one exact confirmation. This supersedes the earlier EURUSD-only entry scope, not the loss limits or entry pause.

- A1: An explicitly configured assisted-demo default may propose market entry when entry is absent, and mandatory SL from at most $10 estimated risk (also capped by the configured risk maximum). Never silently change an explicit entry or SL, size, side, or target. Ambiguous pending/limit instructions still need clarification.
- A2: Require the configured USD demo account, allowed symbol, fresh bid/ask, positive broker tick size/value, valid lot size, and valid protection distances. Round stop distance down to stay inside the estimated cap; never enlarge risk or reduce requested size to fit. Slippage/gaps/costs can exceed the estimate.
- A3: USD TP continues to use the read-only MT5 profit calculator. The proposed automatic market/SL choices and concrete SL/TP must be visible before the single existing confirmation; calculations are not autonomous execution or a profitable strategy.
- A4: Add XAUUSD to Jarvis's entry allowlist. Model/help must derive allowed symbols from configuration, not hard-code EURUSD.
- A5: Preserve entry pause, existing positions, daily/drawdown stops, exposure limits, idempotency, quote-age checks and exact confirmation. A paused request may show a non-executable calculated preview, never a confirmation token.
- A6: No orders or existing-position mutations during connected verification. Record actual tests and remaining activation blockers.
