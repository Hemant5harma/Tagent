# SPEC-011 verification — 2026-10-01

Implemented and tested locally; not deployed to Jarvis in this session. No
external Telegram messages or connected trade requests were sent.

- M01: `tests/test_monitoring.py` verifies all three facade commands reject a
  different owner and monitoring leaves the fake broker book unchanged. Existing
  private Telegram gateway/inbox regressions remain passing.
- M02: Tests distinguish absent, running, stale, account-mismatched and gracefully
  stopped heartbeats. A stalled research call does not prevent heartbeat writes.
- M03: Dashboard snapshot exposes `autonomy_worker`; its stopped state is tested.
  Frontend System shows observed mode/phase and worker freshness, Overview compares
  manual/autonomous outcomes side by side. TypeScript/Vite build passed.
- M04: Blocked demo research enters the existing idempotent notification outbox;
  shadow/WAIT runs do not gain notifications. Existing uncertainty/risk controller
  regression tests pass. Connected notification delivery is not verified.
- M05: `docs/AUTONOMY.md` documents commands, reporting service and Jarvis template
  `deploy/systemd/trade-agent-autonomy.service`.

Commands/results:

- `python -m pytest -q --basetemp=.pytest-dual-monitoring-full`: 230 passed.
- After final performance text/UI additions, monitoring/controller/reporting/inbox
  targeted regression: 21 passed.
- `python -m ruff check src connector tests migrations`: passed.
- `python -m mypy src/trade_agent src/mt5_connector`: 41 files passed.
- `npm.cmd run build` in frontend: passed.

Local process/listener inventory found no Python trading processes or listeners
on 8100/8101/8102. Jarvis is the user-selected deployment target, but no Jarvis SSH
alias/address was available locally; its running status remains unverified until
connection details are provided. Existing protected credentials should be reused.

## Jarvis read-only follow-up

Recovered connection from the existing “Connect to Jarvis via SSH” chat and
verified SSH key access on 2026-10-01. Backend, protection, reporting, dashboard
and user connector services are active; autonomy service is inactive and its
modules/settings are not installed. The account reports USD demo, seven open
positions, zero pending orders and entries paused. Existing Telegram/PostgreSQL
and OpenAI-compatible model configuration are present.

Prepared and staged the tested release under
`/home/jarvis/trade-agent/releases/autonomy-monitoring-20261001`, with matching
local/remote SHA256 `206162ebc2ca3631a16cd7ec0aebc7e78ecab2f37dbcfeca7812baee635e1318`.
This staging does not alter installed code, environment, schema or service state.
Deployment plan: `deploy/AUTONOMY_RELEASE.md`.

Automatic approval review rejected entering the saved password into sudo,
citing credential reuse from untrusted prior chat without explicit authorization.
No workaround or privileged installation was attempted. Installation/restarts
remain pending explicit authorization to reuse the saved sudo credential.

## Installed and verified — 2026-10-02

User explicitly approved saved sudo password reuse and Jarvis deployment.
Deployment/migration completed, services restarted, and autonomy is running in
shadow mode. The read-only website role received only needed report SELECT
permissions; grant payloads and confirmation tokens remain unavailable to it.

Connected facade checks passed for `/autonomy`, `/performance all`, `/decisions`.
Telegram polling/protection heartbeat is fresh. These checks did not send external
test messages; the owner's next commands exercise live Telegram delivery.

Live authenticated website testing passed across Overview/Trades/Agent/System,
with separate origin summaries, fresh worker heartbeat and reports. Fixed actual
decision rendering to read `direction` (with legacy `action` fallback), and added
readable role reports, final reasons and timestamped evidence. Static assets were
published without another service/tunnel restart. The final browser smoke test
checked all five role sections plus market evidence and a 390px mobile viewport,
with zero JavaScript errors. Screenshots: `dist/live-monitoring/`.

The dashboard's Requires dependency stopped the quick tunnel when the website
service stopped; starting the tunnel restored public access. Installation helper
now starts it with the website. Verified public URL at deployment:
`https://month-affairs-nav-beside.trycloudflare.com` (temporary, may change).
Use Telegram `/dashboard` for a current URL and one-use login code.

Current services all active; entries still paused; no autonomy execution grant.
Deployment helper lint and frontend TypeScript/Vite build passed.
