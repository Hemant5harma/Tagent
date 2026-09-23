# Operations runbook

This runbook deploys a demo-only release first. Keep the backend, connector,
database, and MT5 terminal on one private host during Phase 1. Do not expose the
connector or PostgreSQL to the network.

## Release prerequisites

- Use Python 3.13 and a release containing committed `uv.lock` files for both
  the root project and `connector/` project. Verify them with `uv lock --check`
  and `uv lock --project connector --check` before installation.
- Create PostgreSQL 17 and inject `TRADE_AGENT_DATABASE_URL` through a protected
  environment file or secret store. Use `postgresql+psycopg` in production.
- Configure a dedicated MT5 **demo** account, set `TRADE_AGENT_TRADING_MODE=demo`,
  and verify that `TRADE_AGENT_ACCOUNT_ID` is the account expected by the
  connector.
- Set finite values for the allowed symbols, volume, position, spread, and
  slippage limits before enabling submissions. Set
  `MT5_CONNECTOR_DEVIATION_POINTS` no higher than
  `TRADE_AGENT_MAX_SLIPPAGE_POINTS`; the backend rejects new entries when the
  connector limit is higher. Keep the initial Telegram owner
  to a single private-chat user.
- Run `uv run alembic upgrade head` once against the target database before
  starting the services. The command uses `TRADE_AGENT_DATABASE_URL` from the
  process environment or local `.env`; it never reads a URL from `alembic.ini`.
- Treat the provider readiness check and the connected demo acceptance gate as
  release gates. This repository supplies the checks, but contains no claimed
  connected-broker, Wine, reboot, market-session, or restore result.

The provided [`.env.example`](../.env.example) is a field reference, not a
production secret file. Production environment files should be owned by the
service account (Ubuntu mode `0600`) or only readable by the Windows account
that runs MT5. Do not quote their contents in support requests or logs.

## PostgreSQL

For a local single-host PostgreSQL container, set `POSTGRES_PASSWORD` in a
protected deployment environment and start:

```powershell
docker compose --env-file C:\secure\trade-agent-postgres.env -f docker-compose.postgres.yml up -d
```

On Ubuntu, use the equivalent absolute path outside the release directory. The
compose file binds PostgreSQL to loopback and stores data in a named volume. It
does not configure database authentication for a remote network.

Before an upgrade, stop the backend, take a custom-format backup with the
database credential supplied through the environment, then run migrations:

```text
pg_dump --format=custom --file=trade-agent-before-upgrade.dump "$TRADE_AGENT_DATABASE_URL"
uv run alembic upgrade head
```

Practice restoration on an empty database with `pg_restore --clean --if-exists`
and then start the backend while entries remain paused. Reconciliation must
complete before any new submission is accepted.

## Windows Server setup

Install 64-bit Python 3.13, `uv`, PostgreSQL 17, the broker's 64-bit MT5
terminal, and the current Windows MT5 connector prerequisites. Log in to the
dedicated Windows account that owns the MT5 terminal. MT5 needs that interactive
desktop session, so the supplied Scheduled Tasks intentionally launch at user
logon rather than pretending to be a headless Windows service.

1. Copy the release to `C:\TradeAgent` and create `C:\TradeAgent\.env` from
   the example using protected local secrets. Use a PostgreSQL URL, demo mode,
   `mt5_http` broker mode, loopback connector URL, and a long random connector
   token. Add the connector's `MT5_CONNECTOR_TOKEN`, terminal path, and broker
   login settings to that protected file. Restrict the file ACL to the MT5
   service user.
2. Open MT5 in that same session, sign into the dedicated demo account, and
   enable the broker settings required for automated trading. Confirm its shown
   account id before proceeding.
3. From an elevated PowerShell only when local policy requires it, run
   `deploy\windows\Bootstrap-TradeAgent.ps1 -ProjectRoot C:\TradeAgent`.
   It refuses an unlocked release and creates separate backend and connector
   environments, including the durable connector journal directory.
4. Run `deploy\windows\Register-TradeAgentTasks.ps1 -ProjectRoot C:\TradeAgent`.
   It registers `TradeAgent-Backend` and `TradeAgent-Connector` for the current
   interactive user and starts both tasks.
5. Run `uv run alembic upgrade head` with the production environment loaded,
   then verify `http://127.0.0.1:8100/healthz` and the connector account identity.

Inspect `Get-ScheduledTask -TaskName TradeAgent-*` and Windows Event Viewer after
every reboot. The runner retries a failed process every minute for up to 9,999
attempts; investigate a persistent failure rather than relying on restarts. The
runner does not print secret values. If the interactive user is logged out,
treat the MT5 connector as unavailable and pause entries until the terminal and
connector are healthy again.

## Ubuntu 26.04 setup

Run the native backend as a dedicated `tradeagent` user from
`/opt/trade-agent`. Install Python 3.13, `uv`, PostgreSQL client tools, Wine,
and Xvfb from approved packages. Keep the Wine prefix under
`/var/lib/trade-agent/wineprefix`; it must be owned solely by `tradeagent`.

1. Install the release and its locked root environment:

   ```text
   sudo install -d -o tradeagent -g tradeagent /opt/trade-agent /var/lib/trade-agent
   sudo -u tradeagent uv sync --locked --extra postgres
   sudo install -d -m 0700 /etc/trade-agent
   ```

2. Put backend variables in `/etc/trade-agent/backend.env` and connector-only
   `MT5_CONNECTOR_*` variables in `/etc/trade-agent/connector.env`; apply
   `chmod 600` and ownership `tradeagent:tradeagent`. The connector file
   contains its token, terminal path, and broker login settings, with no Telegram
   or model credentials.
3. Initialize the 64-bit Wine prefix, install a 64-bit Windows Python **3.13** at
   `C:\Python313\python.exe`, and install the broker-supplied 64-bit MT5 terminal:

   ```text
   sudo -u tradeagent env WINEPREFIX=/var/lib/trade-agent/wineprefix WINEARCH=win64 wineboot --init
   ```

   Then run the checked-in bootstrap as `tradeagent`:

   ```text
   sudo -u tradeagent env PROJECT_ROOT=/opt/trade-agent \
     WINEPREFIX=/var/lib/trade-agent/wineprefix \
     WINDOWS_PYTHON='C:\Python313\python.exe' \
     bash /opt/trade-agent/deploy/linux/bootstrap-mt5-connector-wine.sh
   ```

   The bootstrap exports the committed `connector/uv.lock` to hash-checked
   requirements, installs them into this Windows Python under Wine, and verifies
   `MetaTrader5` plus the connector imports. It does not start MT5 or submit an
   order. This Windows Python environment is distinct from
   `/opt/trade-agent/.venv`.
4. Copy the three unit files from `deploy/systemd/` to `/etc/systemd/system/`,
   make both scripts in `deploy/linux/` executable, then run
   `systemctl daemon-reload` and enable `trade-agent-xvfb`,
   `trade-agent-mt5-connector-wine`, and `trade-agent`.
5. With the backend environment loaded, run `uv run alembic upgrade head`.
   Start only monitoring and reconciliation first. Keep new entries paused
   until the validation gate below is complete.

`trade-agent-mt5-connector-wine.service` uses display `:99` and invokes the
Windows Python from the Wine prefix. Its wrapper sets `PYTHONPATH` to the
reviewed Linux source via Wine's `Z:` drive. Change `WINDOWS_PYTHON` only in the
protected connector environment file if the installer uses a different path.
Unless overridden in that file, the connector journal is written to
`C:\trade-agent\connector\mt5_connector_requests.jsonl` inside the Wine prefix,
which maps to `/var/lib/trade-agent/wineprefix/drive_c/` and is writable by the
service sandbox. Do not point the journal at `/opt/trade-agent`.

## AI provider readiness

Select exactly one profile in the protected backend environment file before
enabling AI interpretation. The provider smoke test submits one fully specified
sample message to the selected provider and validates the returned JSON schema.
It imports neither the broker adapter nor execution service, creates no proposal,
and cannot send an order.

- **OpenAI-compatible:** set the endpoint, API key, model, and protocol. The
  endpoint must support the selected Responses or Chat Completions protocol.
- **OpenCode:** start the separately managed OpenCode server on its configured
  private URL, authenticate/configure its own upstream provider, then set model
  provider and model in the backend environment. The Trade Agent service does
  not install, supervise, or expose OpenCode.
- **Codex:** install the `codex` command and authenticate it as the same Windows
  interactive user or Ubuntu `tradeagent` user that will run the check. The
  smoke test starts the local app-server protocol using that user’s existing
  Codex authentication; it does not receive broker, database, or Telegram
  secrets.

Run the check on Windows:

```powershell
.\deploy\windows\Test-ProviderReadiness.ps1 -ProjectRoot C:\TradeAgent
```

Run it on Ubuntu with the protected backend environment file:

```text
sudo -u tradeagent /opt/trade-agent/.venv/bin/python \
  /opt/trade-agent/deploy/provider_smoke.py \
  --env-file /etc/trade-agent/backend.env
```

Record the provider, model, executable/server version, and pass/fail result in
the release record. A failed or unavailable provider leaves structured commands,
monitoring, deadline processing, and broker reconciliation available; it must
not be treated as permission to bypass confirmation.

## Mandatory MT5/Wine acceptance gate

Ubuntu deployment is not accepted merely because the process starts. Record the
following against the exact Ubuntu, Wine, Windows Python, MT5 terminal, and
broker build versions in the release ticket:

| Check | Required result |
| --- | --- |
| Account identity | Connector reports the configured dedicated demo account |
| Symbols and quote | An allowed symbol has valid bid/ask and instrument specification |
| Order lifecycle | Demo market and pending orders carry SL/TP, then cancel/close correctly |
| Reconnect | Stop/start terminal and connector; reconciliation finds the same order/position |
| Deadline | An overdue pending order is cancelled and an overdue position is closed once |
| Reboot | After a host reboot, Xvfb, terminal, connector, backend, and reconciliation recover without a duplicate order |

Record the broker order/deal/position identifiers and service logs for each
test using [ACCEPTANCE_RECORD.md](ACCEPTANCE_RECORD.md). If any result is missing or unexpected, leave entries paused and diagnose
before attempting another order. A live account needs a separate deployment
approval and is not enabled by passing this demo gate.

The release record must additionally show all of the following before Phase 1
is called accepted:

- the Windows and Ubuntu CI run for the release commit;
- one full monitored demo market session on each target platform;
- controlled connector disconnect and backend restart drills, with ledger
  reconciliation and no duplicate submission;
- successful PostgreSQL backup restoration into a clean database, migration to
  the release revision, and subsequent reconciliation; and
- the selected provider readiness result, if AI interpretation is enabled.

## Normal operation and incident response

At startup, after every connector reconnect, and after restoring a database,
compare MT5 orders, positions, and deals against the ledger. Any unexpected
external account activity suspends affected automation until the position owner
and state are resolved.

Use the Telegram status and pause operations for normal control. Pausing blocks
new entries only; monitoring, cancellation, stop-loss/take-profit, and confirmed
deadline closures continue. Provider or Telegram failures must be investigated,
but they do not justify stopping reconciliation or deadline processing.

For an uncertain order submission, record it as unknown, inspect MT5 history and
current state, then reconcile the existing intent. Never resend the original
request just because a timeout occurred. For a failed close, retain the exit
intent, determine whether MT5 already executed it, and retry only when its state
is known.

Before a planned shutdown, pause entries, wait for any submission in progress to
settle, check the outbox and active deadline jobs, then stop the connector before
the backend. On unplanned recovery, start the connector first and permit the
backend to reconcile before accepting a new confirmed trade.
