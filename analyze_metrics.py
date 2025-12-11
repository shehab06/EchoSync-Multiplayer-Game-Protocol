import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import json

results_dir = "./results_raw"
merged_path = "./results/metrics.csv"
summary_path = "./results/summary.csv"
plots_dir = "./plots"

os.makedirs("./results", exist_ok=True)
os.makedirs(plots_dir, exist_ok=True)

print("[merge] Loading server + client files...")

server_frames = []
client_frames = []

for f in os.listdir(results_dir):
    if not f.endswith(".csv"):
        continue

    df = pd.read_csv(os.path.join(results_dir, f))

    if "server" in f:
        server_frames.append(df)
    else:
        client_frames.append(df)

server_df = pd.concat(server_frames, ignore_index=True)
client_df = pd.concat(client_frames, ignore_index=True)

print(f"[merge] Server rows: {len(server_df)}, Client rows: {len(client_df)}")

# --- Normalize missing columns ---
for c in ["client_id","seq_num","snapshot_id","server_timestamp_ms","grid","cpu_percent"]:
    if c not in server_df: server_df[c] = np.nan
for c in ["client_id","seq_num","snapshot_id","server_timestamp_ms","grid",
          "latency_ms","jitter_ms","bandwidth_per_client_kbps"]:
    if c not in client_df: client_df[c] = np.nan

# --- Decode grid JSON ---
def decode_grid(g):
    if pd.isna(g):
        return None
    try:
        return list(map(int, json.loads(g)))
    except:
        try:
            return list(map(int, g.strip("[]").split(",")))
        except:
            return None

server_df["grid"] = server_df["grid"].apply(decode_grid)
client_df["grid"] = client_df["grid"].apply(decode_grid)

# ----------------------------------------------------------
#  JOIN: server + client on (client_id, seq_num, snapshot_id)
# ----------------------------------------------------------
merged = pd.merge(
    client_df,
    server_df,
    on=["client_id", "seq_num", "snapshot_id"],
    suffixes=("_client", "_server"),
    how="left"
)

print("[merge] Joined rows:", len(merged))

# ----------------------------------------------------------
#  COMPUTE PERCEIVED POSITION ERROR
# ----------------------------------------------------------
def compute_error(cg, sg):
    if cg is None or sg is None:
        return np.nan
    if len(cg) != len(sg):
        return np.nan
    return sum(1 for a,b in zip(cg,sg) if a != b)

merged["perceived_position_error"] = merged.apply(
    lambda r: compute_error(r["grid_client"], r["grid_server"]),
    axis=1
)

# Drop grids to reduce file size
merged = merged.drop(columns=["grid_client", "grid_server"])

# Ensure server timestamp is consistent
if "server_timestamp_ms_client" in merged.columns and "server_timestamp_ms_server" in merged.columns:
    mismatches = (merged["server_timestamp_ms_client"] != merged["server_timestamp_ms_server"]).sum()
    if mismatches > 0:
        print(f"[warning] {mismatches} rows have mismatched server timestamps!")
    # Keep only server column
    merged = merged.drop(columns=["server_timestamp_ms_client"])
    merged = merged.rename(columns={"server_timestamp_ms_server": "server_timestamp_ms"})

merged.to_csv(merged_path, index=False)
print(f"[merge] Final merged file saved at: {merged_path}")

# -----------------------------
#  ANALYSIS
# -----------------------------
df = merged.copy()

# Convert numeric columns
cols_to_numeric = ["latency_ms", "jitter_ms", "perceived_position_error", "cpu_percent", "bandwidth_per_client_kbps"]
for c in cols_to_numeric:
    df[c] = pd.to_numeric(df[c], errors="coerce")

before = len(df)
df = df.dropna(subset=["latency_ms", "jitter_ms"])
after = len(df)
print(f"[clean] Removed {before-after} corrupted rows, remaining: {after}")

# 20 updates/sec validation
df["server_timestamp_ms"] = pd.to_numeric(df["server_timestamp_ms"], errors="coerce")
df["timestamp_sec"] = (df["server_timestamp_ms"] // 1000).astype(int)

# Identify clients by having bandwidth column
updates_per_client_sec = df[df["bandwidth_per_client_kbps"].notna()].groupby(["client_id", "timestamp_sec"]).size().reset_index(name="count")
avg_updates_per_client = updates_per_client_sec["count"].mean()
min_updates_per_client = updates_per_client_sec["count"].min()
max_updates_per_client = updates_per_client_sec["count"].max()

last_bw_per_client = df[df["bandwidth_per_client_kbps"].notna()].groupby("client_id")["bandwidth_per_client_kbps"].last()

# Compute average across clients
avg_bw_kbps = last_bw_per_client.mean()

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
    "Avg CPU% (server only)": df[df["cpu_percent"].notna()]["cpu_percent"].mean(),
    "Max CPU% (server only)": df[df["cpu_percent"].notna()]["cpu_percent"].max(),
    "Avg Bandwidth (kbps per client)": avg_bw_kbps,
    "Total Packets Logged": len(df),
    "Avg Updates/sec": avg_updates_per_client,
    "Min Updates/sec": min_updates_per_client,
    "Max Updates/sec": max_updates_per_client
}

print("\n[ANALYSIS] === Final Metrics Summary ===")
for k, v in stats.items():
    print(f"[ANALYSIS] {k}: {v}")

pd.DataFrame([stats]).to_csv(summary_path, index=False)
print(f"[ANALYSIS] Summary saved to {summary_path}")

# -----------------------------
# PLOTS
# -----------------------------
# Latency CDF
plt.figure(figsize=(6,4))
sorted_lat = np.sort(df["latency_ms"])
p = np.arange(len(sorted_lat)) / len(sorted_lat)
plt.plot(sorted_lat, p)
plt.xlabel("Latency (ms)")
plt.ylabel("CDF")
plt.title("Latency CDF")
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

# Server CPU usage
plt.figure(figsize=(6,4))
plt.plot(df[df["cpu_percent"].notna()]["cpu_percent"])
plt.title("Server CPU Usage")
plt.xlabel("Samples")
plt.ylabel("CPU (%)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "cpu_timeseries.png"))

# Per Client bandwidth
plt.figure(figsize=(6,4))
for client_id, group in df[df["bandwidth_per_client_kbps"].notna()].groupby("client_id"):
    plt.plot(group["timestamp_sec"], group["bandwidth_per_client_kbps"], label=f"Client {client_id}")

plt.title("Per-Client Bandwidth Over Time")
plt.xlabel("Time (sec)")
plt.ylabel("Average Bandwidth (kbps)")
plt.legend()
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "bandwidth_timeseries.png"))


# Latency histogram
plt.figure(figsize=(6,4))
plt.hist(df["latency_ms"], bins=40)
plt.title("Latency Histogram")
plt.xlabel("Latency (ms)")
plt.ylabel("Count")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "latency_histogram.png"))

# Per-client update frequency
plt.figure(figsize=(6,4))
for client_id, group in updates_per_client_sec.groupby("client_id"):
    plt.plot(group["timestamp_sec"], group["count"], label=f"Client {client_id}")
plt.axhline(20, linestyle="--", color="gray", label="20 updates/sec target")
plt.xlabel("Second")
plt.ylabel("Updates/sec")
plt.title("Per-Client Update Frequency")
plt.legend()
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "per_client_snapshots.png"))

print("[ANALYSIS] All plots saved.")
print("[ANALYSIS] ✅ Analysis complete.")
