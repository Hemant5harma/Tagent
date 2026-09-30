"""Read-only model checks against the existing explicitly approved GLM provider."""
import asyncio
import json
import time

from trade_agent.ai import build_ai_backend
from trade_agent.main import build_runtime
from trade_agent.reporting import now, put_cache


async def main():
    facade, _ = build_runtime()
    backend = build_ai_backend(facade.settings)
    state = facade.service.broker_state()
    account = state['account']
    evidence = {
        'observed_at': now().isoformat(), 'current_time': now().isoformat(),
        'account': {key: account[key] for key in ('balance','equity','margin_free','currency','is_demo')},
        'masked_account_id': '***' + account['account_id'][-4:],
        'state': state['state'], 'entries_paused': state['paused'],
    }
    start = time.monotonic()
    text = await backend.explain('Explain equity versus balance and whether new entries are paused. Do not trade.', json.dumps(evidence))
    elapsed = int((time.monotonic() - start) * 1000)
    assert text.strip()
    put_cache(facade.service.database, 'model', {'status':'ok','latency_ms':elapsed})
    parsed = await backend.parse_trade('Buy EURUSD 0.01 lots at market, stop loss 1.05000, take profit 1.25000. This is a parser test, not authorization to trade.')
    assert parsed.symbol == 'EURUSD' and str(parsed.volume) == '0.01'
    print(json.dumps({'chat_reply':text, 'chat_latency_ms':elapsed,
                      'structured_parse':'passed', 'broker_mutations':0}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
