#!/usr/bin/env bash
# Headless CODESYS build. Thin wrapper -- all paths come from
# codesys_env.json via config.py, so there is no second place to update
# when the CODESYS version changes.
#
# v1 hardcoded the exe path, the profile name and the project path here,
# which is why this script silently targeted SP21 on an SP22 machine.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "$HERE/supervisor.py" build "$@"
