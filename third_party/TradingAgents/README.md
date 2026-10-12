# TradingAgents source attribution

Upstream: https://github.com/TauricResearch/TradingAgents
Reviewed revision: `8b22d43d01d9ddda5d686d093d5385884622f3de`.

`bull_researcher.py` and `bear_researcher.py` are verbatim reference copies of
`tradingagents/agents/researchers/` at that revision, retained with the upstream
Apache-2.0 LICENSE. They are not imported and do not require the TradingAgents
package. `src/trade_agent/intelligence.py` adapts their bull/bear prompts and
sequential opponent-argument debate into an asynchronous structured workflow.
Changes: FX/metals rather than corporate fundamentals, timestamped evidence,
strict citation/schema validation, a WAIT decision, and no trading tools.

This is a narrow source adaptation, not the full TradingAgents graph or a
reproduction of its paper's experiments.
