"""
check_mat.py  –  Read and display TwoConnectedTanks_res.mat contents.
Run this after each simulation to verify the results changed.
"""

import scipy.io
import numpy as np

MAT_FILE = r"C:\Users\Rakes\Videos\fossee\model\TwoConnectedTanks_res.mat"

mat = scipy.io.loadmat(MAT_FILE)

# ── Variable names ────────────────────────────────────────────────────────────
# OpenModelica stores names as a char matrix; each row is one variable name.
raw_names = mat["name"]          # shape (20, max_name_len)
names = ["".join(row).strip() for row in raw_names]
print("Variables in result file:")
for i, n in enumerate(names):
    print(f"  [{i:2d}]  {n}")

# ── Time vector ───────────────────────────────────────────────────────────────
# data_1 holds time + parameters that don't change.
# data_2 holds time-varying signals; row 0 is the time axis.
time = mat["data_2"][0]          # first row = time
print(f"\nTime axis:  start={time[0]:.4f}   stop={time[-1]:.4f}   "
      f"points={len(time)}")

# ── Tank level signals ────────────────────────────────────────────────────────
# Print min/max of every data_2 row so we can see the actual values.
data2 = mat["data_2"]
print("\ndata_2 row values (min → max):")
for row_idx in range(data2.shape[0]):
    row = data2[row_idx]
    print(f"  row {row_idx}: min={row.min():.6f}   max={row.max():.6f}   "
          f"first={row[0]:.6f}   last={row[-1]:.6f}")