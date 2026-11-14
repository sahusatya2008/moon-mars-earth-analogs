#!/usr/bin/env bash
# Keep an independent Cloudflare Quick Tunnel alive for SNSX SPACE.
#
# The tunnel is separate from Tailscale and every other service on this Mac.
# If cloudflared exits, this supervisor restarts it and republishes the new
# https://<random>.trycloudflare.com URL to .run/public-url.txt.
#
# Cloudflare Quick Tunnels buffer SSE by design. The analysis workspace polls
# stored job events every 2 seconds, so live progress still updates in the UI.
set -u

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUN_DIR="$ROOT/.run"
PID_DIR="$RUN_DIR/pids"
LOG_DIR="$RUN_DIR/logs"
URL_FILE="$RUN_DIR/public-url.txt"
CHILD_PID_FILE="$PID_DIR/tunnel-child.pid"
LOG_FILE="$LOG_DIR/tunnel.log"

mkdir -p "$PID_DIR" "$LOG_DIR"

child=""
stopping=0
backoff=2

cleanup() {
  stopping=1
  if [[ -n "$child" ]] && kill -0 "$child" 2>/dev/null; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  rm -f "$CHILD_PID_FILE" "$URL_FILE"
  exit 0
}
trap cleanup TERM INT

while (( ! stopping )); do
  : > "$LOG_FILE"
  printf '[%s] starting Cloudflare Quick Tunnel...\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"

  cloudflared tunnel --no-autoupdate --url http://127.0.0.1:3000 >> "$LOG_FILE" 2>&1 &
  child=$!
  printf '%s\n' "$child" > "$CHILD_PID_FILE"

  url=""
  while kill -0 "$child" 2>/dev/null; do
    candidate="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null | tail -n 1 || true)"
    if [[ -n "$candidate" && "$candidate" != "$url" ]]; then
      url="$candidate"
      printf '%s\n' "$url" > "$URL_FILE"
      printf '[%s] published %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$url" >> "$LOG_FILE"
      backoff=2
    fi
    sleep 2
  done

  wait "$child" 2>/dev/null || true
  child=""
  rm -f "$CHILD_PID_FILE" "$URL_FILE"
  (( stopping )) && break

  printf '[%s] cloudflared exited; restarting in %ss\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$backoff" >> "$LOG_FILE"
  sleep "$backoff"
  if (( backoff < 30 )); then
    backoff=$((backoff * 2))
  fi
done
