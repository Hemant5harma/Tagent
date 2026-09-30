"""Idempotent Jarvis-only provisioning. Run as root with the project's Python.

No secret values are printed. Existing broker/Telegram/model credentials stay on Jarvis.
The legacy ledger is backed up and retained. Entries remain paused for smoke acceptance.
"""

import json
import os
import pwd
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/home/jarvis/trade-agent")


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def save(path, value, owner="jarvis", mode=0o600):
    path.write_text(value)
    user = pwd.getpwnam(owner)
    os.chown(path, user.pw_uid, user.pw_gid)
    path.chmod(mode)


def env_read(path):
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key] = value.strip().strip("\"'")
    return result


def main():
    if os.geteuid() != 0 or not ROOT.is_dir():
        raise RuntimeError("Run as root on the verified Jarvis project")
    os.chdir(ROOT)
    run(["systemctl", "stop", "trade-agent.service"])
    config = env_read(ROOT / ".env")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "backups" / stamp
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(ROOT / ".env", backup / "backend.env")
    source_db = ROOT / "data/vantage_demo_26094634.db"
    with sqlite3.connect(source_db) as source, sqlite3.connect(backup / source_db.name) as target:
        source.backup(target)
    (backup / source_db.name).chmod(0o600)

    secret_file = ROOT / ".postgres.env"
    if not secret_file.exists():
        save(
            secret_file,
            f"POSTGRES_USER=trade_agent_admin\nPOSTGRES_DB=trade_agent\nPOSTGRES_PASSWORD={secrets.token_urlsafe(36)}\n",
        )
    credentials = env_read(secret_file)
    exists = (
        subprocess.run(
            ["docker", "container", "inspect", "trade-agent-postgres"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )
    if not exists:
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "trade-agent-postgres",
                "--restart",
                "unless-stopped",
                "--env-file",
                str(secret_file),
                "-p",
                "127.0.0.1:5433:5432",
                "-v",
                "trade-agent-pgdata:/var/lib/postgresql/data",
                "postgres:17",
            ],
            stdout=subprocess.DEVNULL,
        )
    else:
        run(["docker", "start", "trade-agent-postgres"], stdout=subprocess.DEVNULL)
    import psycopg
    from psycopg import sql

    for attempt in range(60):
        try:
            admin = psycopg.connect(
                host="127.0.0.1",
                port=5433,
                dbname="trade_agent",
                user=credentials["POSTGRES_USER"],
                password=credentials["POSTGRES_PASSWORD"],
                autocommit=True,
            )
            break
        except psycopg.OperationalError:
            if attempt == 59:
                raise
            time.sleep(1)
    role_file = ROOT / ".database-roles.json"
    roles = (
        json.loads(role_file.read_text())
        if role_file.exists()
        else {
            "trade_agent": secrets.token_urlsafe(36),
            "trade_dashboard": secrets.token_urlsafe(36),
        }
    )
    save(role_file, json.dumps(roles))
    for role, password in roles.items():
        if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
    admin.execute("GRANT USAGE, CREATE ON SCHEMA public TO trade_agent")
    url = f"postgresql+psycopg://trade_agent:{roles['trade_agent']}@127.0.0.1:5433/trade_agent"
    migration_env = {**os.environ, **config, "TRADE_AGENT_DATABASE_URL": url}
    run([sys.executable, "-m", "alembic", "upgrade", "head"], env=migration_env)
    if config.get("TRADE_AGENT_DATABASE_URL", "").startswith("sqlite"):
        migration_env["LEDGER_SOURCE_URL"] = f"sqlite:///{source_db}"
        run([sys.executable, "deploy/migrate_ledger.py"], env=migration_env)
    config.update(
        TRADE_AGENT_DATABASE_URL=url,
        TRADE_AGENT_ASSISTED_DEMO_ACCEPTED="true",
        TRADE_AGENT_ALLOWED_SYMBOLS="EURUSD",
        TRADE_AGENT_MAX_ESTIMATED_LOSS="10",
        TRADE_AGENT_MAX_OPEN_POSITIONS="1",
        TRADE_AGENT_MAX_PENDING_ORDERS="1",
        TRADE_AGENT_MAX_PENDING_SUBMISSIONS="1",
        TRADE_AGENT_DEMO_DAILY_LOSS_LIMIT="50",
        TRADE_AGENT_DEMO_DRAWDOWN_LIMIT="300",
        TRADE_AGENT_TRADING_MODE="demo",
        TRADE_AGENT_OPENAI_TIMEOUT_SECONDS="60",
    )
    save(ROOT / ".env", "\n".join(f"{key}={value}" for key, value in config.items()) + "\n")
    with psycopg.connect(url.replace("postgresql+psycopg", "postgresql")) as connection:
        connection.execute(
            "INSERT INTO system_state(key,value,updated_at) VALUES('entries_paused','true',now()) ON CONFLICT(key) DO UPDATE SET value='true',updated_at=now()"
        )
    admin.execute("GRANT USAGE ON SCHEMA public TO trade_dashboard")
    admin.execute(
        "GRANT SELECT ON dashboard_cache,broker_deals,equity_snapshots,agent_activity TO trade_dashboard"
    )
    admin.execute("REVOKE SELECT ON proposals,execution_intents,ledger_events FROM trade_dashboard")
    admin.execute("GRANT SELECT (id,status,created_at,instruction) ON proposals TO trade_dashboard")
    admin.execute("GRANT SELECT (id,status,broker_position_id) ON execution_intents TO trade_dashboard")
    admin.execute("GRANT SELECT (id,proposal_id,kind,created_at) ON ledger_events TO trade_dashboard")
    admin.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON dashboard_access TO trade_dashboard")
    admin.close()

    if subprocess.run(
        ["id", "trade-dashboard"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode:
        run(
            [
                "useradd",
                "--system",
                "--home",
                "/opt/trade-agent-dashboard",
                "--shell",
                "/usr/sbin/nologin",
                "trade-dashboard",
            ]
        )
    public = Path("/opt/trade-agent-dashboard")
    public.mkdir(exist_ok=True)
    shutil.copytree(
        ROOT / "src/trade_agent",
        public / "trade_agent",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(ROOT / "frontend/dist", public / "frontend", dirs_exist_ok=True)
    if not (public / "runtime/bin/python").exists():
        run(["/usr/bin/python3", "-m", "venv", str(public / "runtime")])
    run(
        [
            str(public / "runtime/bin/python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            "fastapi>=0.116,<1",
            "uvicorn>=0.35,<1",
            "sqlalchemy>=2,<3",
            "psycopg[binary]>=3.2,<4",
            "pydantic-settings>=2.10,<3",
        ]
    )
    save(
        public / "dashboard.env",
        f"TRADE_AGENT_DATABASE_URL=postgresql+psycopg://trade_dashboard:{roles['trade_dashboard']}@127.0.0.1:5433/trade_agent\nTRADE_AGENT_DASHBOARD_DIST=/opt/trade-agent-dashboard/frontend\n",
        owner="root",
    )

    for name in [
        "trade-agent-protection",
        "trade-agent-reporting",
        "trade-agent-dashboard",
        "trade-agent-tunnel",
    ]:
        shutil.copyfile(
            ROOT / f"deploy/systemd/{name}.service", Path(f"/etc/systemd/system/{name}.service")
        )
    run(["systemctl", "daemon-reload"])
    run(
        [
            "systemctl",
            "enable",
            "--now",
            "trade-agent-protection",
            "trade-agent-reporting",
            "trade-agent-dashboard",
            "trade-agent-tunnel",
        ]
    )
    run(["systemctl", "start", "trade-agent"])
    print(
        "PostgreSQL migrated and verified; dashboard isolated; entries remain paused for connected smoke acceptance."
    )


if __name__ == "__main__":
    main()
