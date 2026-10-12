# SPEC-010 verification

Local implementation verification on 2026-09-30. No connected test orders,
deployment changes, real account migration or credentials were accessed.

## Evidence

- Market/connector tests: closed candle selection, source/time validation,
  indicators, missing/stale data rejection, bounded requests and existing
  connector regressions.
- Research tests: 16 mocked tests covering all five roles, current provider
  profiles, malformed outputs, invalid citations, source/indicator checks,
  WAIT on failures, and Codex/OpenCode execution-tool isolation.
- Controller/policy tests: 15 tests covering shadow non-execution, durable
  duplicate claims, demo origin attribution, mandatory grant, owner mismatch,
  expiry/revocation/account/profile/mode/risk changes, daily quota, broker
  uncertainty without retry, registered strategy abstention, API authentication
  and SQLite Alembic upgrade/downgrade/upgrade, plus same-symbol exposure appearing
  during research and rejected immediately before submission.
- Reporting/replay/dashboard tests verify manual/autonomous attribution,
  conservative unknown origins, decision evidence, account drawdown labels and
  cost-aware technical-baseline replay. Frontend TypeScript and Vite build passed.
- Full Python regression suite: 226 passed, including stale/future quote checks.
  The final submission exposure recheck also passed a 24-test controller/lifecycle
  regression run and the 15-test final controller suite (one newly added test).
- Ruff over `src`, `connector`, `tests`, `migrations`: passed.
- Mypy over both source packages: 40 files passed.

Requirement mappings: A01/A07 `tests/test_autonomy_reporting.py` and existing
reporting/dashboard tests; A02 `tests/test_market_data.py` and
`connector/tests/test_market_data.py`; A03 `tests/test_intelligence.py`;
A04/A05/A06 `tests/test_autonomy.py` plus existing trade lifecycle, recovery and
risk tests; A07 `tests/test_replay.py`; A08 `docs/AUTONOMY.md` and `.env.example`.

Commands: `.venv/Scripts/python.exe -m pytest -q`,
`python -m ruff check src connector tests migrations`,
`python -m mypy src/trade_agent src/mt5_connector`; frontend `npm run build`.

## Remaining connected acceptance

PostgreSQL migration/locking and restart behavior; real provider authentication
and five-role structured output; terminal candle coverage, timestamp conversion,
quote freshness and broker tick value; official feed reachability; synchronized
history and accepted demo risk gates; protective worker and notification delivery
across process restarts. These gates are not implied by mocked/local tests.

The deterministic replay is a separate baseline. Profitability, calibrated
confidence and model-assisted historical performance have not been established.
Autonomy remains off by default; shadow does not submit orders; demo needs an
owner/account/profile/strategy grant expiring within 24 hours.

## Connected Jarvis deployment — 2026-10-02

User authorized deployment and explicitly authorized saved sudo credential reuse.
Installed staged source and PostgreSQL revision `20260930_05`; enabled systemd
autonomy worker in **shadow** mode using the existing OpenAI-compatible
`@cf/zai-org/glm-5.3` profile. Enabled optional official RSS context. No demo grant,
order submission or loss/pause reset was performed.

- Restricted backup: `/home/jarvis/trade-agent/backups/autonomy-20261002T093717Z`,
  including PostgreSQL custom dump, source/assets and protected environments.
- Backend/protection/reporting/dashboard/autonomy/tunnel and user connector all
  active after deployment. Connector needed warm-up; its initial missing/stale
  evidence runs remain honestly recorded as WAIT.
- Read-only verification fetched **200 fresh broker M15 bars** for both EURUSD
  and XAUUSD, with finite EMA/RSI/ATR and current quotes.
- Five-role research completed on both instruments. Decisions were WAIT with
  archived reasons. One earlier malformed provider report failed closed; a later
  new-bar run and isolated model smoke test passed all five schemas. No validation
  rules were relaxed or failed runs retried as orders.
- Fed/ECB/BLS/BEA RSS returned timestamped evidence. FRED remains unavailable
  because no key is configured; no complete macro/calendar coverage is claimed.
- PostgreSQL website role SELECT query and authenticated public snapshot passed.
  Actual browser login, Overview/Trades/Agent/System and 390px mobile layout
  passed, with zero JavaScript errors.
- Existing entry pause retained; eight pre-existing external positions and zero
  pending orders observed before and after. Protective service remained independent.

Helpers: `deploy/install_autonomy_release.py`, `deploy/verify_autonomy_release.py`,
`deploy/diagnose_research.py`, `frontend/tests/live-monitoring-smoke.cjs`.
No connected autonomous execution acceptance or profitability has been established.
