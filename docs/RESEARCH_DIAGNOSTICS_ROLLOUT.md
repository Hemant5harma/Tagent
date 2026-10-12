# Finish the Jarvis research-diagnostics rollout

The reporting worker already publishes a read-only 30-day shadow-decision
summary and the two reproducible XAUUSD screening scenarios are cached. The
owner-only Telegram `/diagnostics` code and the mobile dashboard build are
staged, but two root-owned system services require an interactive operator
restart/publication. Do not use an unattended `SIGKILL` to bypass this boundary.

On Jarvis, after confirming the MT5 account is flat and there are no unresolved
submissions, run these commands in an interactive terminal as `jarvis`:

```bash
sudo systemctl restart trade-agent.service
sudo install -d -m 0700 /home/jarvis/trade-agent/backups/research-diagnostics-20261011-root
sudo cp -a /opt/trade-agent-dashboard/trade_agent/reporting.py /home/jarvis/trade-agent/backups/research-diagnostics-20261011-root/reporting.py
sudo cp -a /opt/trade-agent-dashboard/frontend/index.html /home/jarvis/trade-agent/backups/research-diagnostics-20261011-root/index.html
sudo install -o root -g root -m 0644 /home/jarvis/trade-agent/src/trade_agent/reporting.py /opt/trade-agent-dashboard/trade_agent/reporting.py
sudo install -o root -g root -m 0644 /home/jarvis/trade-agent/releases/research-diagnostics-20261011-v2/dist/assets/index-B78QxcgA.css /opt/trade-agent-dashboard/frontend/assets/index-B78QxcgA.css
sudo install -o root -g root -m 0644 /home/jarvis/trade-agent/releases/research-diagnostics-20261011-v2/dist/assets/index-UrWaI5RA.js /opt/trade-agent-dashboard/frontend/assets/index-UrWaI5RA.js
sudo install -o root -g root -m 0644 /home/jarvis/trade-agent/releases/research-diagnostics-20261011-v2/dist/index.html /opt/trade-agent-dashboard/frontend/index.html
sudo systemctl restart trade-agent-dashboard.service
```

These copies add only two hashed static assets and replace one read-only
reporting module and `index.html`; they do not touch the trading API, MT5
connector, protection worker, autonomy grant, or risk limits. The staged
reporting module SHA-256 is
`7454d3e9436ffd4e02171e11c437dfb14518f95024d8c773f8d005abda0dc8b2`.
The staged dashboard `index.html` SHA-256 is
`d5485220f7301c5fc8902865a505c6d602c31c63aab4690b9fb03770bea79094`.

Verify `systemctl is-active trade-agent.service trade-agent-dashboard.service
trade-agent-reporting.service`. In Telegram, `/diagnostics` should show the
30-day counts and a negative offline comparator; `/dashboard` should open the
read-only Agent page with the same diagnostics. Zero runs in the past 24 hours
is expected while no fresh closed market bar has been analyzed, but do not
assume market closure without checking the feed.

If the public dashboard fails after publication, restore the two backed-up
files with `sudo install -o root -g root -m 0644` and restart only
`trade-agent-dashboard.service`. Old hashed assets were not removed.
