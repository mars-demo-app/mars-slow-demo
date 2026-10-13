#!/usr/bin/env bash
# Fire HTTP requests at a host for a fixed duration (default 72 hours).
#
# Usage:
#   ./loadgen.sh [ip-or-host] [options]
#
# Examples:
#   ./loadgen.sh
#   ./loadgen.sh 203.0.113.10
#   ./loadgen.sh 203.0.113.10 -c 20 -p /work -d 30m
#   ./loadgen.sh lb.example.com -c 5 --https
#
# Prefer: make load-test  (runs this script in a Docker container)

set -euo pipefail

DEFAULT_HOST="209.38.1.176"

usage() {
  cat <<EOF
Usage: ./loadgen.sh [ip-or-host] [options]

Arguments:
  ip-or-host            Target host (default: ${DEFAULT_HOST})

Options:
  -c, --concurrency N   Parallel workers (default: 5)
  -d, --duration DUR    How long to run, e.g. 30m, 1800, 1h, 72h (default: 72h)
  -p, --path PATH       Request path (default: /work)
  -P, --port PORT       Port (default: 80, or 443 with --https)
      --https           Use https:// instead of http://
  -h, --help            Show this help

Each worker loops curl until the duration elapses. Slow backends are fine;
curl waits for each response before the next request on that worker.
EOF
}

parse_duration_secs() {
  local raw="$1"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    echo "$raw"
    return
  fi
  if [[ "$raw" =~ ^([0-9]+)([smh])$ ]]; then
    local n="${BASH_REMATCH[1]}"
    local u="${BASH_REMATCH[2]}"
    case "$u" in
      s) echo "$n" ;;
      m) echo $((n * 60)) ;;
      h) echo $((n * 3600)) ;;
    esac
    return
  fi
  echo "Invalid duration: $raw (use seconds, or Ns/Nm/Nh)" >&2
  exit 2
}

HOST=""
CONCURRENCY=5
DURATION="72h"
PATH_Q="/work"
PORT=""
SCHEME="http"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -c|--concurrency)
      CONCURRENCY="${2:?}"
      shift 2
      ;;
    -d|--duration)
      DURATION="${2:?}"
      shift 2
      ;;
    -p|--path)
      PATH_Q="${2:?}"
      shift 2
      ;;
    -P|--port)
      PORT="${2:?}"
      shift 2
      ;;
    --https)
      SCHEME="https"
      shift
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$HOST" ]]; then
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 2
      fi
      HOST="$1"
      shift
      ;;
  esac
done

if [[ -z "$HOST" ]]; then
  HOST="$DEFAULT_HOST"
fi

if [[ -z "$PORT" ]]; then
  if [[ "$SCHEME" == "https" ]]; then
    PORT=443
  else
    PORT=80
  fi
fi

DURATION_SECS="$(parse_duration_secs "$DURATION")"
URL="${SCHEME}://${HOST}:${PORT}${PATH_Q}"
END_AT=$((SECONDS + DURATION_SECS))
OK_FILE="$(mktemp)"
FAIL_FILE="$(mktemp)"
: >"$OK_FILE"
: >"$FAIL_FILE"

pids=()
stopping=0

cleanup() {
  trap - EXIT INT TERM HUP
  stopping=1
  local pid
  for pid in "${pids[@]:-}"; do
    # Kill curl children first, then the worker shell.
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  rm -f "$OK_FILE" "$FAIL_FILE"
}

on_signal() {
  echo >&2
  echo "Interrupted; stopping workers..." >&2
  cleanup
  exit 130
}

trap cleanup EXIT
trap on_signal INT TERM HUP

echo "Target:       $URL"
echo "Concurrency:  $CONCURRENCY"
echo "Duration:     ${DURATION_SECS}s ($DURATION)"
echo "Started:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

worker() {
  local id="$1"
  local ok=0
  local fail=0
  local response
  local body
  local status
  # Propagate Ctrl-C / kill into this background worker and its curl.
  trap 'exit 130' INT TERM HUP
  while (( SECONDS < END_AT )); do
    if response=$(curl -sS -w $'\n%{http_code}' --connect-timeout 5 --max-time 120 "$URL"); then
      status="${response##*$'\n'}"
      body="${response%$'\n'*}"
      if [[ "$status" =~ ^2[0-9][0-9]$ ]]; then
        ok=$((ok + 1))
        printf '[worker %s] status=%s %s\n' "$id" "$status" "$body"
      else
        fail=$((fail + 1))
        printf '[worker %s] status=%s %s\n' "$id" "$status" "$body" >&2
        # A controlled 503 means the app is at its configured memory budget.
        # Back off so a long demo does not turn expected saturation into a
        # tight retry loop and an excessive volume of rejection logs.
        sleep 1
      fi
    else
      fail=$((fail + 1))
      printf '[worker %s] request failed\n' "$id" >&2
    fi
  done
  echo "$ok" >>"$OK_FILE"
  echo "$fail" >>"$FAIL_FILE"
}

for i in $(seq 1 "$CONCURRENCY"); do
  worker "$i" &
  pids+=("$!")
done

wait || true
if (( stopping )); then
  exit 130
fi
echo "Finished."

ok_total=0
fail_total=0
while read -r n; do ok_total=$((ok_total + n)); done <"$OK_FILE"
while read -r n; do fail_total=$((fail_total + n)); done <"$FAIL_FILE"

echo
echo "Ended:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Successful:   $ok_total"
echo "Failed:       $fail_total"
