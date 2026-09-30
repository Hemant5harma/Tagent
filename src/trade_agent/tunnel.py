"""Supervise a temporary read-only dashboard tunnel; publish its changing URL locally."""

import re
import subprocess

from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.reporting import put_cache


def main() -> None:
    settings = Settings()
    db = Database(settings.database_url)
    command = [
        "/usr/local/bin/cloudflared",
        "tunnel",
        "--url",
        f"http://127.0.0.1:{settings.dashboard_port}",
        "--no-autoupdate",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                put_cache(db, "dashboard_url", {"url": match[0]})
                print(
                    "Temporary dashboard URL registered. Request /dashboard in Telegram.",
                    flush=True,
                )
        raise SystemExit(process.wait() or 1)
    finally:
        process.terminate()


if __name__ == "__main__":
    main()
