---
name: soak-report-format
description: "Owner's standing format for soak report artifacts (2026-10-05): one URL updated every 30 min, per-stop errors at arrival / +5 ms / +10 ms / leaving, 15-min means and box plots with mean/min/max joined, summary table"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-05T11:19:27.082Z
---

Soak reports are published as one artifact per test campaign and republished
every ~30 min (when the log monitor re-arms). Generator:
scratchpad soak_report/gen.py + template.html (env SOAK_LOG, SOAK_CSV,
SOAK_PLAN, SOAK_OUT, SOAK_SUB); per-stop CSV from `circle_soak --dwell-log`
(columns at_ms, dwell_ms, settle_ms, err_stop_um, err_leave_um, err_5ms_um,
err_10ms_um; PLC SYS DWELL ev has the same 7 fields since 2026-10-05) or,
without it, `dwell_sample.py` pulls the latest 240 stops from the PLC.

Content the owner asked for ("之後就照這樣", 2026-10-05):
- per minute: late %, torque peaks, EtherCAT lost frames, events (segments,
  dropouts);
- stop errors = distance of the actual TCP to the commanded target at the
  cycle the commanded position arrives ("停下"), +5 ms, +10 ms, and when
  leaving; histograms;
- mean per 15-min window as a trend (停下 / 5 ms / 10 ms); the first data
  point is the first minute alone, then 1-15, 15-30, ... (owner 2026-10-05;
  gen.py window());
- box-and-whisker per 15-min window (Q1–Q3, median, min/max whiskers, mean
  dot) with the means joined by solid lines and min / max by dashed lines;
- a whole-run table: n, mean, median, min, max, p95.

**Why:** the owner tracks settling drift over hours and compares drives;
he found the plain histograms unreadable for that.

**How to apply:** reuse gen.py/template.html as they are for the next soak;
do not drop sections. If a soak starts without --dwell-log, say so and use
the PLC sample. The triangle path (`circle_soak --shape tri`) loads the
three arms equally; the square does not (RMS share ~31/42/28 %), so use
tri when comparing axes. Related: [[delta-shock-settle-method]],
[[ethercat-dropout-qec-reel]].
