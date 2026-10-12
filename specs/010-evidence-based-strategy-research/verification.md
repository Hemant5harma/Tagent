# SPEC-010 verification, October 10, 2026

## Implemented now

| Requirement | Evidence |
| --- | --- |
| S1 planning unavailable fails closed before AI | `tests/test_timeframe_comparison.py::test_shadow_prefilter_preserves_failure_and_ready_model_paths[unavailable]` |
| S2 no ready plan is WAIT with zero AI calls | `tests/test_timeframe_comparison.py::test_shadow_pipeline_archives_comparison_without_orders` |
| S3 a ready plan still reaches research | `tests/test_timeframe_comparison.py::test_shadow_prefilter_preserves_failure_and_ready_model_paths[ready]` |
| S4 no execution, limit or pause change | All three tests assert no position; connected read-only check below showed no position or pending order. Source diff changes only the research prefilter. |

Local checks: `python -m pytest -q` passed **263 tests** with one dependency deprecation warning; Ruff passed for changed Python files; mypy passed for `src/trade_agent/autonomy.py`.

Jarvis deployment: the pre-change remote file hash exactly matched the local source with only this patch removed. A copy was retained at `/home/jarvis/trade-agent/src/trade_agent/autonomy.py.pre-prefilter-20261010`. The new file hash matched the tested local source. Only `trade-agent-autonomy.service` was restarted; all six trade-agent services were active afterward. A connected read-only Vantage demo check found **zero** positions and **zero** pending orders.

The connected prefilter path has **not** yet been observed on a new live market candle: both configured symbols had stale quotes during the Saturday check. That deferred observation is not a reason to enable trading.

## Research gates not yet completed

Broker-specific historical bar/tick export, cost-calibrated lot-based replay, walk-forward/untouched-holdout tests, MT5 Strategy Tester comparison and a sustained forward-shadow sample remain unbuilt or unverified. There is no demonstrated profitable strategy. The account-wide loss breaker remains latched, entries remain paused, autonomy remains shadow-only, and no grant is active. External account trades are excluded from agent performance claims.
