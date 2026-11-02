#!/usr/bin/env bash
# Starts the OpenAlex poller: scripts/run_poller.sh <AUTHOR_ID> [SINCE_DATE]
. "$(dirname "$0")/common.sh"
[ $# -ge 1 ] || { echo "Usage: $0 <AUTHOR_ID> [YYYY-MM-DD]"; exit 1; }
exec python3 "$ROOT/sources/openalex/openalex_poller.py" "$@"
