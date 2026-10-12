"""Install the reviewed Jarvis release in shadow mode. Requires approved sudo.

Never submits orders, resets loss state, changes broker secrets, or resumes entries.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path("/home/jarvis/trade-agent")
STAGE = ROOT / "releases/autonomy-monitoring-20261001"
PUBLIC = Path("/opt/trade-agent-dashboard")


def run(args: list[str], **kwargs) -> None:
    subprocess.run(args, check=True, **kwargs)


def main() -> None:
    if os.geteuid() != 0 or not ROOT.is_dir() or not PUBLIC.is_dir():
        raise RuntimeError("Approved root installation on Jarvis is required")
    if not (STAGE / "src/trade_agent/autonomy.py").is_file():
        raise RuntimeError("Reviewed stage is missing")
    os.chdir(ROOT)
    os.umask(0o077)
    backup = ROOT / "backups" / ("autonomy-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(parents=True, mode=0o700)
    for name in ("src", "migrations", "frontend/dist"):
        shutil.copytree(ROOT / name, backup / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        PUBLIC / "trade_agent",
        backup / "dashboard-code",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(PUBLIC / "frontend", backup / "dashboard-frontend")
    shutil.copy2(ROOT / ".env", backup / "backend.env")
    shutil.copy2(PUBLIC / "dashboard.env", backup / "dashboard.env")
    with (backup / "ledger.dump").open("wb") as stream:
        run(
            [
                "docker",
                "exec",
                "trade-agent-postgres",
                "sh",
                "-c",
                'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc',
            ],
            stdout=stream,
        )
    (backup / "ledger.dump").chmod(0o600)
    roles = json.loads((ROOT / ".database-roles.json").read_text())
    connection = psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="trade_agent",
        user="trade_agent",
        password=roles["trade_agent"],
        autocommit=True,
    )
    before = connection.execute(
        "SELECT value FROM system_state WHERE key='entries_paused'"
    ).fetchone()
    (backup / "deployment-state.json").write_text(json.dumps({"entries_paused": before}))
    print(f"Backup complete: {backup}", flush=True)
    run(["systemctl", "stop", "trade-agent", "trade-agent-reporting", "trade-agent-dashboard"])
    installed = False
    try:
        # Schema is additive; old protection remains active during migration.
        shutil.copy2(
            STAGE / "migrations/versions/20260930_05_autonomy.py",
            ROOT / "migrations/versions/20260930_05_autonomy.py",
        )
        run(
            [
                "runuser",
                "-u",
                "jarvis",
                "--",
                str(ROOT / ".venv/bin/python"),
                "-m",
                "alembic",
                "upgrade",
                "head",
            ]
        )
        for name in ("src", "third_party", "docs", "specs", "frontend/dist"):
            shutil.copytree(
                STAGE / name,
                ROOT / name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copy2(STAGE / "pyproject.toml", ROOT / "pyproject.toml")
        shutil.copytree(
            ROOT / "src/trade_agent",
            PUBLIC / "trade_agent",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copytree(ROOT / "frontend/dist", PUBLIC / "frontend", dirs_exist_ok=True)
        # No grant-table access or confirmation-token access for the website role.
        connection.execute("GRANT SELECT ON autonomous_runs TO trade_dashboard")
        connection.execute(
            "GRANT SELECT (origin,strategy_version,decision_run_id) ON proposals TO trade_dashboard"
        )
        connection.execute(
            "GRANT SELECT (proposal_id,broker_order_id) ON execution_intents TO trade_dashboard"
        )
        changes = {
            "TRADE_AGENT_AUTONOMY_MODE": "shadow",
            "TRADE_AGENT_AUTONOMY_CONTEXT_ENABLED": "true",
        }
        env_path = ROOT / ".env"
        lines = [
            line
            for line in env_path.read_text().splitlines()
            if line.split("=", 1)[0] not in changes
        ]
        env_path.write_text(
            "\n".join(lines + [f"{key}={value}" for key, value in changes.items()]) + "\n"
        )
        env_path.chmod(0o600)
        owner = pwd.getpwnam("jarvis")
        os.chown(env_path, owner.pw_uid, owner.pw_gid)
        for base in (
            ROOT / "src",
            ROOT / "migrations",
            ROOT / "third_party",
            ROOT / "docs",
            ROOT / "specs",
            ROOT / "frontend/dist",
        ):
            for path in (base, *base.rglob("*")):
                os.chown(path, owner.pw_uid, owner.pw_gid)
        for base in (PUBLIC / "trade_agent", PUBLIC / "frontend"):
            for path in (base, *base.rglob("*")):
                path.chmod(0o755 if path.is_dir() else 0o644)
        shutil.copy2(
            STAGE / "deploy/systemd/trade-agent-autonomy.service",
            Path("/etc/systemd/system/trade-agent-autonomy.service"),
        )
        Path("/etc/systemd/system/trade-agent-autonomy.service").chmod(0o644)
        run(["systemctl", "daemon-reload"])
        after = connection.execute(
            "SELECT value FROM system_state WHERE key='entries_paused'"
        ).fetchone()
        if after != before:
            raise RuntimeError("Pause state changed unexpectedly; research will not start")
        run(
            [
                "runuser",
                "-u",
                "jarvis",
                "--",
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{owner.pw_uid}",
                "systemctl",
                "--user",
                "restart",
                "trade-agent-connector",
            ]
        )
        run(["systemctl", "restart", "trade-agent-protection"])
        run(
            [
                "systemctl",
                "start",
                "trade-agent",
                "trade-agent-reporting",
                "trade-agent-dashboard",
                "trade-agent-tunnel",
            ]
        )
        run(["systemctl", "enable", "--now", "trade-agent-autonomy"])
        installed = True
        print(
            "Installed: shadow research; pause/risk limits retained; no demo grant or trade submission.",
            flush=True,
        )
    finally:
        connection.close()
        if not installed:
            # Retain the additive database schema, restore old runnable code/config.
            run(
                ["systemctl", "stop", "trade-agent-autonomy"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for name in ("src", "frontend/dist"):
                shutil.copytree(backup / name, ROOT / name, dirs_exist_ok=True)
            shutil.copy2(backup / "backend.env", ROOT / ".env")
            shutil.copytree(backup / "dashboard-code", PUBLIC / "trade_agent", dirs_exist_ok=True)
            shutil.copytree(backup / "dashboard-frontend", PUBLIC / "frontend", dirs_exist_ok=True)
            run(
                [
                    "systemctl",
                    "start",
                    "trade-agent",
                    "trade-agent-reporting",
                    "trade-agent-dashboard",
                    "trade-agent-protection",
                ]
            )
            print(
                "Installation failed; prior code/config restored. Additive migration retained.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
