"""Install verified release files without touching broker positions or enabling entries."""
import os
import pwd
import shutil
import subprocess
import json
import psycopg
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/home/jarvis/trade-agent')


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root is required for the already authorized service upgrade')
    user = pwd.getpwnam('jarvis')
    path = ROOT / 'connector.env'
    backup = ROOT / 'backups' / ('clock-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(path, backup / 'connector.env')
    values = {
        'MT5_CONNECTOR_SERVER_TIME_OFFSET_SECONDS': '10800',
        'MT5_CONNECTOR_SERVER_TIME_VALID_FROM': '2026-03-09T00:00:00+00:00',
        # Stop before the DST transition; do not guess future broker offsets.
        'MT5_CONNECTOR_SERVER_TIME_VALID_UNTIL': '2026-10-31T00:00:00+00:00',
    }
    lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in values]
    path.write_text('\n'.join(lines + [f'{key}={value}' for key, value in values.items()]) + '\n')
    path.chmod(0o600)
    os.chown(path, user.pw_uid, user.pw_gid)
    public = Path('/opt/trade-agent-dashboard')
    shutil.copytree(ROOT / 'src/trade_agent', public / 'trade_agent', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'frontend/dist', public / 'frontend', dirs_exist_ok=True)
    roles = json.loads((ROOT / '.database-roles.json').read_text())
    with psycopg.connect(host='127.0.0.1', port=5433, dbname='trade_agent',
                         user='trade_agent', password=roles['trade_agent'], autocommit=True) as db:
        db.execute('REVOKE SELECT ON proposals,execution_intents,ledger_events FROM trade_dashboard')
        db.execute('GRANT SELECT (id,status,created_at,instruction) ON proposals TO trade_dashboard')
        db.execute('GRANT SELECT (id,status,broker_position_id) ON execution_intents TO trade_dashboard')
        db.execute('GRANT SELECT (id,proposal_id,kind,created_at) ON ledger_events TO trade_dashboard')
    subprocess.run(['runuser', '-u', 'jarvis', '--', 'env',
                    f'XDG_RUNTIME_DIR=/run/user/{user.pw_uid}', 'systemctl', '--user',
                    'restart', 'trade-agent-connector'], check=True)
    subprocess.run(['systemctl', 'restart', 'trade-agent', 'trade-agent-reporting',
                    'trade-agent-protection', 'trade-agent-dashboard'], check=True)
    print('Installed; broker positions untouched. Entry pause state retained.')


if __name__ == '__main__':
    main()
