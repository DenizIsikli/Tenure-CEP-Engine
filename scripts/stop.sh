#!/usr/bin/env bash
# Stops the editor and the runner
. "$(dirname "$0")/common.sh"
docker rm -f "$EDITOR_NAME" "$RUNNER_NAME" 2>/dev/null || true
echo "Stopped."
