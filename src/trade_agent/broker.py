"""Replaceable broker boundary. The rest of the application never imports MT5."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from trade_agent.domain import (
    AccountSnapshot,
    BrokerOperationResult,
    BrokerState,
    ExecutionStatus,
    OrderSnapshot,
    OrderType,
    PositionSnapshot,
    PreflightResult,
    Quote,
    Side,
    SubmissionResult,
    SymbolSpec,
    TradeInstruction,
)


class BrokerUnavailable(RuntimeError):
    """The connector could not establish whether an operation happened."""


class BrokerAdapter(ABC):
    def bars(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        raise BrokerUnavailable("this broker does not provide historical candles")

    def profit_target(
        self,
        account_id: str,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry_price: Decimal,
        target_usd: Decimal,
    ) -> dict:
        raise ValueError("this broker does not support USD profit-target conversion")

    def history(self, account_id: str, since: datetime, until: datetime) -> dict:
        return {"since": since.isoformat(), "until": until.isoformat(), "deals": []}

    @abstractmethod
    def capabilities(self) -> dict[str, bool | int]: ...

    @abstractmethod
    def account(self, account_id: str) -> AccountSnapshot: ...

    @abstractmethod
    def symbol(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    def quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def preflight(
        self, instruction: TradeInstruction, order_type: OrderType
    ) -> PreflightResult: ...

    @abstractmethod
    def submit(
        self, instruction: TradeInstruction, order_type: OrderType, idempotency_key: str
    ) -> SubmissionResult: ...

    @abstractmethod
    def modify(
        self,
        *,
        order_id: str | None,
        position_id: str | None,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        idempotency_key: str,
    ) -> None: ...

    @abstractmethod
    def cancel(self, order_id: str, idempotency_key: str) -> None: ...

    @abstractmethod
    def close(
        self, position_id: str, volume: Decimal | None = None, idempotency_key: str = ""
    ) -> None: ...

    @abstractmethod
    def state(self, account_id: str) -> BrokerState: ...

    @abstractmethod
    def reconcile_operation(self, idempotency_key: str) -> BrokerOperationResult | None: ...


class FakeBroker(BrokerAdapter):
    """In-memory deterministic broker for safety tests and local demos only."""

    def __init__(self, account_id: str = "demo-mt5") -> None:
        self.account_id = account_id
        self._lock = Lock()
        self._account = AccountSnapshot(
            account_id=account_id,
            currency="USD",
            balance=Decimal("10000"),
            equity=Decimal("10000"),
            margin_free=Decimal("10000"),
            is_demo=True,
            trade_allowed=True,
        )
        self._specs = {
            symbol: SymbolSpec(
                symbol=symbol,
                point=Decimal("0.00001") if symbol != "USDJPY" else Decimal("0.001"),
                digits=5 if symbol != "USDJPY" else 3,
                volume_min=Decimal("0.01"),
                volume_max=Decimal("100"),
                volume_step=Decimal("0.01"),
                min_stop_distance_points=Decimal("10"),
                filling_modes=["return", "fok", "ioc"],
                trade_tick_size=Decimal("0.00001") if symbol != "USDJPY" else Decimal("0.001"),
                trade_tick_value=Decimal("1"),
            )
            for symbol in ("EURUSD", "GBPUSD", "USDJPY")
        }
        self._quotes = {
            "EURUSD": (Decimal("1.08000"), Decimal("1.08010")),
            "GBPUSD": (Decimal("1.26000"), Decimal("1.26012")),
            "USDJPY": (Decimal("150.000"), Decimal("150.010")),
        }
        self._orders: dict[str, OrderSnapshot] = {}
        self._positions: dict[str, PositionSnapshot] = {}
        self._submissions: dict[str, SubmissionResult] = {}
        self._operations: dict[str, BrokerOperationResult] = {}

    def capabilities(self) -> dict[str, bool | int]:
        return {
            "market": True,
            "pending": True,
            "modify": True,
            "cancel": True,
            "close": True,
            "max_slippage_points": 0,
        }

    def account(self, account_id: str) -> AccountSnapshot:
        if account_id != self.account_id:
            raise ValueError("unknown account")
        return self._account

    def symbol(self, symbol: str) -> SymbolSpec:
        try:
            return self._specs[symbol]
        except KeyError as exc:
            raise ValueError(f"unsupported symbol: {symbol}") from exc

    def quote(self, symbol: str) -> Quote:
        try:
            bid, ask = self._quotes[symbol]
        except KeyError as exc:
            raise ValueError(f"unsupported symbol: {symbol}") from exc
        return Quote(symbol=symbol, bid=bid, ask=ask, observed_at=datetime.now(UTC))

    def preflight(self, instruction: TradeInstruction, order_type: OrderType) -> PreflightResult:
        spec = self.symbol(instruction.symbol)
        if not self._account.trade_allowed:
            return PreflightResult(accepted=False, reason="account disallows trading")
        if not spec.trade_enabled:
            return PreflightResult(accepted=False, reason="instrument disabled")
        estimated = instruction.volume * Decimal("100")
        if self._account.margin_free < estimated:
            return PreflightResult(
                accepted=False, reason="insufficient margin", estimated_margin=estimated
            )
        return PreflightResult(accepted=True, estimated_margin=estimated)

    def submit(
        self, instruction: TradeInstruction, order_type: OrderType, idempotency_key: str
    ) -> SubmissionResult:
        with self._lock:
            if idempotency_key in self._submissions:
                return self._submissions[idempotency_key]
            checked = self.preflight(instruction, order_type)
            if not checked.accepted:
                result = SubmissionResult(
                    accepted=False, status=ExecutionStatus.REJECTED, reason=checked.reason
                )
                self._submissions[idempotency_key] = result
                return result
            now = datetime.now(UTC)
            if order_type is OrderType.MARKET:
                quote = self.quote(instruction.symbol)
                fill_price = quote.ask if instruction.side is Side.BUY else quote.bid
                position_id = str(uuid4())
                self._positions[position_id] = PositionSnapshot(
                    position_id=position_id,
                    symbol=instruction.symbol,
                    side=instruction.side,
                    volume=instruction.volume,
                    entry_price=fill_price,
                    stop_loss=instruction.stop_loss,
                    take_profit=instruction.take_profit,
                    profit=Decimal("0"),
                    opened_at=now,
                )
                result = SubmissionResult(
                    accepted=True,
                    deal_id=str(uuid4()),
                    position_id=position_id,
                    status=ExecutionStatus.ACCEPTED,
                )
            else:
                order_id = str(uuid4())
                self._orders[order_id] = OrderSnapshot(
                    order_id=order_id,
                    symbol=instruction.symbol,
                    side=instruction.side,
                    order_type=order_type,
                    volume=instruction.volume,
                    entry_price=instruction.entry_price,
                    stop_loss=instruction.stop_loss,
                    take_profit=instruction.take_profit,
                    status="pending",
                    created_at=now,
                )
                result = SubmissionResult(
                    accepted=True, order_id=order_id, status=ExecutionStatus.ACCEPTED
                )
            self._submissions[idempotency_key] = result
            return result

    def modify(
        self,
        *,
        order_id: str | None,
        position_id: str | None,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        idempotency_key: str,
    ) -> None:
        with self._lock:
            if idempotency_key in self._operations:
                return
            if order_id is not None and order_id in self._orders:
                order = self._orders[order_id]
                self._orders[order_id] = order.model_copy(
                    update={"stop_loss": stop_loss, "take_profit": take_profit}
                )
                self._operations[idempotency_key] = BrokerOperationResult(
                    status=ExecutionStatus.ACCEPTED, order_id=order_id
                )
                return
            if position_id is not None and position_id in self._positions:
                position = self._positions[position_id]
                self._positions[position_id] = position.model_copy(
                    update={"stop_loss": stop_loss, "take_profit": take_profit}
                )
                self._operations[idempotency_key] = BrokerOperationResult(
                    status=ExecutionStatus.ACCEPTED, position_id=position_id
                )
                return
        raise ValueError("order or position was not active")

    def cancel(self, order_id: str, idempotency_key: str) -> None:
        with self._lock:
            if idempotency_key in self._operations:
                return
            if order_id not in self._orders:
                raise ValueError("pending order was not active")
            self._orders.pop(order_id)
            self._operations[idempotency_key] = BrokerOperationResult(
                status=ExecutionStatus.CANCELLED, order_id=order_id
            )

    def close(
        self, position_id: str, volume: Decimal | None = None, idempotency_key: str = ""
    ) -> None:
        with self._lock:
            if idempotency_key and idempotency_key in self._operations:
                return
            position = self._positions.get(position_id)
            if position is None:
                raise ValueError("position was not active")
            close_volume = volume or position.volume
            if close_volume <= 0 or close_volume > position.volume:
                raise ValueError("invalid close volume")
            if close_volume == position.volume:
                self._positions.pop(position_id)
            else:
                self._positions[position_id] = position.model_copy(
                    update={"volume": position.volume - close_volume}
                )
            if idempotency_key:
                self._operations[idempotency_key] = BrokerOperationResult(
                    status=ExecutionStatus.CLOSED, position_id=position_id
                )

    def state(self, account_id: str) -> BrokerState:
        self.account(account_id)
        with self._lock:
            return BrokerState(
                orders=list(self._orders.values()), positions=list(self._positions.values())
            )

    def reconcile_operation(self, idempotency_key: str) -> BrokerOperationResult | None:
        with self._lock:
            operation = self._operations.get(idempotency_key)
            if operation is not None:
                return operation
            submission = self._submissions.get(idempotency_key)
            if submission is None:
                return None
            return BrokerOperationResult(
                status=submission.status,
                order_id=submission.order_id,
                deal_id=submission.deal_id,
                position_id=submission.position_id,
                reason=submission.reason,
                raw=submission.raw,
            )

    def fill_pending_order(self, order_id: str, volume: Decimal | None = None) -> str:
        """Test/local-demo helper that models a full or partial pending-order fill."""
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise ValueError("pending order was not active")
            fill_volume = volume or order.volume
            if fill_volume <= 0 or fill_volume > order.volume:
                raise ValueError("fill volume is invalid")
            position_id = str(uuid4())
            self._positions[position_id] = PositionSnapshot(
                position_id=position_id,
                symbol=order.symbol,
                side=order.side,
                volume=fill_volume,
                entry_price=order.entry_price or self.quote(order.symbol).ask,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                profit=Decimal("0"),
                opened_at=datetime.now(UTC),
            )
            if fill_volume == order.volume:
                self._orders.pop(order_id)
            else:
                self._orders[order_id] = order.model_copy(
                    update={"volume": order.volume - fill_volume}
                )
            for key, result in tuple(self._submissions.items()):
                if result.order_id == order_id:
                    self._submissions[key] = result.model_copy(
                        update={"position_id": position_id, "deal_id": str(uuid4())}
                    )
                    break
            return position_id


class MT5HTTPBroker(BrokerAdapter):
    def bars(self, symbol: str, timeframe: str = "M15", count: int = 200) -> list[dict]:
        query = urlencode({"timeframe": timeframe, "count": count})
        payload = self._request("GET", f"/v1/bars/{symbol}?{query}")
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
            raise BrokerUnavailable("connector returned invalid candle history")
        return payload["bars"]

    def profit_target(
        self,
        account_id: str,
        symbol: str,
        side: Side,
        volume: Decimal,
        entry_price: Decimal,
        target_usd: Decimal,
    ) -> dict:
        return self._request(
            "POST",
            "/v1/profit-target",
            {
                "account_id": account_id,
                "symbol": symbol,
                "side": side,
                "volume": str(volume),
                "entry_price": str(entry_price),
                "target_usd": str(target_usd),
            },
        )

    def history(self, account_id: str, since: datetime, until: datetime) -> dict:
        query = urlencode({"since": since.isoformat(), "until": until.isoformat()})
        return self._request("GET", f"/v1/accounts/{account_id}/history?{query}")

    """Authenticated HTTP client for the isolated MT5 connector process."""

    def __init__(self, base_url: str, token: str | None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(10.0),
            headers={"X-Connector-Token": token or ""},
        )

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self._client.request(method, path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise BrokerUnavailable("connector timed out; execution outcome is unknown") from exc
        except httpx.HTTPError as exc:
            raise BrokerUnavailable(f"connector unavailable: {exc}") from exc

    def capabilities(self) -> dict[str, bool | int]:
        payload = self._request("GET", "/v1/capabilities")
        return {key: value for key, value in payload.items() if isinstance(value, (bool, int))}

    def account(self, account_id: str) -> AccountSnapshot:
        return AccountSnapshot.model_validate(self._request("GET", f"/v1/accounts/{account_id}"))

    def symbol(self, symbol: str) -> SymbolSpec:
        return SymbolSpec.model_validate(self._request("GET", f"/v1/symbols/{symbol}"))

    def quote(self, symbol: str) -> Quote:
        return Quote.model_validate(self._request("GET", f"/v1/quotes/{symbol}"))

    def preflight(self, instruction: TradeInstruction, order_type: OrderType) -> PreflightResult:
        return PreflightResult.model_validate(
            self._request(
                "POST",
                "/v1/preflight",
                {
                    "instruction": instruction.model_dump(
                        mode="json", exclude={"profit_target_usd", "estimated_gross_profit", "auto_market_protection"}
                    ),
                    "order_type": order_type,
                },
            )
        )

    def submit(
        self, instruction: TradeInstruction, order_type: OrderType, idempotency_key: str
    ) -> SubmissionResult:
        return SubmissionResult.model_validate(
            self._request(
                "POST",
                "/v1/orders",
                {
                    "instruction": instruction.model_dump(
                        mode="json", exclude={"profit_target_usd", "estimated_gross_profit", "auto_market_protection"}
                    ),
                    "order_type": order_type,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    def modify(
        self,
        *,
        order_id: str | None,
        position_id: str | None,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        idempotency_key: str,
    ) -> None:
        response = self._request(
            "POST",
            "/v1/modify",
            {
                "order_id": order_id,
                "position_id": position_id,
                "stop_loss": str(stop_loss) if stop_loss is not None else None,
                "take_profit": str(take_profit) if take_profit is not None else None,
                "idempotency_key": idempotency_key,
            },
        )
        if response.get("status") == "unknown":
            raise BrokerUnavailable(response.get("reason") or "modification outcome is unknown")

    def cancel(self, order_id: str, idempotency_key: str) -> None:
        response = self._request(
            "POST", f"/v1/orders/{order_id}/cancel", {"idempotency_key": idempotency_key}
        )
        if response.get("status") == "unknown":
            raise BrokerUnavailable(response.get("reason") or "cancellation outcome is unknown")

    def close(
        self, position_id: str, volume: Decimal | None = None, idempotency_key: str = ""
    ) -> None:
        response = self._request(
            "POST",
            f"/v1/positions/{position_id}/close",
            {
                "volume": str(volume) if volume is not None else None,
                "idempotency_key": idempotency_key,
            },
        )
        if response.get("status") == "unknown":
            raise BrokerUnavailable(response.get("reason") or "close outcome is unknown")

    def state(self, account_id: str) -> BrokerState:
        return BrokerState.model_validate(self._request("GET", f"/v1/accounts/{account_id}/state"))

    def reconcile_operation(self, idempotency_key: str) -> BrokerOperationResult | None:
        payload = self._request("GET", f"/v1/reconciliation/{idempotency_key}")
        if payload.get("status") == "not_found":
            return None
        order_ids = payload.get("order_ids") or []
        deal_ids = payload.get("deal_ids") or []
        position_ids = payload.get("position_ids") or []
        return BrokerOperationResult(
            status=ExecutionStatus(payload.get("status", "unknown")),
            order_id=str(order_ids[0]) if order_ids else None,
            deal_id=str(deal_ids[0]) if deal_ids else None,
            position_id=str(position_ids[0]) if position_ids else None,
            reason=(payload.get("journal_result") or {}).get("reason"),
            raw=payload,
        )
