# SPEC-003 — Persistent conversation context and safe draft follow-ups

Status: implemented; verification recorded separately. Authorization: owner requested persistent Telegram conversation context and explicitly approved sending sanitized recent Telegram messages, replies and bounded recap to the existing Cloudflare GLM provider. `/forget` cannot retract provider-side data. Existing entry pause, EURUSD-only scope, mandatory SL and loss limits remain unchanged.

## Requirements

- M1: Persist sanitized user/assistant exchanges per account and authorized owner in PostgreSQL. Commit memory with the durable outgoing reply, so retry/restart cannot duplicate an exchange. Existing trade audit records are not repurposed as model memory.
- M2: Provide bounded recent turns plus a compact, deterministic extractive recap of older turns to the conversational model. Recap is explicitly lossy historical text, not verified facts or permission. Apply bounded size and 30-day retention. Do not retroactively import raw old inbox messages, which may contain secrets.
- M3: Current broker evidence and deterministic safety policy outrank remembered discussion. Memory cannot approve orders, change allowed symbols/risk/SL requirements, or make stale account numbers current. Dashboard remains read-only; confirmation is in private Telegram.
- M4: `/memory` shows what is supplied as conversation context; `/forget` clears that memory and does not re-record its contents. Trading audit and current draft are separate and remain; `/new` clears the draft but preserves normal conversation history. State these semantics to the owner.
- M5: Redact known credentials, credential-like text, login codes and URLs before memory storage/provider use. Do not store dashboard codes or `/memory` dumps in memory. Empty-memory operation and model outages must not block account/status/pause or protective workers.
- M6: Follow-up edits refer to the current incomplete draft, preserving explicit sell/lot/USD target fields from the screenshot. `symbol is XAUUSD` updates the symbol, not a new generic conversation. `given price` does not invent an entry. An explicit request for no SL clears any draft SL and explains that SL is mandatory; XAUUSD is outside the approved EURUSD-only policy. Never pretend these unsupported choices are executable.
- M7: Existing exact confirmation, expiry, replay protection, and isolated fresh drafts remain regression gates. No existing position is changed, no circuit breaker cleared, and entries remain paused during rollout.

## Deliberate limits

No unlimited/permanent verbatim recall, vector database, speculative long-term preference inference or autonomous actions. Recent turns plus an extractive recap are predictable and auditable for this single-owner assistant. Semantic summarization/retrieval may be added only with its own evaluation; it must not silently turn model interpretations into trading instructions.

## Evidence

Requirement-to-test mappings and actual rollout results belong in `verification.md`. Implementation is not execution acceptance.
