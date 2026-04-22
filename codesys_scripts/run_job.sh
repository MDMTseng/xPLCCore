#!/usr/bin/env bash
# run_job.sh <template-name> [timeout-sec]
#
# Copies codesys_scripts/jobs/templates/<name>.py into jobs/inbox/ with a
# timestamp prefix, then polls jobs/done/ for the resulting <stamped>.log.
# Requires watcher.py to be running inside an open CODESYS IDE.
#
# Example:  ./run_job.sh build

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPL_DIR="$HERE/jobs/templates"
INBOX="$HERE/jobs/inbox"
DONE="$HERE/jobs/done"

name="${1:-}"
timeout_sec="${2:-120}"
if [ -z "$name" ]; then
  echo "usage: $0 <template-name> [timeout-sec]" >&2
  ls "$TEMPL_DIR" 2>/dev/null >&2
  exit 2
fi

template="$TEMPL_DIR/$name.py"
if [ ! -f "$template" ]; then
  echo "no template: $template" >&2
  exit 2
fi

mkdir -p "$INBOX" "$DONE"
stamp="$(date +%Y%m%d-%H%M%S)"
job_name="${stamp}-${name}"
inbox_path="$INBOX/${job_name}.py"
log_path="$DONE/${job_name}.log"

cp "$template" "$inbox_path"
echo "submitted: $inbox_path"

# Poll for the log
t0=$(date +%s)
while true; do
  if [ -f "$log_path" ]; then
    echo "---- log (${job_name}) ----"
    cat "$log_path"
    echo "---- end ----"
    exit 0
  fi
  now=$(date +%s)
  elapsed=$((now - t0))
  if [ "$elapsed" -gt "$timeout_sec" ]; then
    echo "TIMEOUT after ${elapsed}s; no log at $log_path" >&2
    echo "Is watcher.py running inside CODESYS?" >&2
    exit 1
  fi
  sleep 0.5
done
