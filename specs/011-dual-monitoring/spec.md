# SPEC-011 — Telegram and website dual monitoring

- M01: Owner-authenticated Telegram `/autonomy`, `/performance [manual|autonomous|all]`
  and `/decisions` report operational status, separate realized outcomes and recent
  research reasons without invoking a model or creating orders.
- M02: Persist an account-labelled autonomy worker heartbeat independently of
  model calls. Both channels distinguish fresh, stale, stopped and never observed;
  configuration and a recorded decision alone never prove a worker is running.
- M03: Website System view exposes the heartbeat and actual observed autonomy
  mode. Existing attribution filters and decision evidence remain available.
- M04: Demo research blocks reach the existing notification outbox. Do not send
  routine shadow/WAIT notifications. No external messages are sent by implementation
  tests and no deployment or connected order activation is implied.
- M05: Document Telegram commands, reporting process and remaining deployment
  prerequisites. Verify authorization, stale heartbeat, monitoring non-execution
  and existing manual/autonomous regressions.
