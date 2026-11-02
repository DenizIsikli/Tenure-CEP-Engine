#!/usr/bin/env bash
# Sends one fake publication event to Siddhi
. "$(dirname "$0")/common.sh"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "${SIDDHI_URL:-http://localhost:8006/publications}" \
  -H "Content-Type: application/json" \
  -d '{"event":{"authorId":"A123","workId":"W456","publicationDate":"2026-10-01"}}'
