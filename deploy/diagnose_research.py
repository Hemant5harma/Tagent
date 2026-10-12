"""Tool-free model smoke test on archived evidence; logs shapes, not response text."""

import asyncio
import json
import os

from pydantic import ValidationError

from trade_agent.autonomy import AutonomyController
from trade_agent.intelligence import ResearchDecision, ResearchReport, ResearchWorkflow
from trade_agent.main import build_runtime
from trade_agent.research_ai import ResearchModel


async def main() -> None:
    os.chdir("/home/jarvis/trade-agent")
    facade, _ = build_runtime()
    runs = AutonomyController(facade.service).runs(30)
    evidence = next(r["snapshot"] for r in runs if r["snapshot"].get("bars"))

    class Observer(ResearchModel):
        async def complete(self, role: str, prompt: str, schema: dict) -> dict:
            value = await super().complete(role, prompt, schema)
            cls = ResearchDecision if role == "judge" else ResearchReport
            try:
                cls.model_validate(value)
                print(json.dumps({"role": role, "schema": "valid"}), flush=True)
            except ValidationError as exc:
                print(
                    json.dumps(
                        {
                            "role": role,
                            "keys": list(value),
                            "types": {k: type(v).__name__ for k, v in value.items()},
                            "lengths": {
                                k: len(v) for k, v in value.items() if isinstance(v, (list, str))
                            },
                            "errors": [
                                {"field": list(e["loc"]), "type": e["type"]} for e in exc.errors()
                            ],
                        }
                    ),
                    flush=True,
                )
            return value

    result = await ResearchWorkflow(facade.settings, Observer(facade.settings)).analyze(evidence)
    print(json.dumps({"direction": result["direction"], "reports": len(result["reports"])}))


if __name__ == "__main__":
    asyncio.run(main())
