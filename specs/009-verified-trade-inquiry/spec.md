# 009 — Verified trade inquiry during model outages

## Problem

A confirmed Vantage demo order can fill, but a later question such as “tell me about this order” was routed to the conversational model. When the model failed, the reply said “No trade was submitted,” falsely implying the earlier fill had not happened.

## Required behavior

- Identify a natural-language request about *this/last* trade or an explicit ticket as a read-only query, before using the model or trade parser.
- For “this trade,” select the latest submitted execution intent owned by the authorized Telegram user on the configured account. Do not select an unrelated external position merely because it is open.
- Read current broker positions/orders to report the matching ticket, side, size, entry, broker-held SL/TP, and floating P/L when open. Distinguish an open position from a pending order.
- If the ticket is absent from live open positions/orders, report closure and cost-inclusive realized P/L only when a complete entry/exit broker-deal lifecycle is present. Otherwise do not infer closure; state that history must be checked.
- If the model provider is unavailable for other questions, say only that the *current message* made no broker changes; do not deny earlier submissions.
- Inquiry and provider failure must never create, confirm, close, or change a broker order.

## Verification

- Regression with an unrelated external position plus a recent owned fill chooses the owned ticket and reports its protection.
- The model parse/explain methods are not called for the inquiry.
- A fully closed lifecycle yields net realized P/L after recorded costs.
- A model outage does not claim that no prior trade was submitted.
- Validate the production ticket against a read-only live broker snapshot after deployment.
