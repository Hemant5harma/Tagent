# SPEC-005 verification — 2026-09-30

- Full Python suite: **155 passed**, one existing dependency deprecation warning. Ruff passed.
- `tests/test_spec_005.py` checks six approval phrases without parsing/quotes, the unblocked button-only path, and a latched budget record that remains unchanged. SPEC-004's paused test now expects a blocker explanation instead of prices. The existing unblocked single-proposal test still passes.
- Jarvis read-only `deploy/verify_spec_005.py`: real demo account, one open position, zero pending orders, entries paused, `breached=true`. Approval follow-up returned all three blockers. Quote/profit/mutation methods were replaced with fail-fast guards for this check: zero recalculations and zero mutations.
- Saved budget reported daily high 111561.02 and day start 110778.12; contemporaneous raw equity was 111404.20 USD. The existing implementation measures cash-flow-adjusted decline from the greater of daily starting equity or daily high, not only net daily loss. This behavior was not changed or reset here.
- Remote source backup retained as `/home/jarvis/trade-agent/pre-spec005-source-20260930.tar.gz`. No environment, budget, pause or position changes.

## Remaining activation decision

This fixes misleading interaction, not account-level activation. Another entry requires resolving the one-exposure restriction and an explicit operator decision about the latched loss evaluation and pause. Do not relax limits, close positions or reset the evaluation under cover of a UI fix. Old paused-preview behavior documented in SPEC-004 is superseded by B2.
