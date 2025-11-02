#!/usr/bin/env python3
import pandas as pd
import glob, os

BASE="./results/baseline"
out = os.path.join(BASE, "merged.csv")
files = sorted(glob.glob(os.path.join(BASE, "client*.csv")))

if not files:
    print("[merge] no client CSVs found in", BASE)
    raise SystemExit(1)

dfs = [pd.read_csv(f) for f in files]
merged = pd.concat(dfs, ignore_index=True)
merged.to_csv(out, index=False)
print(f"[merge] merged {len(files)} files -> {out}")
