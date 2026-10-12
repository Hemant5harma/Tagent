# SPEC-007 verification — 2026-09-30

- Full workspace suite: **169 passed**, one existing Starlette/AnyIO deprecation warning; mypy passed on 33 modules, Ruff passed.
- New tests cover all four explicit pending types, rise-trigger mapping, relative expiry, ambiguous-expiry clarification, expiry preservation across follow-ups, entry-based SL calculation, crossed-order rejection, and connector supported/unsupported expiry without GTC substitution.
- Feature-only deployment excludes SPEC-006: no config, exposure limit, service reconciliation acknowledgement, reset script, or ledger reset changes were deployed. The deployment risk module was built from the current remote version plus only the pending-type/expiry and verified-hedging checks. Source backup retained on Jarvis.
- Actual Vantage demo `order_check` accepted a read-only XAUUSD sell-limit request with two-hour broker expiry, SL and TP. Account reports hedging=true. `deadline_at` remained null. Entries remained paused; zero broker mutations and no execution proposal created.
- Backend, reporting/protection and connector require the new contract together. No dashboard/tunnel changes needed; existing order views remain in use.

## Limits

This is broker preflight evidence, not proof of a submitted, filled, expired or cancelled demo order. End-to-end lifecycle execution remains gated by activation approval. Submission cannot bypass pause, loss limits or account exposure limits. Unsupported expiry is rejected, not silently downgraded. No-expiry requests explicitly display GTC for approval. Existing legacy whole-trade deadlines retain their original meaning.

Pause does not cancel already accepted broker orders. An automatic breaker-triggered cancellation policy and complex locally queued conditions are not enabled in this release. Pending changes can be made by a confirmed cancellation followed by a fresh approved order; no unconfirmed mutation is added.

Example: `Sell limit at 4250 XAUUSD 0.05 lots target $10 profit expires in 2 hours`. This is a syntax example, not a recommendation or authorization to trade.
