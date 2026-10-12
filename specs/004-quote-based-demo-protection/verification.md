# SPEC-004 verification — 2026-09-30

## Results

- Full Python suite: **147 passed**, one existing Starlette/AnyIO deprecation warning. Ruff passed; mypy passed on 31 source files.
- `tests/test_spec_004.py`: 13 cases covering A1–A5: buy/sell calculations, unchanged explicit levels, disabled defaults, stale quote/live account/wrong currency/missing tick data/excess spread/invalid volume/disallowed symbol, exact-message paused preview, pending-entry clarification and one confirmed proposal with no pre-confirmation order.
- Jarvis `deploy/verify_spec_004.py --activate-settings`: actual XAUUSD / 0.05-lot SL and MT5 USD-TP calculation passed. No broker mutations or owner-draft changes. Enabled XAUUSD in the existing allowlist and opt-in automatic protection in `.env`; all other settings preserved. Source and mode-600 environment backups retained locally on Jarvis.
- At verification: **one open position, zero pending orders, entries paused, loss circuit breaker latched**. These are activation blockers, not parser failures. No new execution acceptance or unpause was performed.

## Interpretation and boundaries

SL is calculated from the broker loss tick value, lot size and estimated-risk cap, rounded toward a smaller distance. TP uses MT5 `order_calc_profit`. These are deterministic proposal calculations, not technical-analysis-derived stop placement or guaranteed $10 loss/profit. Final broker preflight and all existing risk checks remain necessary. Explicit price/SL are preserved; no automatic lot-size reduction is permitted.

Paused previews have no executable token and are not accepted broker orders. When entries are legitimately enabled and all gates pass, the normal five-minute exact-action confirmation applies. A newer quote can invalidate the proposal at confirmation; it cannot silently enlarge approved risk.

The exact sample sentence now receives calculated levels instead of requesting entry/SL, under the explicitly enabled defaults. A pending/conditional instruction without an entry price still requires clarification. Old SPEC-003 evidence describes the previous EURUSD-only policy; SPEC-004 supersedes that allowlist restriction, not memory/safety requirements.
