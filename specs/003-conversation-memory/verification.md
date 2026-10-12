# SPEC-003 verification — 2026-09-30

## Requirement mapping

- M1/M2: `tests/test_memory_storage.py` and `test_m1_m2_restart_retains_context_for_model` cover persistent owner-scoped storage, bounded retention, replay and restart behavior.
- M3: `test_m3_history_stays_below_system_policy` checks both provider protocols: historical user/assistant messages do not become system instructions; unauthorized historical roles are discarded. Existing execution authorization remains deterministic.
- M4/M5: integration tests cover `/new`, `/forget`, audit preservation, dashboard code exclusion and memory-dump exclusion; storage tests cover credential filtering.
- M6: `tests/test_spec_003_drafts.py` and the exact screenshot integration test preserve side, lots and monetary target across follow-ups, while enforcing missing entry/SL and EURUSD-only policy.
- M7: existing confirmation, replay and risk tests remain in the full suite. Connected verification creates no proposal and calls no broker mutation.

## Results

- Full Python suite: **134 passed**, one existing Starlette/AnyIO deprecation warning.
- `python -m mypy src/trade_agent src/mt5_connector`: **30 source files passed**. Bare package-mode mypy cannot resolve the editable install's type marker on this Windows environment; explicit source paths pass.
- Ruff source/test checks passed.
- Jarvis additive PostgreSQL migration `20260930_04` applied. Source backup: `/home/jarvis/trade-agent/pre-spec003-source-20260930.tar.gz`. Existing ledger was not rewritten.
- `deploy/verify_spec_003.py` on Jarvis: a fresh database object read the persisted isolated conversation; actual Cloudflare GLM correctly recalled the equity topic. Temporary verification memory was cleared afterwards. Entries remained paused, no owner draft was changed, zero broker mutations.

## User-facing boundaries

Memory begins with new messages; raw historical inbox data is not backfilled. Store at most 12 recent exchanges (16,000 characters total) plus a bounded 4,000-character extractive recap. Individual texts are capped at 4,000 characters. Retention is 30 days, with pruning on access and hourly in the backend. This is bounded, lossy recall, not perfect permanent memory.

`/memory` displays a bounded preview of context; `/forget` clears local conversation context but not trade audit records, provider-side records, or the current draft. `/new` resets the draft without erasing chat memory. Secret filtering reduces exposure but is not a reason to send credentials in chat.

No order execution acceptance was performed for this change. No XAUUSD permission, no-SL permission, autonomy, or unpause is implied. Actual Telegram end-user follow-up after deployment remains a user acceptance check.
