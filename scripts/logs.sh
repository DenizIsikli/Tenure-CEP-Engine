#!/usr/bin/env bash
# Follows the logs: scripts/logs.sh editor | runner
. "$(dirname "$0")/common.sh"
case "${1:-runner}" in
  editor) docker logs -f "$EDITOR_NAME" ;;
  *)      docker logs -f "$RUNNER_NAME" ;;
esac
