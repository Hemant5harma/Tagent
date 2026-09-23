# MT5 connector

This is the only process allowed to install and import `MetaTrader5`.  Bind it to a
loopback address and give the backend the same `MT5_CONNECTOR_TOKEN` through its
`TRADE_AGENT_MT5_CONNECTOR_TOKEN` setting.  Do not expose port 8101 to a network.

Required environment variables:

```text
MT5_CONNECTOR_TOKEN=<long-random-secret>
MT5_CONNECTOR_TERMINAL_PATH=C:\\Program Files\\Broker MT5\\terminal64.exe
MT5_CONNECTOR_LOGIN=<numeric-account-login>
MT5_CONNECTOR_PASSWORD=<broker-password>
MT5_CONNECTOR_SERVER=<broker-server-name>
MT5_CONNECTOR_JOURNAL_PATH=C:\\trade-agent\\data\\mt5_connector_requests.jsonl
```

`MT5_CONNECTOR_LOGIN`, password, server, and terminal path may be omitted only if
the MT5 terminal is already configured and logged in within this Windows user
session. The account id configured in the backend must equal the terminal's numeric
MT5 login.

The journal records `started` before every order, protection change, cancellation,
or close reaches MT5 and fsyncs the terminal result afterwards. A request found as
`started` after a crash is returned as `unknown`; it is never sent a second time.
The backend should provide a durable idempotency key in the request body or
`X-Idempotency-Key` header for every mutation. When it cannot, the connector derives
one from the exact mutation request. Reconcile an unknown submission through
`GET /v1/reconciliation/{idempotency_key}` before any manual retry.

Start it with the connector environment activated. The connector project
installs only the Windows-only MT5 dependencies; `PYTHONPATH` points at the
reviewed connector source in the root project:

```powershell
$env:PYTHONPATH = (Resolve-Path ..\src).Path
uv run --project connector python -m mt5_connector.main
```
