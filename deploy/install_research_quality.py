"""Install only the tested research fixes; preserve shadow/pause and website."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    root = Path("/home/jarvis/trade-agent")
    os.chdir(root)
    if os.geteuid() != 0:
        raise RuntimeError("Authorized service restart requires root")
    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime

    facade, _ = build_runtime()
    status = AutonomyController(facade.service).status()
    assert (
        status["mode"] == "shadow" and status["entries_paused"] and status["active_grant"] is None
    )
    source = root / "src/trade_agent"
    stage = root / "releases/research-quality-20261002"
    backup = root / "backups" / ("research-quality-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(mode=0o700)
    owner = (source / "intelligence.py").stat()
    subprocess.run(["systemctl", "stop", "trade-agent-autonomy"], check=True)
    try:
        for name in ("intelligence.py", "research_facts.py", "autonomy_policy.py"):
            target = source / name
            if target.exists():
                shutil.copy2(target, backup / name)
            temporary = source / (name + ".next")
            shutil.copy2(stage / name, temporary)
            temporary.chmod(0o644)
            os.chown(temporary, owner.st_uid, owner.st_gid)
            temporary.replace(target)
        subprocess.run(["systemctl", "restart", "trade-agent"], check=True)
    finally:
        subprocess.run(["systemctl", "start", "trade-agent-autonomy"], check=True)
    print("Installed research validation; shadow mode and pause preserved; backup " + str(backup))


if __name__ == "__main__":
    main()
