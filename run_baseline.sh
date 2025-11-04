#!/bin/bash
# run_baseline.sh - Run server + 4 clients (1 test=0, 3 test=1)
set -e

LOGDIR="./logs"
RESULTS_DIR="./results"
mkdir -p "$LOGDIR" "$RESULTS_DIR"

NUM_CLIENTS=4
DURATION=60  # seconds
CLIENTDURATION=40  # seconds

echo "[TEST] Running baseline: duration=${DURATION}s, clients=${NUM_CLIENTS}"

# Install dependencies if needed
python3 -m pip install -r requirements.txt

# Start server
python3 server.py --duration $DURATION 2>&1 &
SERVER_PID=$!
echo "[TEST] Server started (PID=$SERVER_PID)"
sleep 2

# === Start Clients ===
CLIENT_PIDS=()

# 1️⃣ Client 1 → test 0
python3 client.py --test "0" --duration $CLIENTDURATION > "$LOGDIR/client1_stdout.log" 2>&1 &
CLIENT_PIDS+=($!)
echo "[TEST] Started client1 (test=0, PID=${CLIENT_PIDS[-1]})"

# 2️⃣–4️⃣ Clients 2-4 → test 1
for ((i=2; i<=NUM_CLIENTS; i++)); do
  python3 client.py --test "1" --duration $CLIENTDURATION > "$LOGDIR/client${i}_stdout.log" 2>&1 &
  CLIENT_PIDS+=($!)
  echo "[TEST] Started client${i} (test=1, PID=${CLIENT_PIDS[-1]})"
  sleep 0.5
done

# Wait for test duration
sleep $((CLIENTDURATION + 5))

# Kill all processes
echo "[TEST] Stopping all..."
kill $SERVER_PID 2>/dev/null || true
for pid in "${CLIENT_PIDS[@]}"; do
  kill $pid 2>/dev/null || true
done

echo "[TEST] Done. Logs in $LOGDIR, results in $RESULTS_DIR"
