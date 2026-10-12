"""Install tested planning release while preserving shadow and paused entries."""

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(*args):
    subprocess.run(list(args), check=True)


def main():
    root = Path("/home/jarvis/trade-agent")
    public = Path("/opt/trade-agent-dashboard")
    stage = root / "releases" / (sys.argv[1] if len(sys.argv) > 1 else "trade-plans-20261002")
    assert stage.resolve().parent == (root / "releases").resolve()
    names = (
        "trade_plans.py",
        "autonomy.py",
        "autonomy_policy.py",
        "config.py",
        "intelligence.py",
        "research_facts.py",
        "reporting.py",
        "autonomy_health.py",
        "market_data.py",
        "app.py",
        "timeframe_comparison.py",
    )
    os.chdir(root)
    assert os.geteuid() == 0
    from sqlalchemy import select

    from trade_agent.autonomy import AutonomyController
    from trade_agent.db import AutonomousRunRecord
    from trade_agent.main import build_runtime

    facade, _ = build_runtime()
    status = AutonomyController(facade.service).status()
    assert (
        status["mode"] == "shadow" and status["entries_paused"] and status["active_grant"] is None
    )
    for name in names:
        assert (stage / "src/trade_agent" / name).is_file()
    assert (stage / "frontend/dist/index.html").is_file()
    backup = root / "backups" / (stage.name + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(mode=0o700)
    source = root / "src/trade_agent"
    shutil.copy2(root / ".env", backup / "backend.env")
    connector_source = root / "src/mt5_connector/service.py"
    shutil.copy2(connector_source, backup / "connector-service.py")
    for label, directory in (("backend", source), ("dashboard", public / "trade_agent")):
        (backup / label).mkdir()
        for name in names:
            if (directory / name).exists():
                shutil.copy2(directory / name, backup / label / name)
    shutil.copytree(public / "frontend", backup / "frontend")
    run(
        "systemctl",
        "stop",
        "trade-agent-autonomy",
        "trade-agent",
        "trade-agent-reporting",
        "trade-agent-dashboard",
    )
    try:
        with facade.service.database.session() as session:
            for row in session.scalars(
                select(AutonomousRunRecord).where(
                    AutonomousRunRecord.mode == "shadow", AutonomousRunRecord.status == "analyzing"
                )
            ):
                row.status = "interrupted"
                row.error = "Research interrupted by deployment before completion; no entry"
        owner = (source / "autonomy.py").stat()
        for directory in (source, public / "trade_agent"):
            for name in names:
                temporary = directory / (name + ".next")
                shutil.copy2(stage / "src/trade_agent" / name, temporary)
                temporary.chmod(0o644)
                if directory == source:
                    os.chown(temporary, owner.st_uid, owner.st_gid)
                temporary.replace(directory / name)
        env = root / ".env"
        lines = [
            line
            for line in env.read_text().splitlines()
            if line.split("=", 1)[0] != "TRADE_AGENT_AUTONOMY_TRADE_PLANS_ENABLED"
        ]
        env.write_text("\n".join(lines + ["TRADE_AGENT_AUTONOMY_TRADE_PLANS_ENABLED=true"]) + "\n")
        env.chmod(0o600)
        if stage.name.startswith("timeframe-comparison"):
            lines = [
                line
                for line in env.read_text().splitlines()
                if line.split("=", 1)[0] != "TRADE_AGENT_AUTONOMY_COMPARISON_ENABLED"
            ]
            env.write_text(
                "\n".join(lines + ["TRADE_AGENT_AUTONOMY_COMPARISON_ENABLED=true"]) + "\n"
            )
            shutil.copy2(stage / "src/mt5_connector/service.py", connector_source)
            os.chown(connector_source, owner.st_uid, owner.st_gid)
            run(
                "runuser",
                "-u",
                "jarvis",
                "--",
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{owner.st_uid}",
                "systemctl",
                "--user",
                "restart",
                "trade-agent-connector",
            )
        for directory in (public / "frontend", root / "frontend/dist"):
            shutil.copytree(
                stage / "frontend/dist/assets", directory / "assets", dirs_exist_ok=True
            )
            temporary = directory / "index.html.next"
            shutil.copy2(stage / "frontend/dist/index.html", temporary)
            temporary.chmod(0o644)
            temporary.replace(directory / "index.html")
            for path in (directory / "assets").rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
    except Exception:
        shutil.copy2(backup / "connector-service.py", connector_source)
        shutil.copy2(backup / "backend.env", root / ".env")
        for label, directory in (("backend", source), ("dashboard", public / "trade_agent")):
            for name in names:
                if (backup / label / name).exists():
                    shutil.copy2(backup / label / name, directory / name)
        shutil.copytree(backup / "frontend", public / "frontend", dirs_exist_ok=True)
        raise
    finally:
        run(
            "systemctl",
            "start",
            "trade-agent",
            "trade-agent-reporting",
            "trade-agent-dashboard",
            "trade-agent-autonomy",
        )
        run("systemctl", "start", "trade-agent-tunnel")
    print("Trade planning deployed in shadow mode. Backup: " + str(backup))


if __name__ == "__main__":
    main()
