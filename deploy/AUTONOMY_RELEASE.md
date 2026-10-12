# Jarvis autonomy/monitoring release — 2026-10-01

Target: existing `/home/jarvis/trade-agent` on Jarvis, dashboard isolation at
`/opt/trade-agent-dashboard`. Local Python/connector tests and frontend build pass.
The stage includes source, new Alembic revision, built frontend, documentation,
attribution and service template. Protected environment files are excluded.

Installation requires root authorization to update/restart system services and
the isolated dashboard. Before installation:

1. Back up PostgreSQL and backend/dashboard source/assets and the existing protected
   environment, with restricted backup permissions. Record current pause state.
2. Pause backend polling briefly for migration/deployment; leave independent
   position protection running. Apply the additive `20260930_05` migration after
   installing its migration file. Do not reset history, loss breakers or pause state.
3. Copy staged backend/connector sources and built frontend into the existing
   installation; retain all unrelated files/configuration. Copy dashboard modules
   and assets into its existing isolated installation.
4. Update the dashboard PostgreSQL role with only necessary SELECT access to
   autonomous_runs and columns referenced by reporting on proposals/intents.
   Do not grant access to grant payloads, confirmation tokens or notifications.
5. Configure autonomy **shadow** on the existing environment/profile, install the
   autonomy service template, restart connector/backend/reporting/dashboard and
   protection after migration, and start the research service. No demo grant.
6. Verify account remains demo, services/worker heartbeat, fresh broker candles,
   role-isolated dashboard snapshot and owner Telegram monitoring operations.
   Record five-role results, provider limitations and unchanged pause/loss state.

No order submission, trade modification, loss reset or secret replacement is part
of this release. Shadow research uses the existing model's inference allowance.
Automatic approval review rejected using the saved sudo credential from chat
history. Do not perform that admin credential step until the user explicitly
authorizes its reuse.
