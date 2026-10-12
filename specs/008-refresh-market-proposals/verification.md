# SPEC-008 verification — 2026-09-30

The saved failed proposal was an XAUUSD 0.05 lot sell: bid 4174.70, SL 4176.37, estimated stop loss $8.35. It was confirmed about 30 seconds after creation and rejected as the fixed stop exceeded the $10 cap at the newer quote. It was not submitted. The later fresh-message preparation also encountered a moving-quote risk rejection.

- Full workspace suite: **174 passed**, one existing dependency deprecation warning. Ruff and mypy (33 source modules) passed.
- Focused tests cover a new confirmation token after a pre-submission quote rejection, no duplicate replacement on replay, bounded preparation retry, explicit user stop protection, and read-only broker failure classified as rejected rather than unknown submission.
- The backend now checks the approved market-quote tolerance before detailed risk validation. If a quote/risk drift rejects a proposal, only a deterministic fresh proposal can be prepared; it needs its own Telegram Confirm button. The rejected proposal remains rejected and cannot submit later. A broker submission timeout still stays unknown and is not refreshed automatically.
- The synchronous rejection reply is no longer duplicated by an outbox rejection notification. Audit events and execution intent status remain durable.
- Deployment included only app, broker HTTP serialization, domain and service source. Source backup: `/home/jarvis/trade-agent/pre-spec008-source-20260930.tar.gz`. Backend restarted; no user trade or broker mutation was initiated for deployment.

This improves recovery, but no market order can be guaranteed after a moving quote without changing the approved entry tolerance or risk terms. A fast market may produce another fresh button. New demo entry acceptance must be confirmed by an actual user-directed order; the agent did not place a test trade on the owner's behalf.
