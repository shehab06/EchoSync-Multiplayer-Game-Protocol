#!/usr/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt
import os
import sys

# -----------------------------
#  Command-line argument
# -----------------------------
if len(sys.argv) != 2:
    print("Usage: python plot_summary_comparison.py <full_path_to_run_dir>")
    print("Example: python plot_summary_comparison.py /home/sh3hb/project2_testing/full_run_20251212_141337")
    sys.exit(1)

RUN_DIR = sys.argv[1]

plots_dir = os.path.join(RUN_DIR, "plots")
os.makedirs(plots_dir, exist_ok=True)

scenarios = ["baseline", "loss2", "loss5", "delay100"]

# -----------------------------
#  Load summaries
# -----------------------------
summary_dfs = []
for scenario in scenarios:
    path = os.path.join(RUN_DIR, scenario, "results", "summary.csv")
    if not os.path.exists(path):
        print(f"[WARNING] Summary file not found: {path}")
        continue
    df = pd.read_csv(path)
    df["scenario"] = scenario
    summary_dfs.append(df)

if not summary_dfs:
    print("[ERROR] No summary files loaded. Exiting.")
    sys.exit(1)

summary_all = pd.concat(summary_dfs, ignore_index=True)

# -----------------------------
#  1. Mean Latency
# -----------------------------
plt.figure(figsize=(6,4))
plt.bar(summary_all["scenario"], summary_all["Mean Latency (ms)"])
plt.title("Mean Latency per Scenario")
plt.xlabel("Scenario")
plt.ylabel("Latency (ms)")
plt.grid(True, axis='y')
plt.savefig(os.path.join(plots_dir, "compare_mean_latency.png"))

# -----------------------------
#  2. Mean Jitter
# -----------------------------
plt.figure(figsize=(6,4))
plt.bar(summary_all["scenario"], summary_all["Mean Jitter (ms)"])
plt.title("Mean Jitter per Scenario")
plt.xlabel("Scenario")
plt.ylabel("Jitter (ms)")
plt.grid(True, axis='y')
plt.savefig(os.path.join(plots_dir, "compare_mean_jitter.png"))

# -----------------------------
#  3. Mean Error
# -----------------------------
plt.figure(figsize=(6,4))
plt.bar(summary_all["scenario"], summary_all["Mean Error"])
plt.title("Mean Position Error per Scenario")
plt.xlabel("Scenario")
plt.ylabel("Error")
plt.grid(True, axis='y')
plt.savefig(os.path.join(plots_dir, "compare_mean_error.png"))

# -----------------------------
#  4. Avg CPU%
# -----------------------------
plt.figure(figsize=(6,4))
plt.bar(summary_all["scenario"], summary_all["Avg CPU% (server only)"])
plt.title("Avg CPU Usage per Scenario")
plt.xlabel("Scenario")
plt.ylabel("CPU (%)")
plt.grid(True, axis='y')
plt.savefig(os.path.join(plots_dir, "compare_avg_cpu.png"))

# -----------------------------
#  5. Bandwidth vs Updates
# -----------------------------
plt.figure(figsize=(6,4))
plt.scatter(summary_all["Avg Updates/sec"], summary_all["Avg Bandwidth (kbps per client)"])

for idx, row in summary_all.iterrows():
    plt.text(row["Avg Updates/sec"], row["Avg Bandwidth (kbps per client)"], row["scenario"],
             fontsize=9, ha='right', va='bottom')

plt.title("Bandwidth vs Updates per Scenario")
plt.xlabel("Avg Updates/sec")
plt.ylabel("Avg Bandwidth (kbps per client)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "compare_bandwidth_vs_updates.png"))

# -----------------------------
#  6. Bandwidth vs Loss
# -----------------------------
plt.figure(figsize=(6,4))
plt.scatter(summary_all["Avg Loss (%)"], summary_all["Avg Bandwidth (kbps per client)"])

for idx, row in summary_all.iterrows():
    plt.text(row["Avg Loss (%)"], row["Avg Bandwidth (kbps per client)"], row["scenario"],
             fontsize=9, ha='right', va='bottom')

plt.title("Bandwidth vs Loss per Scenario")
plt.xlabel("Avg Loss (%)")
plt.ylabel("Avg Bandwidth (kbps per client)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "compare_bandwidth_vs_loss.png"))

print("[DONE] All comparison plots saved in", plots_dir)
