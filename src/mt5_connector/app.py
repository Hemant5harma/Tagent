"""Authenticated, loopback-only FastAPI surface for the MT5 connector."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status

from mt5_connector.contracts import (
    CancelRequest,
    CloseRequest,
    ModifyRequest,
    MutationResponse,
    PreflightRequest,
    ProfitTargetRequest,
    SubmissionResponse,
    SubmitOrderRequest,
)
from mt5_connector.journal import IdempotencyConflict, JSONLRequestJournal
from mt5_connector.service import ConnectorError, MT5Service, TerminalUnavailable
from mt5_connector.settings import ConnectorSettings


def _fingerprint(request: SubmitOrderRequest) -> str:
    body = request.model_dump(mode="json", exclude={"idempotency_key"})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mutation_fingerprint(operation: str, target: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "target": target, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_app(
    settings: ConnectorSettings | None = None,
    service: MT5Service | None = None,
    journal: JSONLRequestJournal | None = None,
) -> FastAPI:
    settings = settings or ConnectorSettings.from_env()
    service = service or MT5Service(settings)
    journal = journal or JSONLRequestJournal(settings.journal_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        gateway = getattr(service, "_gateway", None)
        if gateway is not None:
            gateway.shutdown()

    app = FastAPI(
        title="Trade Agent MT5 Connector",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.journal = journal

    def authorize(
        x_connector_token: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_connector_token is None or not secrets.compare_digest(
            x_connector_token, settings.token
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    def connector_error(exc: ConnectorError) -> HTTPException:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if isinstance(exc, TerminalUnavailable) else 400
        return HTTPException(status_code=code, detail=str(exc))

    def mutation(
        *,
        operation: str,
        target: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
        execute: Any,
    ) -> dict[str, Any]:
        """Persist every exit/modification before it reaches the MT5 terminal."""
        fingerprint = _mutation_fingerprint(operation, target, payload)
        if idempotency_key is not None and not 16 <= len(idempotency_key) <= 256:
            raise HTTPException(
                status_code=422, detail="idempotency key must be 16 to 256 characters"
            )
        effective_key = idempotency_key or f"auto-{fingerprint}"
        journal_key = f"mutation:{operation}:{effective_key}"
        try:
            decision = journal.begin(journal_key, fingerprint)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if decision.kind == "completed":
            return decision.result or {}
        if decision.kind == "unknown":
            return MutationResponse(
                status="unknown",
                reason="previous mutation outcome is unknown; reconcile before retrying",
            ).model_dump(mode="json", exclude_none=True)

        try:
            execute()
            result = MutationResponse(status="accepted")
        except TerminalUnavailable as exc:
            result = MutationResponse(status="unknown", reason=str(exc))
        except ConnectorError as exc:
            result = MutationResponse(status="rejected", reason=str(exc))
        except Exception:  # noqa: BLE001 - a terminal exception leaves the mutation outcome unknown
            result = MutationResponse(
                status="unknown",
                reason="connector lost the mutation outcome; reconcile before retrying",
            )
        response = result.model_dump(mode="json", exclude_none=True)
        journal.complete(journal_key, fingerprint, response)
        return response

    @app.get("/healthz", dependencies=[Depends(authorize)])
    def health(response: Response) -> dict[str, Any]:
        result = service.health()
        if result["status"] != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @app.get("/v1/capabilities", dependencies=[Depends(authorize)])
    def capabilities() -> dict[str, bool | int]:
        try:
            return service.capabilities()
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.post("/v1/profit-target", dependencies=[Depends(authorize)])
    def profit_target(request: ProfitTargetRequest) -> dict[str, str]:
        try:
            return service.profit_target(request)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/accounts/{account_id}", dependencies=[Depends(authorize)])
    def account(account_id: str) -> dict[str, Any]:
        try:
            return service.account(account_id)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/symbols/{symbol}", dependencies=[Depends(authorize)])
    def symbol(symbol: str) -> dict[str, Any]:
        try:
            return service.symbol(symbol)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/quotes/{symbol}", dependencies=[Depends(authorize)])
    def quote(symbol: str) -> dict[str, Any]:
        try:
            return service.quote(symbol)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/bars/{symbol}", dependencies=[Depends(authorize)])
    def bars(
        symbol: str, timeframe: str = "M15", count: int = Query(200, ge=50, le=1000)
    ) -> dict[str, Any]:
        try:
            return {"bars": service.bars(symbol, timeframe, count)}
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/research/bars/{symbol}", dependencies=[Depends(authorize)])
    def historical_bars(
        symbol: str, since: datetime, until: datetime, timeframe: str = "M15"
    ) -> dict[str, Any]:
        try:
            return service.historical_bars(symbol, timeframe, since, until)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/research/ticks/{symbol}", dependencies=[Depends(authorize)])
    def historical_ticks(symbol: str, since: datetime, until: datetime) -> dict[str, Any]:
        try:
            return service.historical_ticks(symbol, since, until)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.post("/v1/preflight", dependencies=[Depends(authorize)])
    def preflight(payload: PreflightRequest) -> dict[str, Any]:
        try:
            return service.preflight(payload.instruction, payload.order_type).model_dump(
                mode="json"
            )
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.post("/v1/orders", dependencies=[Depends(authorize)])
    def submit(payload: SubmitOrderRequest) -> dict[str, Any]:
        fingerprint = _fingerprint(payload)
        try:
            decision = journal.begin(payload.idempotency_key, fingerprint)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if decision.kind == "completed":
            return decision.result or {}
        if decision.kind == "unknown":
            return SubmissionResponse(
                accepted=False,
                status="unknown",
                reason="previous submission result is unknown; reconcile before retrying",
            ).model_dump(mode="json")

        try:
            result = service.submit(
                payload.instruction, payload.order_type, payload.idempotency_key
            )
        except Exception:  # noqa: BLE001 - an unhandled MT5 exception makes execution unknown
            result = SubmissionResponse(
                accepted=False,
                status="unknown",
                reason="connector lost the submission outcome; reconcile before retrying",
            )
        response = result.model_dump(mode="json")
        journal.complete(payload.idempotency_key, fingerprint, response)
        return response

    @app.post("/v1/modify", dependencies=[Depends(authorize)])
    def modify(
        payload: ModifyRequest,
        x_idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        wire_payload = payload.model_dump(mode="json", exclude={"idempotency_key"})
        target = payload.order_id or payload.position_id or "invalid-target"
        return mutation(
            operation="modify",
            target=target,
            payload=wire_payload,
            idempotency_key=payload.idempotency_key or x_idempotency_key,
            execute=lambda: service.modify(payload),
        )

    @app.post(
        "/v1/orders/{order_id}/cancel",
        dependencies=[Depends(authorize)],
    )
    def cancel(
        order_id: str,
        payload: CancelRequest | None = None,
        x_idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        payload = payload or CancelRequest()
        return mutation(
            operation="cancel",
            target=order_id,
            payload={},
            idempotency_key=payload.idempotency_key or x_idempotency_key,
            execute=lambda: service.cancel(order_id),
        )

    @app.post(
        "/v1/positions/{position_id}/close",
        dependencies=[Depends(authorize)],
    )
    def close(
        position_id: str,
        payload: CloseRequest | None = None,
        x_idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        payload = payload or CloseRequest()
        return mutation(
            operation="close",
            target=position_id,
            payload=payload.model_dump(mode="json", exclude={"idempotency_key"}),
            idempotency_key=payload.idempotency_key or x_idempotency_key,
            execute=lambda: service.close(position_id, payload.volume),
        )

    @app.get("/v1/reconciliation/{idempotency_key}", dependencies=[Depends(authorize)])
    def reconciliation(idempotency_key: str) -> dict[str, Any]:
        try:
            record = journal.lookup_operation(idempotency_key)
            if record and str(record["key"]).startswith("mutation:"):
                result = record.get("result") or {}
                return {
                    "idempotency_key": idempotency_key,
                    "status": result.get("status", "unknown"),
                    "journal_status": record["status"],
                    "journal_result": result,
                    "order_ids": [],
                    "deal_ids": [],
                    "position_ids": [],
                }
            return service.reconcile_submission(idempotency_key, record)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/accounts/{account_id}/state", dependencies=[Depends(authorize)])
    def state(account_id: str) -> dict[str, Any]:
        try:
            return service.state(account_id)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    @app.get("/v1/accounts/{account_id}/history", dependencies=[Depends(authorize)])
    def history(account_id: str, since: datetime, until: datetime) -> dict[str, Any]:
        try:
            return service.history(account_id, since, until)
        except ConnectorError as exc:
            raise connector_error(exc) from exc

    return app
