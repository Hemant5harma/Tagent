"""Apply the owner's $20 risk request while preserving shadow execution state."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    root = Path('/home/jarvis/trade-agent')
    os.chdir(root)
    assert os.geteuid() == 0
    from sqlalchemy import select

    from trade_agent.autonomy import AutonomyController
    from trade_agent.db import AutonomousRunRecord
    from trade_agent.main import build_runtime

    f, _ = build_runtime()
    state = AutonomyController(f.service).status()
    assert state['mode'] == 'shadow' and state['entries_paused'] and state['active_grant'] is None
    env = root / '.env'
    backup = root / 'backups' / ('risk-20-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(env, backup / 'backend.env')
    subprocess.run(['systemctl', 'stop', 'trade-agent-autonomy', 'trade-agent'], check=True)
    try:
        with f.service.database.session() as session:
            for row in session.scalars(select(AutonomousRunRecord).where(
                AutonomousRunRecord.account_id == f.settings.account_id,
                AutonomousRunRecord.status == 'analyzing',
                AutonomousRunRecord.mode == 'shadow',
            )):
                row.status = 'interrupted'
                row.error = 'Shadow research interrupted by owner-requested risk configuration update'
        values = {'TRADE_AGENT_AUTONOMY_RISK_AMOUNT': '20', 'TRADE_AGENT_MAX_ESTIMATED_LOSS': '20'}
        lines = [line for line in env.read_text().splitlines() if line.split('=', 1)[0] not in values]
        env.write_text('\n'.join(lines + [f'{key}={value}' for key, value in values.items()]) + '\n')
        env.chmod(0o600)
    except Exception:
        shutil.copy2(backup / 'backend.env', env)
        raise
    finally:
        subprocess.run(['systemctl', 'start', 'trade-agent', 'trade-agent-autonomy'], check=True)
    print('Risk budget and shared stop-risk ceiling updated to $20; shadow/pause/grant state preserved. Backup: ' + str(backup))


if __name__ == '__main__':
    main()
