#!/usr/bin/env python3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os

BASE="./results"
metrics_path = os.path.join(BASE, "metrics.csv")
summary_path = "./results/summary.csv"
plots_dir = "./plots"
os.makedirs(plots_dir, exist_ok=True)

df = pd.read_csv(metrics_path)

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
    "Avg Bandwidth (kbps)": df["bandwidth_per_client_kbps"].mean(),
    "Total Packets Received": len(df)
}

print("=== Baseline Summary ===")
for k,v in stats.items():
    print(f"{k}: {v}")

# Save summary to CSV
pd.DataFrame([stats]).to_csv(summary_path, index=False)
print(f"[analysis] summary saved to {summary_path}")

# Simple plot: latency CDF and time-series
plt.figure(figsize=(6,4))
sorted_lat = np.sort(df["latency_ms"].dropna())
p = np.arange(len(sorted_lat))/len(sorted_lat)
plt.plot(sorted_lat, p)
plt.xlabel("Latency (ms)")
plt.ylabel("CDF")
plt.title("Latency CDF (baseline)")
plt.grid(True)
plt.savefig(os.path.join(plots_dir, "latency_cdf.png"))
print(f"[analysis] latency CDF saved to {plots_dir}/latency_cdf.png")
