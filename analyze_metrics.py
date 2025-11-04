#!/usr/bin/env python3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os

# -------- Paths --------
merged_path = "./results/metrics.csv"
summary_path = "./results/summary.csv"
plots_dir = "./plots"
os.makedirs("./results", exist_ok=True)
os.makedirs(plots_dir, exist_ok=True)

# -------- Load --------
df = pd.read_csv(merged_path)

# -------- Fix column types --------
cols_to_numeric = ["latency_ms", "jitter_ms", "perceived_position_error", "cpu_percent", "bandwidth_per_client_kbps"]
for c in cols_to_numeric:
    df[c] = pd.to_numeric(df[c], errors="coerce")

before = len(df)
df = df.dropna(subset=["latency_ms", "jitter_ms"])
after = len(df)
print(f"[clean] Removed {before-after} corrupted rows, remaining: {after}")

# -------- 20 updates/sec validation (per client) --------
df["server_timestamp_ms"] = pd.to_numeric(df["server_timestamp_ms"], errors="coerce")
df["timestamp_sec"] = (df["server_timestamp_ms"] // 1000).astype(int)

# Group by client and time second
updates_per_client_sec = df.groupby(["client_id", "timestamp_sec"]).size().reset_index(name="count")

# Now compute average/min/max across all clients
avg_updates_per_client = updates_per_client_sec["count"].mean()
min_updates_per_client = updates_per_client_sec["count"].min()
max_updates_per_client = updates_per_client_sec["count"].max()

print(f"[updates] Avg per client: {avg_updates_per_client:.2f}, min: {min_updates_per_client}, max: {max_updates_per_client}")


# -------- Stats --------
def pct95(x): return np.percentile(x.dropna(), 95)

stats = {
    "Mean Latency (ms)": df["latency_ms"].mean(),
    "Median Latency (ms)": df["latency_ms"].median(),
    "95th Latency (ms)": pct95(df["latency_ms"]),
    "Mean Jitter (ms)": df["jitter_ms"].mean(),
    "Median Jitter (ms)": df["jitter_ms"].median(),
    "95th Jitter (ms)": pct95(df["jitter_ms"]),
    "Mean Error": df["perceived_position_error"].mean(),
    "95th Error": pct95(df["perceived_position_error"]),
    "Avg CPU% (clients)": df["cpu_percent"].mean(),
    "Max CPU% (clients)": df["cpu_percent"].max(),
    "Avg Bandwidth (kbps)": df["bandwidth_per_client_kbps"].mean(),
    "Total Packets Received": len(df),
    "Avg Updates/sec": avg_updates_per_client,
    "Min Updates/sec": min_updates_per_client,
    "Max Updates/sec": max_updates_per_client
}

print("=== Baseline Metrics Summary ===")
for k, v in stats.items():
    print(f"{k}: {v}")

pd.DataFrame([stats]).to_csv(summary_path, index=False)
print(f"[analysis] Summary saved to {summary_path}")

# -------- Plots --------

# Latency CDF
plt.figure(figsize=(6,4))
sorted_lat = np.sort(df["latency_ms"])
p = np.arange(len(sorted_lat)) / len(sorted_lat)
plt.plot(sorted_lat, p)
plt.xlabel("Latency (ms)")
plt.ylabel("CDF")
plt.title("Latency CDF (Baseline)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "latency_cdf.png"))


# Latency over time
plt.figure(figsize=(6,4))
plt.plot(df["server_timestamp_ms"], df["latency_ms"])
plt.title("Latency Over Time")
plt.xlabel("Time (ms)")
plt.ylabel("Latency (ms)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "latency_timeseries.png"))

# Jitter over time
plt.figure(figsize=(6,4))
plt.plot(df["server_timestamp_ms"], df["jitter_ms"])
plt.title("Jitter Over Time")
plt.xlabel("Time (ms)")
plt.ylabel("Jitter (ms)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "jitter_timeseries.png"))

# CPU usage over time
plt.figure(figsize=(6,4))
plt.plot(df["cpu_percent"])
plt.title("Client CPU Usage Over Time")
plt.xlabel("Samples")
plt.ylabel("CPU (%)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "cpu_timeseries.png"))

# Bandwidth over time
plt.figure(figsize=(6,4))
plt.plot(df["bandwidth_per_client_kbps"])
plt.title("Bandwidth Usage Over Time")
plt.xlabel("Samples")
plt.ylabel("kbps")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "bandwidth_timeseries.png"))

# Latency Histogram
plt.figure(figsize=(6,4))
plt.hist(df["latency_ms"], bins=40)
plt.title("Latency Histogram")
plt.xlabel("Latency (ms)")
plt.ylabel("Count")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "latency_histogram.png"))

# Per-Client Update Rate
plt.figure(figsize=(6,4))
for client_id, group in updates_per_client_sec.groupby("client_id"):
    plt.plot(group["timestamp_sec"], group["count"], label=f"Client {client_id}")
plt.axhline(20, linestyle="--", color="gray", label="Target = 20 updates/sec")
plt.xlabel("Second")
plt.ylabel("Updates/sec")
plt.title("Per-Client Snapshot Rate")
plt.legend()
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "per_client_snapshots.png"))

print("[analysis] All plots saved in ./plots/")
print("✅ Analysis complete.")

