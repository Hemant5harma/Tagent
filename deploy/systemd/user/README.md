# User services for the Jarvis MT5 deployment

These units use an existing installation at `$HOME/trade-agent`, with Wine in
`wineprefix`, Windows Python at `C:\Python313`, and MT5 at `C:\MetaTrader5`.
Install them into `~/.config/systemd/user/`. The host must have user lingering
enabled so the services start at boot without an interactive login.

The protected `trade-agent/connector.env` file supplies the connector token,
expected MT5 login/server/password, host `127.0.0.1`, port `8101`, and
`MT5_CONNECTOR_PORTABLE=true`. Set `PYTHONPATH` to the Windows `Z:` path of the
deployment's `src` directory. Give the request journal an account-specific path.
Keep this file at mode `0600`; never commit its values.

The terminal reads `wineprefix/drive_c/trade-agent-terminal.ini` in UTF-16 with
`[Common]` login, server, password and `KeepPrivate=1`. Its `[Experts]` section uses
`Enabled=1`, `AllowLiveTrading=1`, `AllowDllImport=0`, `Account=1`, and `Profile=1`.
`AllowLiveTrading` is MT5's name for programmatic-trading permission; the backend
must separately enforce `TRADE_AGENT_TRADING_MODE=demo` and the expected account.
Protect this startup file with mode `0600` as well.

After stopping any temporary services using display `:99` or this Wine prefix:

```sh
systemctl --user daemon-reload
systemctl --user enable --now trade-agent-display.service trade-agent-terminal.service trade-agent-connector.service
```

Before switching the backend, verify authenticated connector account reads,
`is_demo`, the expected account ID, trading permissions, and a current quote.
Use a separate migrated ledger for Vantage to avoid replaying fake-broker
proposals or execution intents. Set the backend broker to `mt5_http`, use the
same connector token, and restart the backend. Preserve the old fake ledger.

Verify `/readyz`, the authenticated backend account endpoint, and the Telegram
account handler. A broker `order_check` verifies preflight without submitting an
order. Service restart checks do not substitute for a full host reboot test.
