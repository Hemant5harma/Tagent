# SPEC-005 — Actionable blockers, no repeated price-preview loop

- B1: Approval-like messages (`okay open this trade`, `open this trade`, `yes`) never reparse a draft, cancel a pending proposal or recalculate prices. A valid proposal still requires its exact Telegram confirmation button.
- B2: Before preparing complete entry instructions, report persisted pause/loss-breaker and current account exposure blockers. Do not generate a price preview or token when blocked. Broker read failures are explicit verification blockers, never assumed zero exposure.
- B3: Report all known blockers with concrete next steps. Clearing chat memory, repeating approval, or resuming the poller cannot clear account risk restrictions.
- B4: Keep risk checks at actual confirmation unchanged. No breaker reset, exposure-policy relaxation, position closure or unpause is authorized by this implementation. Verify current account state read-only and request explicit operator direction for material policy changes.
- B5: Existing unblocked proposal creation still provides one exact confirmation. No changes to existing open positions during rollout.
