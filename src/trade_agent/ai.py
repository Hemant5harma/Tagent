"""Non-executing adapters for AI-assisted trade instruction parsing.

Providers are deliberately limited to turning a user message into a typed,
untrusted draft.  They never receive broker credentials and this module has no
dependency on the execution service or the broker boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trade_agent.config import Settings


class AIBackendError(RuntimeError):
    """A provider could not return a trustworthy structured parse."""


class AIBackendUnavailable(AIBackendError):
    """The selected provider is unavailable or has not been configured."""


class AIBackendResponseError(AIBackendError):
    """The provider answered, but not with the promised schema."""


class ParsedEntry(BaseModel):
    """Entry intent extracted from a message without deciding an order type."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["market", "price", "missing"]
    price: Decimal | None = None

    @model_validator(mode="after")
    def validate_price(self) -> ParsedEntry:
        if self.kind == "price" and self.price is None:
            raise ValueError("a price entry needs price")
        if self.kind != "price" and self.price is not None:
            raise ValueError("only a price entry may include price")
        return self


class TradeProposalParse(BaseModel):
    """Untrusted, structured extraction from a user's natural-language request.

    The application service owns all business validation.  In particular, a
    parse is not a :class:`TradeInstruction` and can never be submitted.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Literal["trade", "clarification", "not_trade"]
    symbol: str | None = Field(default=None, max_length=32)
    side: Literal["buy", "sell"] | None = None
    volume: Decimal | None = Field(default=None, gt=Decimal("0"))
    entry: ParsedEntry
    stop_loss: Decimal | None = Field(default=None, gt=Decimal("0"))
    take_profit: Decimal | None = Field(default=None, gt=Decimal("0"))
    deadline_at: str | None = Field(
        default=None,
        description="Only an explicit ISO-8601 timestamp which includes its UTC offset.",
    )
    note: str | None = Field(default=None, max_length=512)
    missing_fields: list[
        Literal["symbol", "side", "volume", "entry", "stop_loss", "take_profit", "deadline"]
    ] = Field(default_factory=list)
    clarification: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def normalize_and_check(self) -> TradeProposalParse:
        if self.symbol is not None:
            self.symbol = self.symbol.strip().upper() or None
        if len(set(self.missing_fields)) != len(self.missing_fields):
            raise ValueError("missing_fields must not contain duplicates")
        if (
            self.intent == "trade"
            and self.entry.kind == "missing"
            and "entry" not in self.missing_fields
        ):
            raise ValueError("missing entry must be declared in missing_fields")
        return self


class AIBackend(ABC):
    """A provider that can only parse a requested trade into structured data."""

    name: str

    @abstractmethod
    async def parse_trade(self, user_text: str) -> TradeProposalParse:
        """Return an untrusted parse; this operation cannot execute a trade."""


_PARSE_SCHEMA = TradeProposalParse.model_json_schema()
_PARSER_INSTRUCTIONS = """You are a strict trading-message parser. Extract only values explicitly
provided by the user message. Do not give advice, market analysis, order-type
selection, position sizing, or execution instructions. A numeric entry is
entry.kind='price'; the deterministic application will resolve its pending
order type later. Use entry.kind='market' only when the user explicitly asks
for a market order. Use entry.kind='missing' when the user has not supplied an
entry instruction. List every required missing trade field in missing_fields.
A deadline is allowed only when the user supplied an unambiguous ISO-8601 date
and UTC offset; otherwise list deadline as missing if a deadline was requested.
Do not call tools, inspect files, run commands, or make network requests. You
are only authorized to return the JSON parse.
Return only a JSON object matching the supplied schema."""


def _prompt(user_text: str) -> str:
    if not user_text.strip():
        raise AIBackendResponseError("cannot parse an empty message")
    return f"{_PARSER_INSTRUCTIONS}\n\nUser message:\n{user_text.strip()}"


def _parse_json(value: str | dict[str, Any]) -> TradeProposalParse:
    try:
        if isinstance(value, str):
            return TradeProposalParse.model_validate_json(value)
        return TradeProposalParse.model_validate(value)
    except (ValueError, TypeError) as exc:
        raise AIBackendResponseError("provider returned an invalid structured trade parse") from exc


def _error_message(response: httpx.Response) -> str:
    """Return a safe error without echoing provider payloads or credentials."""

    return f"provider request failed with HTTP {response.status_code}"


class DisabledAIBackend(AIBackend):
    name = "none"

    async def parse_trade(self, user_text: str) -> TradeProposalParse:
        del user_text
        raise AIBackendUnavailable("AI parsing is disabled; use the structured trade command")


class OpenAICompatibleBackend(AIBackend):
    """OpenAI Responses or Chat Completions compatible structured-output client."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str | None,
        protocol: Literal["chat_completions", "responses"],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._protocol = protocol
        self._client = client

    async def parse_trade(self, user_text: str) -> TradeProposalParse:
        if not self._api_key or not self._model:
            raise AIBackendUnavailable(
                "OpenAI-compatible provider needs API key and model configuration"
            )
        payload = self._payload(_prompt(user_text))
        headers = {"Authorization": f"Bearer {self._api_key}"}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(30.0)
        )
        try:
            response = await client.post(self._endpoint, json=payload, headers=headers)
            if response.is_error:
                raise AIBackendUnavailable(_error_message(response))
            return _parse_json(self._extract_content(response.json()))
        except httpx.HTTPError as exc:
            raise AIBackendUnavailable("OpenAI-compatible provider is unavailable") from exc
        finally:
            if owns_client:
                await client.aclose()

    @property
    def _endpoint(self) -> str:
        return "/responses" if self._protocol == "responses" else "/chat/completions"

    def _payload(self, prompt: str) -> dict[str, Any]:
        if self._protocol == "responses":
            return {
                "model": self._model,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "trade_proposal_parse",
                        "strict": True,
                        "schema": _PARSE_SCHEMA,
                    }
                },
            }
        return {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "trade_proposal_parse",
                    "strict": True,
                    "schema": _PARSE_SCHEMA,
                },
            },
        }

    def _extract_content(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        if self._protocol == "chat_completions":
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise AIBackendResponseError(
                    "provider response did not include a chat completion"
                ) from exc
            return _content_to_text(content)

        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
        for output in payload.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    return content["text"]
        raise AIBackendResponseError("provider response did not include structured output")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
        if text:
            return text
    raise AIBackendResponseError("provider response content was not text")


class OpenCodeBackend(AIBackend):
    """OpenCode HTTP API adapter using its validated `json_schema` output format."""

    name = "opencode"

    def __init__(
        self,
        *,
        base_url: str,
        model_provider: str | None,
        model: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        _require_loopback_opencode_url(base_url)
        self._base_url = base_url.rstrip("/")
        self._model_provider = model_provider
        self._model = model
        self._client = client

    async def parse_trade(self, user_text: str) -> TradeProposalParse:
        if bool(self._model_provider) != bool(self._model):
            raise AIBackendUnavailable(
                "OpenCode model provider and model must be configured together"
            )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(45.0)
        )
        try:
            created = await client.post("/session", json={"title": "trade-agent-parse"})
            if created.is_error:
                raise AIBackendUnavailable(_error_message(created))
            session_id = _opencode_session_id(created.json())
            request: dict[str, Any] = {
                "parts": [{"type": "text", "text": _prompt(user_text)}],
                "format": {"type": "json_schema", "schema": _PARSE_SCHEMA, "retryCount": 1},
            }
            if self._model_provider and self._model:
                request["model"] = {"providerID": self._model_provider, "modelID": self._model}
            response = await client.post(f"/session/{session_id}/message", json=request)
            if response.is_error:
                raise AIBackendUnavailable(_error_message(response))
            return _parse_json(_opencode_structured_output(response.json()))
        except httpx.HTTPError as exc:
            raise AIBackendUnavailable("OpenCode server is unavailable") from exc
        finally:
            if owns_client:
                await client.aclose()


def _opencode_session_id(payload: Any) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("id"), str):
            return payload["id"]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return data["id"]
    raise AIBackendResponseError("OpenCode did not return a session id")


def _require_loopback_opencode_url(value: str) -> None:
    """OpenCode must be the dedicated local parser service, never a remote host."""

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "::1", "localhost"}:
        raise AIBackendUnavailable("OpenCode URL must point to a loopback-only local server")


def _opencode_structured_output(payload: Any) -> dict[str, Any] | str:
    if not isinstance(payload, dict):
        raise AIBackendResponseError("OpenCode returned an invalid response")
    candidates: list[Any] = [
        payload.get("structured_output"),
        payload.get("structuredOutput"),
        payload.get("info", {}).get("structured_output")
        if isinstance(payload.get("info"), dict)
        else None,
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("structured_output"),
                data.get("structuredOutput"),
                data.get("info", {}).get("structured_output")
                if isinstance(data.get("info"), dict)
                else None,
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, (dict, str)):
            return candidate
    raise AIBackendResponseError("OpenCode response did not include structured output")


class CodexAppServerBackend(AIBackend):
    """One-shot, read-only Codex app-server JSON-RPC parser.

    Each invocation starts an ephemeral app-server process.  It deliberately
    starts no broker-facing tools, sets a read-only no-approval policy, and only
    accepts a JSON parse from the final agent message.
    """

    name = "codex"

    def __init__(self, *, command: str, model: str | None, timeout_seconds: float = 60.0) -> None:
        self._command = command
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def parse_trade(self, user_text: str) -> TradeProposalParse:
        # Never give Codex the backend's database, broker, Telegram, or model
        # environment. Copy only its authentication record into an ephemeral
        # CODEX_HOME, so global MCP/plugin configuration is not loaded.
        with tempfile.TemporaryDirectory(prefix="trade-agent-codex-") as cwd:
            isolated_home = Path(cwd) / ".codex"
            isolated_home.mkdir(mode=0o700)
            source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
            source_auth = source_home / "auth.json"
            if source_auth.is_file():
                shutil.copyfile(source_auth, isolated_home / "auth.json")
            try:
                process = await asyncio.create_subprocess_exec(
                    self._command,
                    "app-server",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=cwd,
                    env=_codex_environment(isolated_home),
                )
            except OSError as exc:
                raise AIBackendUnavailable("Codex app-server could not be started") from exc

            client = _CodexJsonRpcClient(process, self._timeout_seconds)
            try:
                await client.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "trade-agent",
                            "title": "Trade Agent",
                            "version": "0.1.0",
                        }
                    },
                )
                await client.notify("initialized", {})
                thread_params: dict[str, Any] = {}
                if self._model:
                    thread_params["model"] = self._model
                thread = await client.request("thread/start", thread_params)
                thread_id = _codex_thread_id(thread)
                turn = await client.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": _prompt(user_text)}],
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    },
                )
                turn_id = _codex_turn_id(turn)
                return _parse_json(await client.wait_for_agent_message(turn_id))
            except TimeoutError as exc:
                raise AIBackendUnavailable("Codex app-server timed out") from exc
            finally:
                await client.close()


def _codex_environment(isolated_home: Path | None = None) -> dict[str, str]:
    """Keep only OS/runtime variables needed to start Codex and read its own auth."""
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "TEMP",
        "TERM",
        "TMP",
        "USERPROFILE",
        "WINDIR",
        "XDG_CONFIG_HOME",
    }
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    if isolated_home is not None:
        environment["CODEX_HOME"] = str(isolated_home)
    return environment


def _codex_thread_id(payload: Any) -> str:
    if isinstance(payload, dict):
        thread = payload.get("thread")
        if isinstance(thread, dict) and isinstance(thread.get("id"), str):
            return thread["id"]
    raise AIBackendResponseError("Codex app-server did not return a thread id")


def _codex_turn_id(payload: Any) -> str:
    if isinstance(payload, dict):
        turn = payload.get("turn")
        if isinstance(turn, dict) and isinstance(turn.get("id"), str):
            return turn["id"]
    raise AIBackendResponseError("Codex app-server did not return a turn id")


class _CodexJsonRpcClient:
    def __init__(self, process: asyncio.subprocess.Process, timeout_seconds: float) -> None:
        self._process = process
        self._timeout_seconds = timeout_seconds
        self._request_id = 0
        self._agent_messages: dict[str, str] = {}

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        await self._write({"method": method, "id": request_id, "params": params})
        while True:
            message = await self._read_message()
            if message.get("id") != request_id:
                self._consume_notification(message)
                continue
            if isinstance(message.get("error"), dict):
                raise AIBackendUnavailable("Codex app-server rejected the request")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AIBackendResponseError("Codex app-server returned an invalid result")
            return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def wait_for_agent_message(self, turn_id: str) -> str:
        while True:
            message = await self._read_message()
            self._consume_notification(message)
            if message.get("method") != "turn/completed":
                continue
            params = message.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            if not isinstance(turn, dict) or turn.get("id") != turn_id:
                continue
            if turn.get("status") != "completed":
                raise AIBackendUnavailable("Codex did not complete the parse")
            try:
                return self._agent_messages.pop(turn_id)
            except KeyError as exc:
                raise AIBackendResponseError("Codex completed without a parser response") from exc

    def _consume_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") != "item/completed":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            return
        turn_id = params.get("turnId") or item.get("turnId")
        text = item.get("text")
        if isinstance(turn_id, str) and isinstance(text, str):
            self._agent_messages[turn_id] = text

    async def _write(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise AIBackendUnavailable("Codex app-server stdin is unavailable")
        self._process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await asyncio.wait_for(self._process.stdin.drain(), timeout=self._timeout_seconds)

    async def _read_message(self) -> dict[str, Any]:
        if self._process.stdout is None:
            raise AIBackendUnavailable("Codex app-server stdout is unavailable")
        line = await asyncio.wait_for(
            self._process.stdout.readline(), timeout=self._timeout_seconds
        )
        if not line:
            raise AIBackendUnavailable("Codex app-server exited before responding")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AIBackendResponseError("Codex app-server emitted invalid JSON-RPC") from exc
        if not isinstance(message, dict):
            raise AIBackendResponseError("Codex app-server emitted a non-object message")
        return message

    async def close(self) -> None:
        if self._process.returncode is None:
            self._process.terminate()
            with suppress(ProcessLookupError, asyncio.TimeoutError):
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
        if self._process.returncode is None:
            self._process.kill()
            with suppress(ProcessLookupError):
                await self._process.wait()


def build_ai_backend(settings: Settings) -> AIBackend:
    """Build exactly the explicitly selected provider profile."""

    if settings.ai_provider == "none":
        return DisabledAIBackend()
    if settings.ai_provider == "openai_compatible":
        return OpenAICompatibleBackend(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            protocol=settings.openai_protocol,
        )
    if settings.ai_provider == "opencode":
        return OpenCodeBackend(
            base_url=settings.opencode_url,
            model_provider=settings.opencode_model_provider,
            model=settings.opencode_model,
        )
    if settings.ai_provider == "codex":
        return CodexAppServerBackend(command=settings.codex_command, model=settings.codex_model)
    raise ValueError(f"unknown AI provider: {settings.ai_provider}")
