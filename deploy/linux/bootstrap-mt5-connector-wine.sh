#!/usr/bin/env bash
# Install the connector's locked Windows dependencies into the Windows Python
# that Wine actually runs. Run this as the tradeagent user before enabling the
# connector service; it neither starts MT5 nor submits an order.
set -euo pipefail

: "${PROJECT_ROOT:=/opt/trade-agent}"
: "${WINEPREFIX:=/var/lib/trade-agent/wineprefix}"
: "${WINE:=/usr/bin/wine}"
: "${UV:=uv}"
: "${WINDOWS_PYTHON:=C:\\Python313\\python.exe}"

requirements_file="${WINEPREFIX}/drive_c/trade-agent/connector-requirements.txt"
windows_requirements='C:\trade-agent\connector-requirements.txt'

if [[ ! -d "${WINEPREFIX}/drive_c" ]]; then
    printf '%s\n' "Wine prefix is not initialized: ${WINEPREFIX}" >&2
    exit 1
fi
if [[ ! -f "${PROJECT_ROOT}/connector/uv.lock" ]]; then
    printf '%s\n' "Connector lockfile is missing: ${PROJECT_ROOT}/connector/uv.lock" >&2
    exit 1
fi

mkdir -p "$(dirname "${requirements_file}")"
"${UV}" export --project "${PROJECT_ROOT}/connector" --locked --no-emit-project \
    --format requirements.txt --output-file "${requirements_file}"

export WINEPREFIX
"${WINE}" "${WINDOWS_PYTHON}" -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version"
"${WINE}" "${WINDOWS_PYTHON}" -m ensurepip --upgrade
"${WINE}" "${WINDOWS_PYTHON}" -m pip install --disable-pip-version-check --require-hashes \
    -r "${windows_requirements}"

# Import only the connector dependencies and code. It cannot contact the MT5
# terminal without connector runtime settings, so this remains a safe bootstrap
# verification.
PYTHONPATH='Z:\opt\trade-agent\src' \
    "${WINE}" "${WINDOWS_PYTHON}" -c "import MetaTrader5, fastapi, mt5_connector.main"
