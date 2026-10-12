"""Connected read-only SPEC-002 verification; no proposals or broker mutations."""
import asyncio
import json
from decimal import Decimal

from trade_agent.ai import AIBackendResponseError, ParsedEntry, TradeProposalParse, build_ai_backend
from trade_agent.app import TelegramApplicationFacade
from trade_agent.main import build_runtime
from trade_agent.workflow import ConversationRunner

EXACT = 'i want you to open a trade for sell side lot 0.05 and take 10$ profit'


async def main():
    application, _ = build_runtime()
    assert application.service._paused(), 'Entries must remain paused'
    broker = application.service.broker

    def forbidden(*args, **kwargs):
        raise AssertionError('Broker mutations forbidden in SPEC-002 verification')

    for method in ('submit', 'modify', 'close', 'cancel'):
        setattr(broker, method, forbidden)

    async def malformed(message):
        raise AIBackendResponseError('Simulated malformed response')

    # Isolated in-memory conversation: never replace the owner's draft/checkpoint.
    runner = ConversationRunner(malformed, 'sqlite://')
    first = await runner.run('spec-002-verification', EXACT)
    assert first['requires_trade_fields'] == ['entry', 'stop_loss', 'symbol']
    print(json.dumps({'exact_message_reply': first['response'], 'missing_fields': first['requires_trade_fields']}))
    quote = broker.quote('EURUSD')
    spec = broker.symbol('EURUSD')
    stop = quote.ask + spec.point * Decimal(500)
    parsed = TradeProposalParse(intent='trade', symbol='EURUSD', side='sell', volume=Decimal('.05'),
        entry=ParsedEntry(kind='market'), stop_loss=stop, profit_target_usd=Decimal(10))
    facade = TelegramApplicationFacade(application)
    instruction = await asyncio.to_thread(facade._instruction_from_parse,
        application.settings.telegram_owner_id, parsed.model_dump())
    assert instruction.take_profit < quote.bid and instruction.profit_target_usd == 10
    print(json.dumps({'broker_calculation': 'passed', 'tp_price':str(instruction.take_profit),
        'estimated_gross_profit_usd':str(instruction.estimated_gross_profit),
        'entries_paused':application.service._paused(), 'proposals_created':0, 'broker_mutations':0}))
    # Read-only provider test: validate the repaired JSON-object schema path independently.
    provider_parse = await build_ai_backend(application.settings).parse_trade(EXACT)
    assert provider_parse.side == 'sell' and provider_parse.volume == Decimal('.05')
    assert provider_parse.take_profit is None and provider_parse.profit_target_usd == Decimal(10)
    print('Actual GLM structured extraction: passed')
    await runner.aclose()
    await facade.aclose()


if __name__ == '__main__':
    asyncio.run(main())
