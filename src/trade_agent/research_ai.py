"""Tool-free structured research using the explicitly configured AI profile."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from trade_agent.ai import (
    AIBackendUnavailable,
    OpenAICompatibleBackend,
    _codex_environment,
    _codex_thread_id,
    _codex_turn_id,
    _CodexJsonRpcClient,
    _opencode_session_id,
    _opencode_structured_output,
    _require_loopback_opencode_url,
)
from trade_agent.config import Settings


class ResearchModel:
    """No broker imports, execution tools, or provider fallback."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def complete(self, role: str, prompt: str, schema: dict) -> dict:
        async with asyncio.timeout(min(max(self.settings.openai_timeout_seconds, 1), 90)):
            if self.settings.ai_provider == "openai_compatible":
                result = await self._openai(role, prompt, schema)
            elif self.settings.ai_provider == "opencode":
                result = await self._opencode(role, prompt, schema)
            elif self.settings.ai_provider == "codex":
                result = await self._codex(prompt, schema)
            else:
                raise AIBackendUnavailable("Research AI provider is disabled")
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise ValueError("Research provider returned a non-object")
        return result

    async def _post(self, base: str, path: str, payload: dict, headers: dict | None = None) -> Any:
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60)
        try:
            response = await client.post(path, json=payload, headers=headers)
            if response.is_error:
                raise AIBackendUnavailable(f"Research provider HTTP {response.status_code}")
            return response.json()
        finally:
            if owns_client:
                await client.aclose()

    async def _openai(self, role: str, prompt: str, schema: dict) -> Any:
        s = self.settings
        if not s.openai_api_key or not s.openai_model:
            raise AIBackendUnavailable("Research provider needs its configured key and model")
        if s.openai_protocol == "responses":
            payload: dict[str, Any] = {
                "model": s.openai_model,
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": role,
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
            if s.openai_max_completion_tokens:
                payload["max_output_tokens"] = s.openai_max_completion_tokens
            if s.openai_reasoning_effort:
                payload["reasoning"] = {"effort": s.openai_reasoning_effort}
            endpoint = "/responses"
        else:
            payload = {"model": s.openai_model, "messages": [{"role": "user", "content": prompt}]}
            if s.openai_response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
                payload["messages"][0]["content"] += "\nJSON schema: " + json.dumps(schema)
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": role, "strict": True, "schema": schema},
                }
            if s.openai_reasoning_effort:
                payload["reasoning_effort"] = s.openai_reasoning_effort
            if s.openai_max_completion_tokens:
                payload["max_completion_tokens"] = s.openai_max_completion_tokens
            endpoint = "/chat/completions"
        response = await self._post(
            s.openai_base_url, endpoint, payload, {"Authorization": f"Bearer {s.openai_api_key}"}
        )
        # Reuse response extraction only, never the manual parser's instructions.
        extractor = OpenAICompatibleBackend(
            base_url=s.openai_base_url,
            api_key=s.openai_api_key,
            model=s.openai_model,
            protocol=s.openai_protocol,
        )
        return extractor._extract_content(response)

    async def _opencode(self, role: str, prompt: str, schema: dict) -> Any:
        s = self.settings
        _require_loopback_opencode_url(s.opencode_url)
        if bool(s.opencode_model_provider) != bool(s.opencode_model):
            raise AIBackendUnavailable("Configure both OpenCode model fields")
        session = await self._post(
            s.opencode_url,
            "/session",
            {
                "title": f"research-{role}",
                "permission": [{"permission": "*", "pattern": "*", "action": "deny"}],
            },
        )
        session_id = _opencode_session_id(session)
        request = {
            "parts": [{"type": "text", "text": prompt}],
            "tools": {"*": False},
            "format": {"type": "json_schema", "schema": schema, "retryCount": 0},
        }
        if s.opencode_model_provider and s.opencode_model:
            request["model"] = {
                "providerID": s.opencode_model_provider,
                "modelID": s.opencode_model,
            }
        return _opencode_structured_output(
            await self._post(s.opencode_url, f"/session/{session_id}/message", request)
        )

    async def _codex(self, prompt: str, schema: dict) -> Any:
        with tempfile.TemporaryDirectory(prefix="trade-research-codex-") as cwd:
            isolated_home = Path(cwd) / ".codex"
            isolated_home.mkdir(mode=0o700)
            auth = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
            if auth.is_file():
                shutil.copyfile(auth, isolated_home / "auth.json")
            process = await asyncio.create_subprocess_exec(
                self.settings.codex_command,
                "app-server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=_codex_environment(isolated_home),
            )
            client = _CodexJsonRpcClient(process, 60)
            try:
                await client.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "trade-research",
                            "title": "Trade Research",
                            "version": "0.1.0",
                        }
                    },
                )
                await client.notify("initialized", {})
                params = {"model": self.settings.codex_model} if self.settings.codex_model else {}
                thread = await client.request("thread/start", params)
                turn = await client.request(
                    "turn/start",
                    {
                        "threadId": _codex_thread_id(thread),
                        "input": [
                            {
                                "type": "text",
                                "text": prompt + "\nReturn JSON schema: " + json.dumps(schema),
                            }
                        ],
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    },
                )
                return await client.wait_for_agent_message(_codex_turn_id(turn))
            finally:
                await client.close()
