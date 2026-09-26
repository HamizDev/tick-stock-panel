#!/usr/bin/env bash
set -euo pipefail

PORT="${TSP_HOST_PORT:-3018}"
MIN_MEM_KB=1572864   # conservative guardrail: 1.5 GiB MemAvailable
MIN_DISK_KB=10485760 # conservative guardrail: 10 GiB free

echo "[1/7] Orvia/Fund failed units (read-only)"
FAILED="$(systemctl --failed --no-legend 2>/dev/null | grep -Ei 'orvia|fund' || true)"
if [[ -n "$FAILED" ]]; then
  echo "STOP: existing Orvia/Fund unit is failed:"
  echo "$FAILED"
  exit 20
fi

echo "[2/7] Protected main services"
for svc in orvia-funds.service fund-advisor.service; do
  if ! systemctl is-active --quiet "$svc"; then
    echo "STOP: protected service not active: $svc"
    exit 21
  fi
  echo "OK: $svc active"
done

echo "[3/7] Protected ports 8765/8766"
for p in 8765 8766; do
  if ! ss -lnt | awk '{print $4}' | grep -Eq "[:.]$p$"; then
    echo "STOP: protected Orvia port $p is not listening"
    exit 22
  fi
  echo "OK: $p listening"
done

echo "[4/7] TSP candidate port"
if ss -lnt | awk '{print $4}' | grep -Eq "[:.]$PORT$"; then
  echo "STOP: TSP port $PORT is already in use. Pick a different TSP_HOST_PORT; do not move Orvia."
  exit 23
fi
echo "OK: $PORT free"

echo "[5/7] Docker availability"
if ! command -v docker >/dev/null 2>&1; then
  echo "STOP: docker not installed"
  exit 24
fi
if ! docker info >/dev/null 2>&1; then
  echo "STOP: docker daemon unavailable to current user"
  exit 25
fi
echo "OK: docker available"

echo "[6/7] Memory guardrail"
MEM_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
if (( MEM_KB < MIN_MEM_KB )); then
  echo "STOP: MemAvailable is below conservative 1.5 GiB guardrail."
  echo "Use another VPS or free capacity in the Orvia project first; do not tune Orvia from this project."
  exit 26
fi
echo "OK: MemAvailable=$((MEM_KB/1024)) MiB"

echo "[7/7] Disk guardrail"
DISK_KB="$(df -Pk /opt | awk 'NR==2 {print $4}')"
if (( DISK_KB < MIN_DISK_KB )); then
  echo "STOP: less than 10 GiB free under /opt"
  exit 27
fi
echo "OK: free=$((DISK_KB/1024)) MiB"

echo "PASS: read-only preflight passed. No Orvia resource was modified."
