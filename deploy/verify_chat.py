"""Verify real account questions without accepting any broker mutation."""
import asyncio
import json

from telegram import Bot

from trade_agent.app import TelegramApplicationFacade
from trade_agent.db import DashboardCacheRecord
from trade_agent.main import build_runtime


async def main():
    application, _ = build_runtime()
    assert application.service._paused(), 'Keep entries paused'

    def forbidden(*args, **kwargs):
        raise AssertionError('No broker mutation is permitted by this check')

    for name in ('submit', 'modify', 'cancel', 'close'):
        setattr(application.service.broker, name, forbidden)
    facade = TelegramApplicationFacade(application)
    try:
        for question in ('do we have any open trades ?', 'what is my account balance?'):
            response = await facade.create_trade_from_text(
                owner_id=application.settings.telegram_owner_id, text=question)
            assert response.confirmation_token is None
            assert 'parser unavailable' not in response.text.lower(), response.text
            print(json.dumps({'question': question, 'answer': response.text}))
        async with Bot(application.settings.telegram_bot_token) as bot:
            identity = await bot.get_me()
            webhook = await bot.get_webhook_info()
            print(json.dumps({'telegram_bot': identity.username,
                              'polling_mode': not bool(webhook.url)}))
        with application.service.database.session() as session:
            checkpoints = {}
            for key in ('telegram', 'protection', 'limits', 'dashboard_url'):
                row = session.get(DashboardCacheRecord, key)
                checkpoints[key] = {'status': row.payload.get('status'),
                                    'observed_at': row.updated_at.isoformat()} if row else None
            print(json.dumps({'services': checkpoints, 'broker_mutations': 0}))
    finally:
        await facade.aclose()


if __name__ == '__main__':
    asyncio.run(main())
