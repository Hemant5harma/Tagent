import json

import httpx
import pytest

from trade_agent.config import Settings
from trade_agent.intelligence import ResearchWorkflow
from trade_agent.research_ai import ResearchModel


def snapshot():
    return {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "as_of": "2026-09-30T12:00:01Z",
        "closed_bar_at": "2026-09-30T12:00:00Z",
        "data_mode": "fake",
        "bars": [
            {
                "time": "2026-09-30T11:45:00Z",
                "open": "1.1",
                "high": "1.2",
                "low": "1.0",
                "close": "1.15",
            }
        ],
        "indicators": {"ema_fast": "1.12", "ema_slow": "1.1", "rsi": "55", "atr": "0.01"},
        "quote": {"bid": "1.15", "ask": "1.151", "observed_at": "2026-09-30T12:00:01Z"},
        "evidence": [
            {
                "id": "price",
                "source": "fake",
                "observed_at": "2026-09-30T12:00:00Z",
                "text": "Closed candles and calculated indicators",
            }
        ],
    }


class Model:
    def __init__(self, *, direction="buy", citation="price", failure=None):
        self.calls = []
        self.direction, self.citation, self.failure = direction, citation, failure

    async def complete(self, role, prompt, schema):
        self.calls.append((role, prompt, schema))
        if role == self.failure:
            raise RuntimeError("secret-api-key must not leak")
        if role == "judge":
            return {
                "direction": self.direction,
                "confidence": 0.7,
                "reason": "Supported trend; macro unavailable",
                "evidence_ids": [self.citation],
            }
        return {
            "summary": "Trend evidence; macro unavailable",
            "limitations": ["No macro feed"],
            "evidence_ids": ["price"],
        }


async def test_five_agents_debate_and_ground_decision():
    model = Model()
    result = await ResearchWorkflow(Settings(_env_file=None), model).analyze(snapshot())
    assert result["direction"] == "buy"
    assert [role for role, _, _ in model.calls] == ["technical", "macro", "bull", "bear", "judge"]
    bear_prompt = model.calls[3][1]
    assert '"role": "bull"' in bear_prompt
    assert len(result["reports"]) == 5


async def test_code_verified_price_facts_are_valid_directional_evidence():
    result = await ResearchWorkflow(
        Settings(_env_file=None), Model(citation="verified-facts")
    ).analyze(snapshot())
    assert result["direction"] == "buy"


@pytest.mark.parametrize("failure", ["technical", "macro", "bull", "bear", "judge"])
async def test_provider_failure_abstains_without_leaking(failure):
    result = await ResearchWorkflow(Settings(_env_file=None), Model(failure=failure)).analyze(
        snapshot()
    )
    assert result["direction"] == "wait"
    assert "secret-api-key" not in json.dumps(result)


@pytest.mark.parametrize("direction,citation", [("BUY", "price"), ("buy", "invented")])
async def test_invalid_decision_or_citation_abstains(direction, citation):
    result = await ResearchWorkflow(
        Settings(_env_file=None), Model(direction=direction, citation=citation)
    ).analyze(snapshot())
    assert result["direction"] == "wait"


async def test_missing_price_and_nonfinite_indicators_prevent_model_calls():
    for mutation in ("missing", "nan", "duplicate"):
        data = snapshot()
        if mutation == "missing":
            data["evidence"] = []
        elif mutation == "nan":
            data["indicators"]["atr"] = "NaN"
        else:
            data["evidence"].append(data["evidence"][0])
        model = Model()
        result = await ResearchWorkflow(Settings(_env_file=None), model).analyze(data)
        assert result["direction"] == "wait"
        assert model.calls == []


async def test_only_allowlisted_snapshot_context_reaches_provider():
    data = snapshot()
    data["api_key"] = "hidden-secret"
    data["portfolio"] = {
        "account_id": "hidden-secret",
        "equity": "1000",
        "currency": "USD",
        "positions": [
            {"symbol": "EURUSD", "volume": "0.01", "profit": "-8095", "raw": "hidden-secret"}
        ],
    }
    data["quote"]["raw"] = "hidden-secret"
    data["bars"][0]["raw"] = "hidden-secret"
    model = Model()
    await ResearchWorkflow(Settings(_env_file=None), model).analyze(data)
    assert all("hidden-secret" not in prompt for _, prompt, _ in model.calls)
    assert all("-8095" not in prompt for _, prompt, _ in model.calls)
    assert '"equity": "1000"' in model.calls[0][1]


@pytest.mark.parametrize(
    "protocol,format",
    [
        ("chat_completions", "json_schema"),
        ("chat_completions", "json_object"),
        ("responses", "json_schema"),
    ],
)
async def test_openai_research_uses_selected_profile_and_schema(protocol, format):
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert request.headers["authorization"] == "Bearer test-key"
        result = '{"summary":"ok","evidence_ids":["price"],"limitations":[]}'
        return httpx.Response(
            200,
            json={"output_text": result}
            if protocol == "responses"
            else {"choices": [{"message": {"content": result}}]},
        )

    settings = Settings(
        _env_file=None,
        ai_provider="openai_compatible",
        openai_api_key="test-key",
        openai_model="chosen",
        openai_protocol=protocol,
        openai_response_format=format,
    )
    async with httpx.AsyncClient(
        base_url="https://test.invalid/v1", transport=httpx.MockTransport(handler)
    ) as client:
        value = await ResearchModel(settings, client=client).complete(
            "technical", "Research evidence", {"type": "object"}
        )
    assert value["summary"] == "ok"
    assert seen[0]["model"] == "chosen"
    assert "strict trading-message parser" not in json.dumps(seen)
    assert "tools" not in seen[0]


async def test_opencode_research_denies_tools_and_honors_model():
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "test-session"})
        return httpx.Response(200, json={"structured_output": {"summary": "ok"}})

    settings = Settings(
        _env_file=None,
        ai_provider="opencode",
        opencode_model_provider="provider",
        opencode_model="chosen",
    )
    async with httpx.AsyncClient(
        base_url="http://localhost:4096", transport=httpx.MockTransport(handler)
    ) as client:
        await ResearchModel(settings, client=client).complete(
            "technical", "Research", {"type": "object"}
        )
    assert seen[0]["permission"][0]["action"] == "deny"
    assert seen[1]["tools"] == {"*": False}
    assert seen[1]["model"] == {"providerID": "provider", "modelID": "chosen"}


async def test_disabled_provider_does_not_fallback():
    result = await ResearchWorkflow(Settings(_env_file=None, ai_provider="none")).analyze(
        snapshot()
    )
    assert result["direction"] == "wait"


@pytest.mark.parametrize("failure", ["timeout", "schema"])
async def test_transient_role_failure_retries_without_passing_bad_report(failure):
    class Flaky(Model):
        attempts = 0

        async def complete(self, role, prompt, schema):
            if role == "bear":
                self.attempts += 1
                if self.attempts == 1:
                    if failure == "timeout":
                        raise TimeoutError("private-provider-detail")
                    return {"summary": "bad response", "unexpected": "private-provider-detail"}
            return await super().complete(role, prompt, schema)

    model = Flaky()
    result = await ResearchWorkflow(Settings(_env_file=None), model).analyze(snapshot())
    assert len(result["reports"]) == 5
    assert result["research_health"]["bear"]["attempts"] == 2
    assert "private-provider-detail" not in json.dumps(result)
    assert all("private-provider-detail" not in p for _, p, _ in model.calls)


async def test_full_window_facts_survive_smaller_model_context_and_partial_failure():
    data = snapshot()
    data["bars"] = [dict(data["bars"][0], time=str(i)) for i in range(200)]
    model = Model(failure="judge")
    result = await ResearchWorkflow(Settings(_env_file=None), model).analyze(data)
    context = json.loads(
        model.calls[0][1].split("\nSnapshot:\n")[1].split("\nResearch reports:\n")[0]
    )
    assert len(context["bars"]) == context["model_bar_count"] == 24
    assert context["verified_facts"]["bar_count"] == len(data["bars"]) == 200
    assert "Verified by code: 200" in result["reports"][0]["output"]["summary"]


async def test_codex_research_isolated_read_only_and_closes_client(monkeypatch, tmp_path):
    import trade_agent.research_ai as provider_module

    calls = []
    started = []
    closed = []
    # Point at an empty authentication home; tests never touch actual credentials.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("TRADE_AGENT_MT5_CONNECTOR_TOKEN", "broker-secret")

    async def subprocess(*args, **kwargs):
        started.append((args, kwargs))
        return object()

    class RPC:
        def __init__(self, process, timeout):
            pass

        async def request(self, method, params):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "thread"}}
            if method == "turn/start":
                return {"turn": {"id": "turn"}}
            return {}

        async def notify(self, method, params):
            pass

        async def wait_for_agent_message(self, turn_id):
            return '{"summary":"ok"}'

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(provider_module.asyncio, "create_subprocess_exec", subprocess)
    monkeypatch.setattr(provider_module, "_CodexJsonRpcClient", RPC)
    model = ResearchModel(Settings(_env_file=None, ai_provider="codex", codex_model="chosen"))
    result = await model.complete("technical", "Research only", {"type": "object"})
    assert result == {"summary": "ok"}
    assert "TRADE_AGENT_MT5_CONNECTOR_TOKEN" not in started[0][1]["env"]
    assert started[0][1]["cwd"] != str(tmp_path)
    turn = next(params for method, params in calls if method == "turn/start")
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert turn["approvalPolicy"] == "never"
    assert closed == [True]
