# SPEC-002 verification — 2026-09-30

## Requirement-to-test map

| Requirement | Automated evidence | Result |
| --- | --- | --- |
| R1 exact incomplete sell request | `tests/test_spec_002.py::test_r1_exact_screenshot_groups_only_missing_fields` | Pass: sell, 0.05 lots, USD 10 retained; symbol/entry/SL requested together |
| R2 price versus monetary units | `test_r2_money_is_not_price`, `test_r2_ambiguous_or_conflicting_targets_require_clarification`, `test_r2_invalid_monetary_units_cannot_become_positive_usd` | Pass: dollars never become a TP price; invalid/conflicting currency targets require clarification |
| R3 malformed provider response | `test_r3_invalid_response_recovers_explicit_partial_fields`, `test_r3_malformed_complete_response_cannot_become_approval`, `test_r3_provider_receives_actual_schema` | Pass: full schema supplied; conservative partial recovery; no approval from malformed complete response |
| R4 broker conversion | `connector/tests/test_profit_target.py`, `test_r4_stale_quote_blocks_calculation` | Pass: authenticated read-only calculation; USD demo identity; bounds, tick alignment, invalid/unavailable calculations and currency guards |
| R5 explicit fixed-price confirmation | `test_r4_r5_broker_conversion_is_explicit_in_persisted_confirmation` | Pass: broker-derived TP and gross-dollar estimate persisted and displayed; existing confirmation/risk/expiry tests remain regression gates |
| R6 draft isolation/replacement | `test_r6_fresh_screenshot_draft_does_not_inherit_symbol_or_stop`, `test_r6_price_revision_replaces_money_target`; existing chat-routing tests | Pass: new draft isolation, price replaces money target, account questions retain drafts |
| R7 pause and capability distinction | `test_r7_incomplete_message_does_not_unpause_or_create_order`; `ai.py` conversational system instruction | Pass: no mutation/unpause; model explains that the backend can act only after exact human confirmation |

Unqualified `test_*` names above are in `tests/test_spec_002.py`.

## Commands and results

- `pytest -q`: **102 passed**, one third-party Starlette/AnyIO deprecation warning.
- `ruff check src tests connector/tests`: passed.
- `mypy src`: passed, 29 source files.
- Existing dashboard UI unchanged; its prior five browser tests are historical regression evidence, not a new browser run for this backend-only change.

## Connected read-only evidence

`deploy/verify_spec_002.py` ran on Jarvis against the configured Vantage MT5 USD demo account. It uses an isolated in-memory conversation, explicitly disables all four broker mutation methods, and never calls proposal creation or confirmation.

- Exact screenshot message returned: “Understood: sell, 0.05 lots; target $10 estimated gross profit. Please provide: entry, stop_loss, symbol.”
- Read-only broker calculation for 0.05 EURUSD sell and USD 10 returned a valid tick-aligned TP with estimated gross profit USD 10. The transient price is deliberately not a suggested trade or reusable quote.
- A separate actual GLM call with that same message passed the corrected JSON schema and preserved the monetary target.
- `proposals_created=0`, `broker_mutations=0`, `entries_paused=true`.
- Backend, connector, reporting and protection services were restarted to load the fix. Existing positions were not changed.

The MT5 calculation uses account currency and current market conditions; this was checked against [MetaQuotes order_calc_profit documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py). Gross estimates do not include fees or guarantee realized returns.

## Remaining release gates

This fixes extraction/clarification and pre-confirmation calculation. It does not clear the existing loss latch, authorize changing external positions, prove connected order execution/closure, or authorize live money. Those gates remain in the demo-to-live roadmap. No SDD framework/plugin is claimed to be installed: this is an explicit repository-native spec → tests → implementation → evidence workflow.
