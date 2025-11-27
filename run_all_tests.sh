#!/usr/bin/env bash
# run_all_tests.sh - Standalone version
# Usage: sudo ./run_all_tests.sh <interface>
#
# Runs all 4 test scenarios sequentially:
#   baseline, loss2, loss5, delay100
# Results are saved in ./full_run/<scenario>/

set -euo pipefail

IFACE=${1:-eth0}                 # Network interface to test on
TS="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="./full_run_$TS"
mkdir -p "$RUN_DIR"

SCENARIOS=("baseline" "loss2" "loss5" "delay100")

NUM_CLIENTS=4
DURATION=60  # seconds

# Install dependencies if needed
python3 -m pip install -r requirements.txt

cleanup() {
  echo "[CLEANUP] Killing server, clients, and tcpdump..."
  kill ${SERVER_PID:-} 2>/dev/null || true
  for pid in "${CLIENT_PIDS[@]:-}"; do
      kill $pid 2>/dev/null || true
  done
  kill ${TCPDUMP_PID:-} 2>/dev/null || true

  echo "[CLEANUP] Removing netem qdisc..."
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
}

# Run cleanup on EXIT or SIGINT/SIGTERM
trap cleanup EXIT SIGINT SIGTERM

echo "[TEST] Running all ${#SCENARIOS[@]} test scenarios. Interface: $IFACE, each test duration=${DURATION}s, clients=${NUM_CLIENTS}"
echo "[TEST] Results will be saved in: $RUN_DIR"
echo "[TEST] -----------------------------------------------------"

for SCENARIO in "${SCENARIOS[@]}"; do
  echo -e "\n[TEST] --- 🚀 STARTING SCENARIO: $SCENARIO ---"

  # --- final directories for this scenario ---
  SCEN_DIR="${RUN_DIR}/${SCENARIO}"
  LOGDIR="${SCEN_DIR}/logs"
  RESULTS_RAW="${SCEN_DIR}/results_raw"
  RESULTS_DIR="${SCEN_DIR}/results"
  PLOTS_DIR="${SCEN_DIR}/plots"
  PCAP_DIR="${SCEN_DIR}/pcaps"

  # --- Clean and apply netem ---
  echo "[TEST] Cleaning any previous tc rules on $IFACE"
  tc qdisc del dev "$IFACE" root 2>/dev/null || true

  NETEM_CMD=""
  case "$SCENARIO" in
    loss2) NETEM_CMD="root netem loss 2%" ;;
    loss5) NETEM_CMD="root netem loss 5%" ;;
    delay100) NETEM_CMD="root netem delay 100ms" ;;
    baseline) NETEM_CMD="" ;;
  esac

  if [ -n "$NETEM_CMD" ]; then
    echo "[TEST] Applying: tc qdisc add dev $IFACE $NETEM_CMD"
    tc qdisc add dev "$IFACE" $NETEM_CMD
  else
    echo "[TEST] No netem (baseline)"
  fi
  sleep 2
  echo "[TEST] ✅ Netem applied."
  mkdir -p "$LOGDIR" "$RESULTS_RAW" "$RESULTS_DIR" "$PLOTS_DIR" "$PCAP_DIR"
  tc qdisc show > "${SCEN_DIR}/netem_list.txt"
  
  # --- Start tcpdump ---
  PCAP_FILE="${PCAP_DIR}/${SCENARIO}.pcap"
  tcpdump -i "$IFACE" udp port 9999 -w "$PCAP_FILE" 2> "$PCAP_DIR/tcpdump_${SCENARIO}.log" &
  TCPDUMP_PID=$!

  # Start server
  python3 server.py --duration $DURATION --log "$LOGDIR/server.log" &
  SERVER_PID=$!
  echo "[TEST] Server started (PID=$SERVER_PID)"
  sleep 2

  # === Start Clients ===
  CLIENT_PIDS=()

  # 1️⃣ Client 1 → test 0
  python3 client.py --metrics_id 1 --test "0" --duration $DURATION --log "$LOGDIR/client1_stdout.log" & 
  CLIENT_PIDS+=($!)
  echo "[TEST] Started client1 (test=0, PID=${CLIENT_PIDS[-1]})"

  sleep $((5))

  # 2️⃣–4️⃣ Clients 2-4 → test 1
  for ((i=2; i<=NUM_CLIENTS; i++)); do
    python3 client.py --metrics_id $i --test "1" --duration $DURATION --log "$LOGDIR/client${i}_stdout.log" & 
    CLIENT_PIDS+=($!)
    echo "[TEST] Started client${i} (test=1, PID=${CLIENT_PIDS[-1]})"
    sleep 0.5
  done

  # Wait for test duration
  sleep $((DURATION + 5))
  echo "[TEST] Test duration completed. Stopping clients and server..."

  # --- Remove tc rules ---
  echo "[TEST] Removing netem qdisc on $IFACE"
  tc qdisc del dev "$IFACE" root 2>/dev/null || true
  sleep 2
  echo "[TEST] ✅ Netem removed."

  # --- Analyze results ---
  echo "[TEST] Analyzing results..."
  python3 analyze_metrics.py
  sleep 2

  # --- Move results to final directories ---
  mv ./plots/* "$PLOTS_DIR/" 2>/dev/null || true
  mv ./results/* "$RESULTS_DIR/" 2>/dev/null || true
  mv ./results_raw/* "$RESULTS_RAW/" 2>/dev/null || true
  rm -rf ./plots ./results ./results_raw
  echo "[TEST] ✅ $SCENARIO Done. results in $SCEN_DIR"
done

# --- Change ownership of all run files back to the user ---
SUDO_USER=${SUDO_USER:-$(who am i | awk '{print $1}')}
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    chown -R $SUDO_USER:$SUDO_USER "$RUN_DIR"
    echo "[TEST] Changed ownership of $RUN_DIR to $SUDO_USER"
fi

echo -e "\n[TEST] 🎉 All scenarios completed. Full results in $RUN_DIR"