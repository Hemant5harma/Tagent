"""Publish reviewed static dashboard assets without restarting the tunnel or trading."""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    root = Path("/home/jarvis/trade-agent")
    public = Path("/opt/trade-agent-dashboard/frontend")
    stage = root / "releases/autonomy-ui-20261002/dist"
    if os.geteuid() != 0 or not public.is_dir() or not (stage / "index.html").is_file():
        raise RuntimeError("Approved root publication of staged dashboard assets is required")
    backup = root / "backups" / ("autonomy-ui-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(mode=0o700)
    shutil.copytree(public, backup / "frontend")
    for target in (root / "frontend/dist", public):
        shutil.copytree(stage / "assets", target / "assets", dirs_exist_ok=True)
        temporary = target / "index.html.next"
        shutil.copy2(stage / "index.html", temporary)
        temporary.chmod(0o644)
        temporary.replace(target / "index.html")
        for path in (target / "assets").rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
    print("Published readable role summaries and decisions; no service restart or trade action.")


if __name__ == "__main__":
    main()
