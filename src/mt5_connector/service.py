"""The only module that accesses the MetaTrader5 Python package."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any

from mt5_connector.contracts import (
    ModifyRequest,
    OrderType,
    PreflightResponse,
    SubmissionResponse,
    TradeInstructionPayload,
)
from mt5_connector.settings import ConnectorSettings


class ConnectorError(RuntimeError):
    """A request failed before a trade could be submitted."""


class TerminalUnavailable(ConnectorError):
    """The MT5 terminal cannot currently be queried."""


class MT5Gateway:
    """Lazily imports and reconnects to MT5 without leaking it to the backend."""

    def __init__(self, settings: ConnectorSettings, module: Any | None = None) -> None:
        self._settings = settings
        self._module = module
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _mt5(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("MetaTrader5")
            except ImportError as exc:
                self._last_error = (
                    "MetaTrader5 package is not installed in the connector environment"
                )
                raise TerminalUnavailable(self._last_error) from exc
        return self._module

    def connect(self) -> Any:
        mt5 = self._mt5()
        terminal_info = mt5.terminal_info()
        if terminal_info is not None:
            self._verify_configured_identity(mt5)
            self._last_error = None
            return mt5

        arguments: dict[str, Any] = {
            "timeout": self._settings.timeout_ms,
            "portable": self._settings.portable,
        }
        if self._settings.login is not None:
            arguments["login"] = self._settings.login
        if self._settings.password is not None:
            arguments["password"] = self._settings.password
        if self._settings.server is not None:
            arguments["server"] = self._settings.server
        initialized = (
            mt5.initialize(self._settings.terminal_path, **arguments)
            if self._settings.terminal_path
            else mt5.initialize(**arguments)
        )
        if not initialized:
            self._last_error = self._safe_last_error(mt5)
            raise TerminalUnavailable(f"MT5 terminal is unavailable: {self._last_error}")
        self._verify_configured_identity(mt5)
        self._last_error = None
        return mt5

    def _verify_configured_identity(self, mt5: Any) -> None:
        """Reject a pre-opened terminal session that is not the configured account."""
        account = mt5.account_info()
        if account is None:
            raise TerminalUnavailable("MT5 account information is unavailable")
        if self._settings.login is not None and int(account.login) != self._settings.login:
            raise TerminalUnavailable("connected MT5 account does not match MT5_CONNECTOR_LOGIN")
        if self._settings.server is not None:
            connected_server = str(getattr(account, "server", ""))
            if connected_server.casefold() != self._settings.server.casefold():
                raise TerminalUnavailable(
                    "connected MT5 server does not match MT5_CONNECTOR_SERVER"
                )

    def is_connected(self) -> bool:
        try:
            return self._mt5().terminal_info() is not None
        except TerminalUnavailable:
            return False

    def shutdown(self) -> None:
        if self._module is not None:
            self._module.shutdown()

    @staticmethod
    def _safe_last_error(mt5: Any) -> str:
        try:
            return str(mt5.last_error())
        except Exception:  # noqa: BLE001 - reporting must not hide the primary failure
            return "unavailable"


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConnectorError("terminal returned an invalid numeric value") from exc


def _utc_timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise ConnectorError("terminal returned an invalid timestamp") from exc


def _asdict(value: Any) -> dict[str, Any]:
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if isinstance(value, dict):
        return dict(value)
    return {}


class MT5Service:
    """Maps safe connector HTTP operations to the MT5 terminal API."""

    def __init__(self, settings: ConnectorSettings, gateway: MT5Gateway | None = None) -> None:
        self._settings = settings
        self._gateway = gateway or MT5Gateway(settings)
        self._mutation_lock = RLock()

    def _check_clock(self, value: datetime) -> None:
        for bound, lower in (
            (self._settings.server_time_valid_from, True),
            (self._settings.server_time_valid_until, False),
        ):
            if bound:
                boundary = datetime.fromisoformat(bound)
                if (lower and value < boundary) or (not lower and value >= boundary):
                    raise ConnectorError("broker clock profile requires review for this date")

    def _server_timestamp(self, value: Any) -> datetime:
        normalized = _utc_timestamp(value) - timedelta(
            seconds=self._settings.server_time_offset_seconds
        )
        self._check_clock(normalized)
        return normalized

    def _server_bound(self, value: datetime) -> datetime:
        self._check_clock(value)
        return value + timedelta(seconds=self._settings.server_time_offset_seconds)

    def health(self) -> dict[str, Any]:
        connected = self._gateway.is_connected()
        return {
            "status": "ok" if connected else "degraded",
            "mt5_connected": connected,
            "last_error": self._gateway.last_error if not connected else None,
        }

    def capabilities(self) -> dict[str, bool | int]:
        self._gateway.connect()
        return {
            "market": True,
            "pending": True,
            "modify": True,
            "cancel": True,
            "close": True,
            "broker_side_pending_expiration": False,
            "max_slippage_points": self._settings.deviation_points,
        }

    def account(self, account_id: str) -> dict[str, Any]:
        mt5 = self._gateway.connect()
        info = mt5.account_info()
        if info is None:
            raise TerminalUnavailable("MT5 account information is unavailable")
        if str(info.login) != account_id:
            raise ConnectorError("requested account does not match the connected MT5 account")
        terminal = mt5.terminal_info()
        demo_constant = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        return {
            "account_id": str(info.login),
            "currency": str(info.currency),
            "balance": _decimal(info.balance),
            "equity": _decimal(info.equity),
            "margin_free": _decimal(info.margin_free),
            "is_demo": getattr(info, "trade_mode", None) == demo_constant,
            "trade_allowed": bool(getattr(info, "trade_allowed", False))
            and bool(getattr(terminal, "trade_allowed", False)),
        }

    def _symbol_info(self, symbol: str) -> tuple[Any, Any]:
        mt5 = self._gateway.connect()
        if not mt5.symbol_select(symbol, True):
            raise ConnectorError(f"symbol is unavailable: {symbol}")
        info = mt5.symbol_info(symbol)
        if info is None:
            raise ConnectorError(f"symbol is unavailable: {symbol}")
        return mt5, info

    def symbol(self, symbol: str) -> dict[str, Any]:
        mt5, info = self._symbol_info(symbol.upper())
        filling_mask = int(getattr(info, "filling_mode", 0))
        modes = ["return"]
        if filling_mask & int(getattr(mt5, "SYMBOL_FILLING_FOK", 1)):
            modes.append("fok")
        if filling_mask & int(getattr(mt5, "SYMBOL_FILLING_IOC", 2)):
            modes.append("ioc")
        disabled = getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0)
        return {
            "symbol": symbol.upper(),
            "point": _decimal(info.point),
            "digits": int(info.digits),
            "volume_min": _decimal(info.volume_min),
            "volume_max": _decimal(info.volume_max),
            "volume_step": _decimal(info.volume_step),
            "min_stop_distance_points": _decimal(getattr(info, "trade_stops_level", 0)),
            "trade_tick_size": _decimal(getattr(info, "trade_tick_size", 0)) or None,
            "trade_tick_value": _decimal(getattr(info, "trade_tick_value_loss", 0))
            or _decimal(getattr(info, "trade_tick_value", 0))
            or None,
            "trade_enabled": getattr(info, "trade_mode", disabled) != disabled,
            "filling_modes": modes,
        }

    def quote(self, symbol: str) -> dict[str, Any]:
        mt5, _ = self._symbol_info(symbol.upper())
        tick = mt5.symbol_info_tick(symbol.upper())
        if tick is None or _decimal(tick.bid) <= 0 or _decimal(tick.ask) <= 0:
            raise TerminalUnavailable(f"quote is unavailable for {symbol.upper()}")
        return {
            "symbol": symbol.upper(),
            "bid": _decimal(tick.bid),
            "ask": _decimal(tick.ask),
            "observed_at": self._server_timestamp(
                getattr(tick, "time", int(datetime.now(UTC).timestamp()))
            ),
        }

    def _account_matches(self, instruction: TradeInstructionPayload) -> None:
        self.account(instruction.account_id)

    @staticmethod
    def _validate_volume(instruction: TradeInstructionPayload, info: Any) -> str | None:
        volume = instruction.volume
        minimum = _decimal(info.volume_min)
        maximum = _decimal(info.volume_max)
        step = _decimal(info.volume_step)
        if volume < minimum or volume > maximum:
            return f"volume must be between {minimum} and {maximum}"
        units = (volume - minimum) / step
        if units != units.to_integral_value():
            return f"volume must use the broker step of {step}"
        return None

    @staticmethod
    def _is_tick_aligned(value: Decimal, point: Decimal) -> bool:
        return (value / point) == (value / point).to_integral_value()

    def _price_and_protection(
        self, instruction: TradeInstructionPayload, order_type: OrderType, info: Any
    ) -> tuple[Decimal, str | None]:
        point = _decimal(info.point)
        tick_size = _decimal(getattr(info, "trade_tick_size", 0)) or point
        if order_type == "market":
            quote = self.quote(instruction.symbol)
            price = quote["ask"] if instruction.side == "buy" else quote["bid"]
        else:
            if instruction.entry_price is None:
                return Decimal("0"), "pending orders require entry_price"
            price = instruction.entry_price
        for label, level in (
            ("entry price", price),
            ("stop loss", instruction.stop_loss),
            ("take profit", instruction.take_profit),
        ):
            if not self._is_tick_aligned(level, tick_size):
                return price, f"{label} must align with broker tick size {tick_size}"
        if instruction.side == "buy":
            valid_levels = instruction.stop_loss < price < instruction.take_profit
        else:
            valid_levels = instruction.take_profit < price < instruction.stop_loss
        if not valid_levels:
            return price, "stop loss and take profit are on the wrong side of the execution price"
        min_distance = _decimal(getattr(info, "trade_stops_level", 0)) * point
        if instruction.side == "buy":
            distances = (price - instruction.stop_loss, instruction.take_profit - price)
        else:
            distances = (instruction.stop_loss - price, price - instruction.take_profit)
        if any(distance < min_distance for distance in distances):
            return price, f"stop loss and take profit must be at least {min_distance} from price"
        return price, None

    def _order_constants(self, mt5: Any, side: str, order_type: OrderType) -> tuple[int, int]:
        market_types = {
            "buy": int(mt5.ORDER_TYPE_BUY),
            "sell": int(mt5.ORDER_TYPE_SELL),
        }
        pending_names = {
            "buy_limit": "ORDER_TYPE_BUY_LIMIT",
            "buy_stop": "ORDER_TYPE_BUY_STOP",
            "sell_limit": "ORDER_TYPE_SELL_LIMIT",
            "sell_stop": "ORDER_TYPE_SELL_STOP",
        }
        if order_type == "market":
            return int(mt5.TRADE_ACTION_DEAL), market_types[side]
        return int(mt5.TRADE_ACTION_PENDING), int(getattr(mt5, pending_names[order_type]))

    def _request_for(
        self, instruction: TradeInstructionPayload, order_type: OrderType, idempotency_key: str
    ) -> tuple[Any, dict[str, Any], Decimal, str | None]:
        self._account_matches(instruction)
        mt5, info = self._symbol_info(instruction.symbol)
        if not bool(getattr(info, "visible", True)):
            return mt5, {}, Decimal("0"), "symbol is not visible in terminal"
        volume_error = self._validate_volume(instruction, info)
        if volume_error:
            return mt5, {}, Decimal("0"), volume_error
        price, protection_error = self._price_and_protection(instruction, order_type, info)
        if protection_error:
            return mt5, {}, price, protection_error
        action, mt5_order_type = self._order_constants(mt5, instruction.side, order_type)
        request = {
            "action": action,
            "symbol": instruction.symbol,
            "volume": float(instruction.volume),
            "type": mt5_order_type,
            "price": float(price),
            "sl": float(instruction.stop_loss),
            "tp": float(instruction.take_profit),
            "deviation": self._settings.deviation_points,
            "magic": self._settings.magic,
            "comment": f"ta:{idempotency_key[:24]}",
        }
        if order_type != "market":
            expiration_mode = int(getattr(info, "expiration_mode", 0))
            specified_flag = int(getattr(mt5, "SYMBOL_EXPIRATION_SPECIFIED", 4))
            if instruction.deadline_at is not None and expiration_mode & specified_flag:
                request["type_time"] = int(mt5.ORDER_TIME_SPECIFIED)
                request["expiration"] = int(self._server_bound(instruction.deadline_at).timestamp())
            else:
                request["type_time"] = int(mt5.ORDER_TIME_GTC)
            request["type_filling"] = int(mt5.ORDER_FILLING_RETURN)
        else:
            request["type_filling"] = self._market_filling(mt5, info)
        return mt5, request, price, None

    @staticmethod
    def _market_filling(mt5: Any, info: Any) -> int:
        mask = int(getattr(info, "filling_mode", 0))
        foc = int(getattr(mt5, "SYMBOL_FILLING_FOK", 1))
        ioc = int(getattr(mt5, "SYMBOL_FILLING_IOC", 2))
        if mask & foc:
            return int(mt5.ORDER_FILLING_FOK)
        if mask & ioc:
            return int(mt5.ORDER_FILLING_IOC)
        return int(mt5.ORDER_FILLING_RETURN)

    def preflight(
        self, instruction: TradeInstructionPayload, order_type: OrderType
    ) -> PreflightResponse:
        try:
            mt5, request, _, local_error = self._request_for(instruction, order_type, "preflight")
        except TerminalUnavailable:
            raise
        except ConnectorError as exc:
            return PreflightResponse(accepted=False, reason=str(exc))
        if local_error:
            return PreflightResponse(accepted=False, reason=local_error)
        try:
            result = mt5.order_check(request)
        except Exception as exc:  # noqa: BLE001 - the terminal call is an external boundary
            raise TerminalUnavailable("MT5 order check could not be completed") from exc
        if result is None:
            return PreflightResponse(
                accepted=False,
                reason="MT5 rejected the order check",
                raw={"last_error": MT5Gateway._safe_last_error(mt5)},
            )
        raw = _asdict(result)
        retcode = int(raw.get("retcode", -1))
        return PreflightResponse(
            accepted=retcode == 0,
            reason=None
            if retcode == 0
            else str(raw.get("comment", "MT5 rejected the order check")),
            estimated_margin=_decimal(raw["margin"]) if raw.get("margin") is not None else None,
            raw={"retcode": retcode, "comment": str(raw.get("comment", ""))},
        )

    def submit(
        self, instruction: TradeInstructionPayload, order_type: OrderType, idempotency_key: str
    ) -> SubmissionResponse:
        """Serialize all terminal order sends for this connector account."""
        with self._mutation_lock:
            return self._submit_unlocked(instruction, order_type, idempotency_key)

    def _submit_unlocked(
        self, instruction: TradeInstructionPayload, order_type: OrderType, idempotency_key: str
    ) -> SubmissionResponse:
        preflight = self.preflight(instruction, order_type)
        if not preflight.accepted:
            return SubmissionResponse(
                accepted=False,
                status="rejected",
                reason=preflight.reason,
                raw=preflight.raw,
            )
        try:
            mt5, request, _, local_error = self._request_for(
                instruction, order_type, idempotency_key
            )
        except ConnectorError as exc:
            return SubmissionResponse(accepted=False, status="rejected", reason=str(exc))
        if local_error:
            return SubmissionResponse(accepted=False, status="rejected", reason=local_error)
        result = mt5.order_send(request)
        if result is None:
            return SubmissionResponse(
                accepted=False,
                status="unknown",
                reason="MT5 returned no submission result; reconcile before retrying",
                raw={"last_error": MT5Gateway._safe_last_error(mt5)},
            )
        raw = _asdict(result)
        retcode = int(raw.get("retcode", -1))
        accepted_codes = {
            int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)),
            int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
            int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
        }
        accepted = retcode in accepted_codes
        order_id = self._ticket(raw.get("order"))
        deal_id = self._ticket(raw.get("deal"))
        position_id = self._position_from_deal(mt5, deal_id) if accepted and deal_id else None
        return SubmissionResponse(
            accepted=accepted,
            order_id=order_id,
            deal_id=deal_id,
            position_id=position_id,
            status="accepted" if accepted else "rejected",
            reason=None if accepted else str(raw.get("comment", "MT5 rejected the order")),
            raw={
                "retcode": retcode,
                "comment": str(raw.get("comment", "")),
                "request_id": raw.get("request_id"),
                "execution_state": (
                    "partial_fill"
                    if retcode == int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))
                    else "filled"
                    if accepted and deal_id
                    else "pending_order"
                    if accepted and order_type != "market"
                    else "accepted"
                    if accepted
                    else "rejected"
                ),
            },
        )

    @staticmethod
    def _ticket(value: Any) -> str | None:
        try:
            ticket = int(value)
            return str(ticket) if ticket > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _position_from_deal(mt5: Any, deal_id: str) -> str | None:
        try:
            deals = mt5.history_deals_get(ticket=int(deal_id))
            if deals:
                position_id = getattr(deals[0], "position_id", 0)
                return MT5Service._ticket(position_id)
        except Exception:  # noqa: BLE001 - an accepted order must not become a failed response
            return None
        return None

    def modify(self, request: ModifyRequest) -> None:
        with self._mutation_lock:
            self._modify_unlocked(request)

    def _modify_unlocked(self, request: ModifyRequest) -> None:
        if bool(request.order_id) == bool(request.position_id):
            raise ConnectorError("provide exactly one of order_id or position_id")
        mt5 = self._gateway.connect()
        if request.order_id is not None:
            orders = mt5.orders_get(ticket=int(request.order_id))
            if not orders:
                raise ConnectorError("pending order was not active")
            order = orders[0]
            payload: dict[str, Any] = {
                "action": int(mt5.TRADE_ACTION_MODIFY),
                "order": int(request.order_id),
                "price": float(order.price_open),
                "sl": float(request.stop_loss if request.stop_loss is not None else order.sl),
                "tp": float(request.take_profit if request.take_profit is not None else order.tp),
            }
        else:
            assert request.position_id is not None
            positions = mt5.positions_get(ticket=int(request.position_id))
            if not positions:
                raise ConnectorError("position was not active")
            position = positions[0]
            payload = {
                "action": int(mt5.TRADE_ACTION_SLTP),
                "position": int(request.position_id),
                "symbol": str(position.symbol),
                "sl": float(request.stop_loss if request.stop_loss is not None else position.sl),
                "tp": float(
                    request.take_profit if request.take_profit is not None else position.tp
                ),
            }
        self._ensure_done(mt5, mt5.order_send(payload), "MT5 rejected modification")

    def cancel(self, order_id: str) -> None:
        with self._mutation_lock:
            self._cancel_unlocked(order_id)

    def _cancel_unlocked(self, order_id: str) -> None:
        mt5 = self._gateway.connect()
        payload = {"action": int(mt5.TRADE_ACTION_REMOVE), "order": int(order_id)}
        self._ensure_done(mt5, mt5.order_send(payload), "MT5 rejected cancellation")

    def close(self, position_id: str, volume: Decimal | None) -> None:
        with self._mutation_lock:
            self._close_unlocked(position_id, volume)

    def _close_unlocked(self, position_id: str, volume: Decimal | None) -> None:
        mt5 = self._gateway.connect()
        positions = mt5.positions_get(ticket=int(position_id))
        if not positions:
            raise ConnectorError("position was not active")
        position = positions[0]
        close_volume = volume or _decimal(position.volume)
        if close_volume <= 0 or close_volume > _decimal(position.volume):
            raise ConnectorError("close volume is invalid")
        _, info = self._symbol_info(str(position.symbol))
        quote = self.quote(str(position.symbol))
        is_buy = int(position.type) == int(mt5.POSITION_TYPE_BUY)
        payload = {
            "action": int(mt5.TRADE_ACTION_DEAL),
            "position": int(position_id),
            "symbol": str(position.symbol),
            "volume": float(close_volume),
            "type": int(getattr(mt5, "ORDER_TYPE_SELL" if is_buy else "ORDER_TYPE_BUY")),
            "price": float(quote["bid"] if is_buy else quote["ask"]),
            "deviation": self._settings.deviation_points,
            "magic": self._settings.magic,
            "comment": "ta:close",
            "type_filling": self._market_filling(mt5, info),
        }
        self._ensure_done(mt5, mt5.order_send(payload), "MT5 rejected close")

    @staticmethod
    def _ensure_done(mt5: Any, result: Any, message: str) -> None:
        if result is None:
            raise TerminalUnavailable(f"{message}; terminal returned no result")
        raw = _asdict(result)
        retcode = int(raw.get("retcode", -1))
        accepted = {
            int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)),
            int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
            int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
        }
        if retcode not in accepted:
            raise ConnectorError(f"{message}: {raw.get('comment', 'unknown terminal response')}")

    def state(self, account_id: str) -> dict[str, Any]:
        self.account(account_id)
        mt5 = self._gateway.connect()
        orders = mt5.orders_get() or ()
        positions = mt5.positions_get() or ()
        return {
            "orders": [self._order_snapshot(mt5, order) for order in orders],
            "positions": [self._position_snapshot(mt5, position) for position in positions],
        }

    def history(self, account_id: str, since: datetime, until: datetime) -> dict[str, Any]:
        self.account(account_id)
        if (
            since.tzinfo is None
            or until.tzinfo is None
            or until <= since
            or (until - since).days > 366
        ):
            raise ConnectorError("history requires an aware, ordered range of at most 366 days")
        with self._mutation_lock:
            mt5 = self._gateway.connect()
            deals = mt5.history_deals_get(self._server_bound(since), self._server_bound(until))
        if deals is None:
            raise TerminalUnavailable("broker deal history is unavailable")
        fields = (
            "ticket",
            "order",
            "position_id",
            "time",
            "time_msc",
            "type",
            "entry",
            "magic",
            "symbol",
            "volume",
            "price",
            "profit",
            "commission",
            "swap",
            "fee",
        )
        normalized_deals = []
        for item in deals:
            deal = {field: str(getattr(item, field, "")) for field in fields}
            deal["server_time_raw"] = deal["time"]
            deal["server_time_msc_raw"] = deal["time_msc"]
            deal["time"] = str(int(self._server_timestamp(deal["time"]).timestamp()))
            if deal["time_msc"]:
                deal["time_msc"] = str(
                    int(deal["time_msc"]) - self._settings.server_time_offset_seconds * 1000
                )
            normalized_deals.append(deal)
        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "clock_offset_seconds": self._settings.server_time_offset_seconds,
            "deals": normalized_deals,
        }

    def reconcile_submission(
        self, idempotency_key: str, journal_record: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Find terminal evidence for a submission whose HTTP outcome was uncertain.

        MT5 places the idempotency-derived comment on the order.  Searching both
        active objects and history covers a fill that no longer has an active order
        or position by the time the backend restarts.
        """
        comment = f"ta:{idempotency_key[:24]}"
        with self._mutation_lock:
            mt5 = self._gateway.connect()
            now = datetime.now(UTC)
            since = self._journal_started_at(journal_record) or now - timedelta(days=30)
            try:
                active_orders = mt5.orders_get() or ()
                active_positions = mt5.positions_get() or ()
                history_orders = (
                    mt5.history_orders_get(self._server_bound(since), self._server_bound(now)) or ()
                )
                history_deals = (
                    mt5.history_deals_get(self._server_bound(since), self._server_bound(now)) or ()
                )
            except Exception as exc:  # noqa: BLE001 - MT5 history is an external boundary
                raise TerminalUnavailable("MT5 history is unavailable for reconciliation") from exc

        matching_orders = [
            item
            for item in (*active_orders, *history_orders)
            if str(getattr(item, "comment", "")) == comment
        ]
        matching_deals = [
            item for item in history_deals if str(getattr(item, "comment", "")) == comment
        ]
        matching_positions = [
            item for item in active_positions if str(getattr(item, "comment", "")) == comment
        ]
        order_ids = {self._ticket(getattr(item, "ticket", None)) for item in matching_orders}
        deal_ids = {self._ticket(getattr(item, "ticket", None)) for item in matching_deals}
        position_ids = (
            {self._ticket(getattr(item, "position_id", None)) for item in matching_deals}
            | {self._ticket(getattr(item, "position_id", None)) for item in matching_orders}
            | {self._ticket(getattr(item, "ticket", None)) for item in matching_positions}
        )
        order_ids.discard(None)
        deal_ids.discard(None)
        position_ids.discard(None)

        result = journal_record.get("result") if journal_record is not None else None
        stored_status = result.get("status") if isinstance(result, dict) else None
        matched = bool(order_ids or deal_ids or position_ids)
        if matched:
            resolution = "accepted"
        elif stored_status in {"accepted", "rejected", "unknown"}:
            resolution = stored_status
        else:
            resolution = "unknown"
        return {
            "idempotency_key": idempotency_key,
            "status": resolution,
            "journal_status": journal_record.get("status") if journal_record else "missing",
            "journal_result": result,
            "order_ids": sorted(order_ids),
            "deal_ids": sorted(deal_ids),
            "position_ids": sorted(position_ids),
        }

    @staticmethod
    def _journal_started_at(record: dict[str, Any] | None) -> datetime | None:
        if record is None or not isinstance(record.get("created_at"), str):
            return None
        try:
            value = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        except ValueError:
            return None

    def _order_snapshot(self, mt5: Any, order: Any) -> dict[str, Any]:
        type_map = {
            int(mt5.ORDER_TYPE_BUY_LIMIT): ("buy", "buy_limit"),
            int(mt5.ORDER_TYPE_BUY_STOP): ("buy", "buy_stop"),
            int(mt5.ORDER_TYPE_SELL_LIMIT): ("sell", "sell_limit"),
            int(mt5.ORDER_TYPE_SELL_STOP): ("sell", "sell_stop"),
        }
        side, order_type = type_map.get(int(order.type), ("buy", "market"))
        return {
            "order_id": str(order.ticket),
            "symbol": str(order.symbol),
            "side": side,
            "order_type": order_type,
            "volume": _decimal(order.volume_current),
            "entry_price": _decimal(order.price_open),
            "stop_loss": _decimal(order.sl) if _decimal(order.sl) > 0 else None,
            "take_profit": _decimal(order.tp) if _decimal(order.tp) > 0 else None,
            "status": "pending",
            "created_at": self._server_timestamp(order.time_setup),
        }

    def _position_snapshot(self, mt5: Any, position: Any) -> dict[str, Any]:
        is_buy = int(position.type) == int(mt5.POSITION_TYPE_BUY)
        stop_loss = _decimal(position.sl)
        take_profit = _decimal(position.tp)
        return {
            "position_id": str(position.ticket),
            "symbol": str(position.symbol),
            "side": "buy" if is_buy else "sell",
            "volume": _decimal(position.volume),
            "entry_price": _decimal(position.price_open),
            "stop_loss": stop_loss if stop_loss > 0 else None,
            "take_profit": take_profit if take_profit > 0 else None,
            "profit": _decimal(position.profit),
            "opened_at": self._server_timestamp(position.time),
        }
