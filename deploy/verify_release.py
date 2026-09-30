"""Connected read-only checks. Does not submit, modify, cancel, or close trades."""
import asyncio
import json
from datetime import UTC, datetime

import httpx

from trade_agent.dashboard import issue_code
from trade_agent.db import DashboardCacheRecord
from trade_agent.main import build_runtime
from trade_agent.reporting import Reporter, dashboard_data


def main():
    facade, _ = build_runtime()
    service = facade.service
    Reporter(service).collect()
    data = dashboard_data(service.database, '30d', 'account')
    quote = service.broker.quote('EURUSD')
    age = (datetime.now(UTC) - quote.observed_at).total_seconds()
    assert -5 <= age < 15, f'Quote freshness invalid: {age}'
    assert data['broker']['account']['is_demo']
    assert data['broker']['paused'], 'Entries must remain paused'
    with service.database.session() as session:
        url = session.get(DashboardCacheRecord, 'dashboard_url').payload['url']
    with httpx.Client(base_url=url, timeout=30) as client:
        assert client.get('/').status_code == 200
        assert client.get('/dashboard-api/snapshot').status_code == 401
        code = issue_code(service.database)
        response = client.post('/dashboard-api/login', json={'code': code})
        assert response.status_code == 200, response.status_code
        cookie = response.headers['set-cookie'].lower()
        assert all(flag in cookie for flag in ('secure', 'httponly', 'samesite=strict'))
        assert client.get('/dashboard-api/snapshot').status_code == 200
        assert client.post('/dashboard-api/login', json={'code': code}).status_code == 401
        assert client.post('/v1/proposals', json={}).status_code == 404
        assert client.post('/dashboard-api/logout').status_code == 200
        assert client.get('/dashboard-api/snapshot').status_code == 401
    state = data['broker']['state']
    print(json.dumps({
        'dashboard_url': url, 'public_auth_checks': 'passed', 'entries_paused': True,
        'quote_age_seconds': round(age, 2), 'open_positions': len(state['positions']),
        'positions_without_sl': sum(not p.get('stop_loss') for p in state['positions']),
        'pending_orders': len(state['orders']), 'account_closed_trades': data['metrics']['closed_trades'],
        'history_coverage': data['coverage'],
    }, indent=2))


if __name__ == '__main__':
    main()
