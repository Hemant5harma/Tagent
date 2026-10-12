"""Loopback-first HTTP surface for monitoring and future interfaces."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from trade_agent.app import ApplicationFacade
from trade_agent.autonomy_policy import GrantRequest
from trade_agent.domain import Side, TradeInstruction
from trade_agent.risk import RiskRejected
from trade_agent.service import ProposalError
from trade_agent.worker import TradeWorker


class TradeRequest(BaseModel):
    owner_id: str
    symbol: str
    side: Side
    volume: Decimal = Field(gt=0)
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    deadline_at: datetime | None = None

    def instruction(self, account_id: str) -> TradeInstruction:
        return TradeInstruction(
            owner_id=self.owner_id,
            account_id=account_id,
            symbol=self.symbol,
            side=self.side,
            volume=self.volume,
            entry_price=self.entry_price,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            deadline_at=self.deadline_at,
        )


class ConfirmationRequest(BaseModel):
    owner_id: str
    version: int
    confirmation_token: str


class ProtectionProposalRequest(BaseModel):
    owner_id: str
    order_id: str | None = None
    position_id: str | None = None
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)


class ResearchRunRequest(BaseModel):
    owner_id: str
    symbol: str
    mode: str = "shadow"


class AutonomyOwnerRequest(BaseModel):
    owner_id: str


def create_api(facade: ApplicationFacade, worker: TradeWorker | None = None) -> FastAPI:
    def require_api_access(request: Request) -> None:
        expected = getattr(facade.settings, "http_api_token", None)
        supplied = request.headers.get("X-Trade-Agent-Token")
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="HTTP trading API is disabled until TRADE_AGENT_HTTP_API_TOKEN is configured",
            )
        if supplied is None or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid API token")

    def require_owner(owner_id: str) -> None:
        expected = facade.settings.telegram_owner_id
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="trading owner is not configured",
            )
        if not secrets.compare_digest(owner_id, str(expected)):
            raise HTTPException(status_code=403, detail="owner is not authorized")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if worker is not None:
            await worker.start()
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()

    app = FastAPI(title="Trade Agent", version="0.1.0", lifespan=lifespan)

    from trade_agent.autonomy import AutonomyController

    autonomy = AutonomyController(facade.service)

    @app.get("/v1/autonomy/status", dependencies=[Depends(require_api_access)])
    def autonomy_status() -> dict:
        return autonomy.status()

    @app.get("/v1/autonomy/runs", dependencies=[Depends(require_api_access)])
    def autonomy_runs(limit: int = 30) -> list[dict]:
        return autonomy.runs(limit)

    @app.post("/v1/autonomy/run", dependencies=[Depends(require_api_access)])
    async def autonomy_run(request: ResearchRunRequest) -> dict:
        require_owner(request.owner_id)
        try:
            return await autonomy.run(request.owner_id, request.symbol, mode=request.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/autonomy/grants", dependencies=[Depends(require_api_access)])
    def autonomy_grant(request: GrantRequest) -> dict:
        require_owner(request.owner_id)
        try:
            return autonomy.create_grant(request)
        except (ValueError, RiskRejected) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/autonomy/revoke", dependencies=[Depends(require_api_access)])
    def autonomy_revoke(request: AutonomyOwnerRequest) -> dict:
        require_owner(request.owner_id)
        return autonomy.revoke(request.owner_id)

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready() -> dict[str, str]:
        facade.service.broker_state()
        return {"status": "ready"}

    @app.post("/v1/proposals")
    def create_proposal(request: TradeRequest, _: None = Depends(require_api_access)) -> dict:
        try:
            require_owner(request.owner_id)
            return facade.create_trade(request.instruction(facade.settings.account_id)).model_dump(
                mode="json"
            )
        except (RiskRejected, ValueError) as exc:
            raise fail(exc) from exc

    @app.get("/v1/proposals/{proposal_id}")
    def get_proposal(proposal_id: str, _: None = Depends(require_api_access)) -> dict:
        try:
            proposal = facade.service.proposal(proposal_id)
            require_owner(proposal.instruction.owner_id)
            response = proposal.model_dump(mode="json")
            response.pop("confirmation_token", None)
            return response
        except ProposalError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/v1/proposals/{proposal_id}")
    def revise_proposal(
        proposal_id: str,
        request: TradeRequest,
        _: None = Depends(require_api_access),
    ) -> dict:
        try:
            require_owner(request.owner_id)
            return facade.service.revise_proposal(
                proposal_id,
                request.owner_id,
                request.instruction(facade.settings.account_id),
            ).model_dump(mode="json")
        except (ProposalError, RiskRejected, ValueError) as exc:
            raise fail(exc) from exc

    @app.post("/v1/proposals/{proposal_id}/confirm")
    def confirm(
        proposal_id: str, request: ConfirmationRequest, _: None = Depends(require_api_access)
    ) -> dict:
        try:
            require_owner(request.owner_id)
            return facade.service.confirm(
                proposal_id, request.owner_id, request.version, request.confirmation_token
            )
        except ProposalError as exc:
            raise fail(exc) from exc

    @app.post("/v1/proposals/{proposal_id}/reject")
    def reject(
        proposal_id: str, request: ConfirmationRequest, _: None = Depends(require_api_access)
    ) -> dict[str, str]:
        try:
            require_owner(request.owner_id)
            facade.service.reject(
                proposal_id, request.owner_id, request.version, request.confirmation_token
            )
            return {"status": "rejected"}
        except ProposalError as exc:
            raise fail(exc) from exc

    @app.get("/v1/account")
    def account(_: None = Depends(require_api_access)) -> dict:
        return facade.service.broker_state()

    @app.get("/v1/orders")
    def orders(_: None = Depends(require_api_access)) -> list[dict]:
        return facade.service.broker_state()["state"]["orders"]

    @app.get("/v1/positions")
    def positions(_: None = Depends(require_api_access)) -> list[dict]:
        return facade.service.broker_state()["state"]["positions"]

    @app.get("/v1/status")
    def status(_: None = Depends(require_api_access)) -> dict:
        return facade.service.broker_state()

    @app.post("/v1/pause")
    def pause(_: None = Depends(require_api_access)) -> dict[str, str]:
        return {"message": facade.pause_entries()}

    @app.post("/v1/resume")
    def resume(_: None = Depends(require_api_access)) -> dict[str, str]:
        return {"message": facade.resume_entries()}

    @app.post("/v1/orders/{order_id}/cancel")
    def cancel(order_id: str, _: None = Depends(require_api_access)) -> dict[str, str]:
        try:
            return {"message": facade.cancel_order(order_id)}
        except ValueError as exc:
            raise fail(exc) from exc

    @app.post("/v1/positions/{position_id}/close")
    def close(position_id: str, _: None = Depends(require_api_access)) -> dict[str, str]:
        try:
            return {"message": facade.close_position(position_id)}
        except ValueError as exc:
            raise fail(exc) from exc

    @app.post("/v1/protection/proposals")
    def create_protection_proposal(
        request: ProtectionProposalRequest, _: None = Depends(require_api_access)
    ) -> dict:
        try:
            require_owner(request.owner_id)
            proposal = facade.service.create_protection_modification_proposal(
                request.owner_id,
                request.order_id,
                request.position_id,
                request.stop_loss,
                request.take_profit,
            )
            return proposal.model_dump(mode="json")
        except (ProposalError, ValueError) as exc:
            raise fail(exc) from exc

    @app.post("/v1/protection/proposals/{proposal_id}/confirm")
    def confirm_protection(
        proposal_id: str, request: ConfirmationRequest, _: None = Depends(require_api_access)
    ) -> dict:
        try:
            require_owner(request.owner_id)
            return facade.service.confirm_protection_modification(
                proposal_id, request.owner_id, request.version, request.confirmation_token
            )
        except ProposalError as exc:
            raise fail(exc) from exc

    @app.post("/v1/protection/proposals/{proposal_id}/reject")
    def reject_protection(
        proposal_id: str, request: ConfirmationRequest, _: None = Depends(require_api_access)
    ) -> dict[str, str]:
        try:
            require_owner(request.owner_id)
            facade.service.reject_action_proposal(
                proposal_id, request.owner_id, request.version, request.confirmation_token
            )
            return {"status": "rejected"}
        except ProposalError as exc:
            raise fail(exc) from exc

    return app
