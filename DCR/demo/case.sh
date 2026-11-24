#!/bin/sh
# Plays the users of the PAIS for one tenure-track case.
# Usage: case.sh setup | finish
P=${PAIS:-http://pais:8090}
S=${SIDDHI:-http://siddhi:8006}
LOG=${LOG_FILE:-/logs/p4_proof_log.txt}
C=case1

log() {  # append one line to the shared proof log
  echo "$(date -u '+%Y-%m-%d %H:%M:%S') [USER] $1 | case=$C" >> "$LOG"
}

wait_for() {  # wait until a URL answers at all (POST, since Siddhi's HTTP sources reject GET)
  until [ "$(curl -s -o /dev/null -w '%{http_code}' -XPOST "$1")" != "000" ]; do sleep 2; done
}

if [ "$1" = "setup" ]; then
  echo "=== P4 tenure-track demo, run started $(date -u '+%Y-%m-%d %H:%M:%S') (UTC) ===" > "$LOG"
  echo "Waiting for PAIS and Siddhi..."
  wait_for "$P/health"; wait_for "$S/ready"
  log "HR creates case and runs the first years"
  curl -s -XPOST "$P/cases" -d '{"caseId":"'$C'","authorId":"A5069386704","start":"2021-01-01"}'
  for e in start plan mentor vision udtu portfolio midterm; do
    curl -s -o /dev/null -XPOST "$P/cases/$C/events/$e"
  done
  curl -s -XPOST "$P/cases/$C/advance" -d '{"days":1827}'
  log "employee requests assessment"
  echo "Request assessment:";              curl -s -XPOST "$P/cases/$C/events/req"
  log "committee tries to start assessment"
  echo "Assessment before research output:"; curl -s -XPOST "$P/cases/$C/events/a1"
fi

if [ "$1" = "finish" ]; then
  echo "Waiting for research output to unblock the assessment..."
  until [ "$(curl -s -o /dev/null -w '%{http_code}' -XPOST "$P/cases/$C/events/a1")" = "200" ]; do sleep 2; done
  log "assessment unblocked by research output and started"
  echo "First academic assessment executed"
  log "committee finds employee qualified, HR transfers"
  curl -s -XPOST "$P/cases/$C/events/q1"
  curl -s -XPOST "$P/cases/$C/events/transfer"
  mkdir -p /output && curl -s "$P/log.xes" > /output/p4_log.xes
  log "event log exported to output/p4_log.xes"
  echo "Event log written to output/p4_log.xes"
fi
