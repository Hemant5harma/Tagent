# SPEC-002 — Safe trade clarification and monetary profit targets

Status: implemented and deployed; unit and connected read-only verification passed. Demo execution remains paused. Owner request: fix the screenshot's incomplete sell request and establish spec-driven development.

## Requirements

- R1: `i want you to open a trade for sell side lot 0.05 and take 10$ profit` preserves sell, 0.05 lots and USD 10 target, then asks together for symbol, entry and stop loss. No parser traceback or broker mutation.
- R2: Missing/ambiguous data is never invented. A monetary profit target is a distinct typed field (`profit_target_usd`), never a TP price. Ambiguous units, multiple candidate values, unsupported currencies or conflicting price/money targets require clarification.
- R3: JSON-object providers receive the actual extraction schema. Invalid structured responses cannot authorize an action; bounded deterministic extraction may recover only explicitly recognizable fields for clarification. Provider outages retain a safe user-facing error.
- R4: Once all other fields are explicit, USD targets are converted using the broker's read-only profit calculator for the actual USD demo account. Invalid/stale quotes, missing specifications, wrong account/currency or unavailable calculations block the proposal. No language-model arithmetic determines TP.
- R5: The single confirmation includes the fixed computed TP price and requested estimated gross USD target. Fees, slippage and conversion changes can alter realized profit. Confirmation never recomputes or silently changes that approved TP. Existing risk/pause/expiry/deduplication rules remain mandatory.
- R6: New drafts do not inherit old monetary targets. Revisions between price and money targets clear the replaced representation. Account questions preserve a pending draft. A malformed provider response cannot turn a dollars target into a price.
- R7: Chat explains the distinction between a non-executing model and the backend's human-confirmed execution capability. Current entry pause remains unchanged.

## Out of scope

Real money, autonomy, guaranteed net profit, multi-currency money targets, changing existing positions, clearing circuit breakers or resuming entries. Deployment smoke checks must disable broker mutations and use an isolated test conversation.

## Acceptance and traceability

Implementation, tests and live read-only evidence are recorded in `verification.md`. A test is required for each requirement before its status can be marked verified. Passing unit tests is not connected execution acceptance.
