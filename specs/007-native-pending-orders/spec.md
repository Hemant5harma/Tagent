# SPEC-007 — Native pending entries

P1: Explicit price-trigger requests become broker-held limit/stop orders after one exact confirmation. Never reinterpret an explicit limit/stop or rise/fall trigger after market crossing.
P2: Distinct `pending_expires_at` expires only an unfilled order; legacy `deadline_at` remains a separately requested whole-trade deadline. Relative expiry is resolved once when parsing, retained across clarification, and shown as an absolute timestamp. Without specified expiry, clearly display good-till-cancelled.
P3: Broker must support requested expiry; reject rather than silently substitute GTC. SL/TP calculation uses pending entry, not current quote. Confirmed entry/type/SL/TP/expiry are fixed.
P4: Preserve risk blockers, durable submission/idempotency and broker reconciliation. No server activation, risk reset, exposure-cap change or broker trade is authorized by this feature request.
P5: Telegram/help explains native pending creation and existing confirmed cancellation. Changes require fresh approval; pausing new submissions does not cancel existing broker orders. Do not claim full lifecycle connected acceptance without evidence.
