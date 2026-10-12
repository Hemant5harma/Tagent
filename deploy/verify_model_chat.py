"""Non-trading, credential-free conversational provider health check."""

import asyncio
import json
import time

from trade_agent.ai import build_ai_backend
from trade_agent.main import build_runtime


async def main() -> None:
    app, _ = build_runtime()
    backend = build_ai_backend(app.settings)
    started = time.monotonic()
    try:
        result = await backend.explain(
            "Reply with a short greeting only.", json.dumps({"account": {}, "state": {}})
        )
    except Exception as exc:
        print(json.dumps({
            "status": "error", "type": type(exc).__name__,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }))
    else:
        print(json.dumps({
            "status": "ok", "elapsed_seconds": round(time.monotonic() - started, 1),
            "nonempty_reply": bool(result.strip()),
        }))


if __name__ == "__main__":
    asyncio.run(main())
