#!/bin/bash
# run_baseline.sh - Phase 1 baseline test
set -e
RESULTS_DIR="./results/baseline"
mkdir -p "$RESULTS_DIR"
LOGDIR="./logs"
mkdir -p "$LOGDIR"

NUM_CLIENTS=2
DURATION=60  # seconds
SERVER_LOG="$RESULTS_DIR/server.log"

echo "[TEST] Baseline test: results -> $RESULTS_DIR, duration ${DURATION}s, clients ${NUM_CLIENTS}"

# Start server (broadcasting to local client ports)
CLIENT_ADDRS=""
for ((i=1;i<=NUM_CLIENTS;i++)); do
  CLIENT_ADDRS="$CLIENT_ADDRS 127.0.0.1:$((5000+i))"
done

# Start server
python3 -m pip install -r requirements.txt
python3 server.py --clients $CLIENT_ADDRS --rate 20 --duration $DURATION --log "$SERVER_LOG" > "$LOGDIR/server_stdout.log" 2>&1 &
SERVER_PID=$!
echo "[TEST] server pid=$SERVER_PID"

sleep 1

# Start clients
CLIENT_PIDS=()
for ((i=1;i<=NUM_CLIENTS;i++)); do
  OUT="$RESULTS_DIR/client${i}.csv"
  python3 client.py --player $i --headless --duration $DURATION --out "$OUT" > "$LOGDIR/client${i}_stdout.log" 2>&1 &
  CLIENT_PIDS+=($!)
  echo "[TEST] started client $i pid=${CLIENT_PIDS[-1]}"
  sleep 0.3
done

# Wait for duration + small buffer
sleep $((DURATION + 2))

# Ensure processes are stopped
kill $SERVER_PID 2>/dev/null || true
for pid in "${CLIENT_PIDS[@]}"; do
  kill $pid 2>/dev/null || true
done

echo "[TEST] Baseline finished. Files in $RESULTS_DIR. Server log: $SERVER_LOG"
