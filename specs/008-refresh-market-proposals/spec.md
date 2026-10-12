# SPEC-008 — Fresh approval after market movement

The owner saw a market XAUUSD proposal rejected when the quote moved during the 30 seconds before confirmation, followed by another failure while preparing a replacement. Preserve the $10 estimated per-trade cap and all account limits.

- Q1: A confirmation never submits an order outside its approved quote tolerance or risk limit. If an auto-protected market proposal is rejected before submission for quote drift, mark it rejected and offer a newly calculated proposal with a **new** Confirm button, if still eligible. No automatic broker submission.
- Q2: Distinguish an automatically chosen SL from an explicit user SL with durable metadata. Never silently recompute a user-specified SL, size, side, or entry.
- Q3: When preparing a new auto-protected market proposal, retry a small bounded number of times if the quote moves between calculation and risk validation. No proposal or broker mutation is created on a failed attempt. Explain that a fast market may still need a fresh confirmation.
- Q4: In confirmation, check the approved quote tolerance **before** validating risk so a large move gets an accurate message. Still validate all risk gates before submission.
- Q5: Replay/double-tap/unknown submission remain idempotent. Do not offer a replacement if the broker outcome is unknown, if the account is paused, or if any non-price risk blocker is present.
