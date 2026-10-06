"""Fetch the latest N stops from the PLC's every-stop log (SYS DWELL, 8 per
query) into a CSV in the shape circle_soak --dwell-log writes, so gen.py can
draw the stop-error histograms for a soak that was started without the CSV.

    python dwell_sample.py OUT.csv [N=240]
"""
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tools/
import xplc  # noqa: E402

out = sys.argv[1]
want = int(sys.argv[2]) if len(sys.argv) > 2 else 240
m = xplc.Machine()
n = m.dwell(0)["n"]
start = max(0, n - want, n - 4096)
rows = []
i = start
while i < n:
    ev = m.dwell(i)["ev"].rstrip(",")
    items = [e for e in ev.split(",") if e]
    if not items:
        break
    for e in items:
        f = e.split(":")
        at, dwell, settle, e0, el = f[:5]
        e5 = f[5] if len(f) > 5 else ""
        e10 = f[6] if len(f) > 6 else ""
        rows.append((at, dwell, "" if settle == "4294967295" else settle, e0, el, e5, e10))
    i += len(items)
with open(out, "w", encoding="utf-8") as f:
    f.write("at_ms,dwell_ms,settle_ms,err_stop_um,err_leave_um,err_5ms_um,err_10ms_um\n")
    for r in rows:
        f.write(",".join(r) + "\n")
print("stops on PLC %d, sampled %d (from %d) -> %s" % (n, len(rows), start, out))
