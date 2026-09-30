"""Separate, read-only public application. Never constructs a broker or trading facade."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from starlette.middleware.base import RequestResponseEndpoint

from trade_agent.config import Settings
from trade_agent.db import DashboardAccessRecord, Database
from trade_agent.reporting import aware, dashboard_data, now

COOKIE = "__Host-trade-dashboard"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_code(db: Database) -> str:
    # 60 bits of entropy: readable, pasteable, and not an offline-bruteforceable PIN.
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(12))
    with db.session() as session:
        session.execute(delete(DashboardAccessRecord).where(DashboardAccessRecord.kind == "code"))
        session.add(
            DashboardAccessRecord(
                key=digest(code), kind="code", payload={}, expires_at=now() + timedelta(minutes=5)
            )
        )
    return code


class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


def create_dashboard(db: Database, dist: Path) -> FastAPI:
    app = FastAPI(title="Trade Agent Dashboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def authenticate(request: Request) -> None:
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 128:
            raise HTTPException(401, "Sign in using a fresh /dashboard code from Telegram.")
        with db.session() as session:
            row = session.get(DashboardAccessRecord, digest(token))
            if row is None or row.kind != "session" or aware(row.expires_at) <= now():
                raise HTTPException(401, "Dashboard session expired.")

    @app.post("/dashboard-api/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict:
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site login is disabled")
        stamp = now()
        # Global rate limit is deliberate for this single-owner dashboard; no trust in proxy IPs.
        with db.session() as session:
            if db.engine.dialect.name == "postgresql":
                from sqlalchemy import text

                session.execute(text("SELECT pg_advisory_xact_lock(830021)"))
            session.execute(
                delete(DashboardAccessRecord).where(DashboardAccessRecord.expires_at <= stamp)
            )
            attempts = session.scalars(
                select(DashboardAccessRecord).where(DashboardAccessRecord.kind == "attempt")
            ).all()
            if len(attempts) >= 5:
                raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
            row = session.get(
                DashboardAccessRecord, digest(body.code.strip().upper()), with_for_update=True
            )
            valid = row is not None and row.kind == "code" and aware(row.expires_at) > stamp
            if not valid:
                session.add(
                    DashboardAccessRecord(
                        key=secrets.token_hex(32),
                        kind="attempt",
                        payload={},
                        expires_at=stamp + timedelta(minutes=15),
                    )
                )
            else:
                session.delete(row)
                token = secrets.token_urlsafe(32)
                session.add(
                    DashboardAccessRecord(
                        key=digest(token),
                        kind="session",
                        payload={},
                        expires_at=stamp + timedelta(hours=12),
                    )
                )
        if not valid:
            raise HTTPException(401, "Invalid or expired code. Request /dashboard in Telegram.")
        response.set_cookie(
            COOKIE, token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/"
        )
        return {"authenticated": True}

    @app.post("/dashboard-api/logout", dependencies=[Depends(authenticate)])
    def logout(request: Request, response: Response) -> dict:
        with db.session() as session:
            session.execute(
                delete(DashboardAccessRecord).where(
                    DashboardAccessRecord.key == digest(request.cookies[COOKIE])
                )
            )
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return {"authenticated": False}

    @app.get("/dashboard-api/snapshot", dependencies=[Depends(authenticate)])
    def snapshot(
        period: Literal["today", "7d", "30d", "all"] = "7d",
        scope: Literal["agent", "account"] = "agent",
    ) -> dict:
        return dashboard_data(db, period, scope)

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok", "role": "read-only-dashboard"}

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "Dashboard frontend has not been built")
        return FileResponse(dist / "index.html")

    return app


def main() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]  # BaseSettings runtime option
    db = Database(settings.database_url)
    uvicorn.run(
        create_dashboard(db, Path(settings.dashboard_dist)),
        host="127.0.0.1",
        port=settings.dashboard_port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
