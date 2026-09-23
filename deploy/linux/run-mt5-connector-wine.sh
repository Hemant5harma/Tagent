#!/usr/bin/env bash
# Launch the Windows Python connector from the dedicated Wine prefix.  This
# wrapper deliberately contains no account or connector credentials; systemd
# provides them from /etc/trade-agent/connector.env.
set -euo pipefail

: "${WINEPREFIX:=/var/lib/trade-agent/wineprefix}"
: "${DISPLAY:=:99}"
: "${WINE:=/usr/bin/wine}"
: "${WINDOWS_PYTHON:=C:\\Python313\\python.exe}"
: "${MT5_CONNECTOR_JOURNAL_PATH:=C:\\trade-agent\\connector\\mt5_connector_requests.jsonl}"

export WINEPREFIX DISPLAY
# Wine exposes the Linux filesystem as the Z: drive.  The native backend and
# the Windows connector therefore use the same checked-out source, but never
# share a Python environment.
export PYTHONPATH="${PYTHONPATH:-Z:\\opt\\trade-agent\\src}"
# C: is the Wine prefix under /var/lib/trade-agent, the only durable writable
# location granted to the connector service. Do not use the release directory.
export MT5_CONNECTOR_JOURNAL_PATH

exec "${WINE}" "${WINDOWS_PYTHON}" -m mt5_connector.main
