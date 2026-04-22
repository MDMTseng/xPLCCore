#!/usr/bin/env bash
# Usage: ./build.sh
# Runs a headless CODESYS build of PackerX.project and prints the message dump.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODESYS_EXE="/c/Program Files/CODESYS 3.5.21.20/CODESYS/Common/CODESYS.exe"
PROFILE="CODESYS V3.5 SP21 Patch 20"
PROJECT="C:\\Users\\X1\\Desktop\\XPack2_codesys\\PackerX.project"
LOG="$HERE/build.log"
SCRIPT="$HERE/build.py"

rm -f "$LOG"

# --noUI dropped intentionally — IDE window stays visible so you can watch.
# Side effect: CODESYS does not auto-exit after the script, so we run in the
# background and read back the log file once the script has written it.
"$CODESYS_EXE" \
  --profile="$PROFILE" \
  --runscript="$SCRIPT" \
  --scriptargs="$PROJECT $LOG"

rc=$?
echo "---- build.log ----"
[ -f "$LOG" ] && cat "$LOG" || echo "(no log written)"
echo "---- codesys exit: $rc ----"
exit "$rc"
