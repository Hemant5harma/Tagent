"""Conservative recovery of explicit fields, never broker prices or risk decisions."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

from trade_agent.ai import ParsedEntry, TradeProposalParse

NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"


def explicit_draft(message: str) -> TradeProposalParse:
    text = message.lower()

    def unique(pattern: str) -> Decimal | None:
        values = {Decimal(v) for v in re.findall(pattern, text)}
        return next(iter(values)) if len(values) == 1 and next(iter(values)) > 0 else None

    sides = set(re.findall(r"\b(buy|sell)\b", text))
    symbols = set(re.findall(r"\b((?:eur|usd|gbp|jpy|chf|cad|aud|nzd|xau|xag){2})\b", text))
    volume = unique(rf"\b(?:lots?|volume|size)\s*[:=]?\s*({NUMBER})(?![\d.])")
    after = unique(rf"(?<![\d.])({NUMBER})\s*lots?\b")
    if volume is None:
        volume = after
    elif after is not None and after != volume:
        volume = None
    entry_price = unique(rf"\bentry\s*(?:at|price|=|:)?\s*({NUMBER})(?![\d.])")
    trigger_price = unique(
        rf"\b(?:price\s+(?:rises?|falls?|drops?|reaches?|hits?)(?:\s+to)?|(?:buy|sell)\s+(?:limit|stop)(?:\s+at)?)\s+({NUMBER})(?![\d.])"
    )
    entry_conflict = (
        entry_price is not None and trigger_price is not None and entry_price != trigger_price
    )
    entry_price = None if entry_conflict else entry_price or trigger_price
    order_types = re.findall(r"\b(buy|sell)\s+(limit|stop)\b", text)
    requested_type = "_".join(order_types[0]) if len(set(order_types)) == 1 else None
    if requested_type is None and len(sides) == 1 and trigger_price is not None:
        side = next(iter(sides))
        if re.search(r"\bprice\s+rises?\b", text):
            requested_type = "buy_stop" if side == "buy" else "sell_limit"
        elif re.search(r"\bprice\s+(?:falls?|drops?)\b", text):
            requested_type = "buy_limit" if side == "buy" else "sell_stop"
    expiry = None
    expiry_match = re.search(
        r"\bexpir(?:e|es|y)\s+(?:in\s+)?(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h)\b", text
    )
    if expiry_match:
        amount = int(expiry_match[1]) * (60 if expiry_match[2].startswith("h") else 1)
        if 1 <= amount <= 10080:
            expiry = (datetime.now(UTC) + timedelta(minutes=amount)).isoformat()
    iso_expiry = re.search(
        r"\bexpir(?:e|es|y)\s+(?:at\s+)?(\d{4}-\d\d-\d\dt\d\d:\d\d(?::\d\d)?(?:z|[+-]\d\d:\d\d))",
        text,
    )
    if iso_expiry:
        expiry = datetime.fromisoformat(iso_expiry[1].upper().replace("Z", "+00:00")).isoformat()
    expiry_unclear = bool(re.search(r"\bexpir(?:e|es|y)\b", text)) and expiry is None
    market = bool(re.search(r"\b(?:at\s+market|entry\s+market|market\s+order)\b", text))
    entry = ParsedEntry(kind="missing")
    if market and entry_price is None:
        entry = ParsedEntry(kind="market")
    elif entry_price is not None and not market:
        entry = ParsedEntry(kind="price", price=entry_price)
    stop = unique(
        rf"\b(?:sl|stop[ -]?loss)\s*(?:at|price|=|:)?\s*({NUMBER})(?![\d.]|\s*(?:\$|usd|dollars?|€|eur\b))"
    )
    target = unique(rf"(?:\$\s*({NUMBER})\s*(?:profit|take[ -]?profit|tp)\b)")
    alternate = unique(
        rf"(?<![\d.])({NUMBER})\s*(?:\$|usd\b|dollars?\b)\s*(?:profit|take[ -]?profit|tp)\b"
    )
    leading = unique(
        rf"\b(?:profit(?:\s+target)?|take[ -]?profit|tp)\s*(?:of|=|:)?\s*\$\s*({NUMBER})(?![\d.])"
    )
    leading_usd = unique(
        rf"\b(?:profit(?:\s+target)?|take[ -]?profit|tp)\s*(?:of|=|:)?\s*({NUMBER})\s*(?:usd|dollars?)\b"
    )
    money = {v for v in (target, alternate, leading, leading_usd) if v is not None}
    target = next(iter(money)) if len(money) == 1 else None
    invalid_money = bool(
        re.search(
            rf"[€£₹]|{NUMBER}\s*(?:eur|gbp|inr)\b|[-+]\s*{NUMBER}\s*(?:\$|usd|dollars?)", text
        )
    )
    if invalid_money:
        target = None
    tp = unique(
        rf"\b(?:tp|take[ -]?profit)\s*(?:at|price|=|:)?\s*({NUMBER})(?![\d.]|\s*(?:\$|usd|dollars?|€|eur\b))"
    )
    conflict = target is not None and tp is not None
    ambiguous = bool(
        re.search(r"\bprofit\b", text) and re.search(NUMBER, text) and target is None and tp is None
    )
    if conflict or invalid_money:
        target, tp = None, None
    return TradeProposalParse(
        intent="clarification" if entry_conflict or expiry_unclear else "trade",
        symbol=next(iter(symbols)).upper() if len(symbols) == 1 else None,
        side=next(iter(sides)) if len(sides) == 1 else None,
        volume=volume,
        entry=entry,
        stop_loss=stop,
        take_profit=tp,
        profit_target_usd=target,
        pending_expires_at=expiry,
        requested_order_type=cast(
            Literal["buy_limit", "buy_stop", "sell_limit", "sell_stop"] | None, requested_type
        ),
        clarification=(
            "Please give one entry price and an expiry such as 'expires in 2 hours' or an ISO timestamp with timezone."
            if entry_conflict or expiry_unclear
            else "Choose one target: a TP price or a USD profit amount."
            if conflict
            else "Please specify a TP price or an explicit USD profit target; other currencies are not supported."
            if ambiguous or invalid_money
            else None
        ),
    )


def has_profit_amount(message: str) -> bool:
    return bool(
        re.search(r"\b(?:profit|tp)\b", message, re.I)
        and (
            re.search(r"\$|€|£|₹|\b(?:usd|dollars?|eur|gbp|inr)\b", message, re.I)
            or re.search(rf"{NUMBER}\s+profit\b", message, re.I)
        )
    )
