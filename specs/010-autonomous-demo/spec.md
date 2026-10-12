# SPEC-010 — Measurable multi-agent demo trading

Implementation scope: local code and fake-broker tests. This spec does not authorize deployment, live-money trading, or placing connected test orders.

- A01: Preserve the manual confirmation workflow. Record manual, autonomous, and unknown/external origins independently from broker ownership. Do not retroactively claim autonomous performance.
- A02: Collect bounded, closed MT5 candles through an authenticated read-only connector endpoint. Validate timestamps, coverage and finite prices; compute numerical indicators in code. Archive the evidence used by each run.
- A03: Use the configured AI provider for technical and macro analysis, bull/bear challenge and a structured decision. Provider failures, missing required evidence and invalid decisions abstain. Models have no execution tools or credentials.
- A04: Shadow is the default. Autonomous broker submission requires an explicit expiring owner/account/demo policy grant and enabled deployment configuration. Grants cannot enlarge deterministic deployment limits. Global pause, demo budget breaker and unknown execution outcomes block entries.
- A05: Deduplicate a strategy/symbol/closed-bar run durably. Never resubmit an uncertain or interrupted decision. Revalidate current quote and authorization immediately before submission. Initial autonomy opens market entries with mandatory broker-held SL/TP and a bounded deadline; it does not change manual positions.
- A06: Persist evidence, reports, decisions, grant attribution, strategy/provider version, outcomes and proposal references. Expose authenticated run/status/grant/revoke operations and a scheduled worker.
- A07: Separate manual/autonomous performance, costs and outcomes on the read-only dashboard; account-wide limits still count both. Evaluate chronological replay with transaction costs and clear historical-data limitations.
- A08: Document installation, migration, provider/data prerequisites, shadow smoke test, grant activation and rollback. Include free official feeds/macro options without promising complete historical news or premium event consensus.

Acceptance: regression tests cover grant expiry/revocation, mode/account/owner mismatch, duplicate closed bars, model failure, stale/missing data, risk limits, shadow non-execution, manual confirmation preservation, attribution and recovery. Connected provider/MT5 and PostgreSQL acceptance are recorded separately from local tests.
