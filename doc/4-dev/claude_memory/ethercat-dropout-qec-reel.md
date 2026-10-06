---
name: ethercat-dropout-qec-reel
description: EtherCAT dropouts (QEC + reel back in INIT, ~2.5 s of lost frames) -- QEC wiring fixed 10-05 (9.5 h clean) but RECURRED 2026-10-06 at standstill after a power cycle; not caused by logins or UI reconnects
metadata:
  type: project
---

Since the afternoon of 2026-10-03 the bus loses all frames for ~2.5 s (master lost-frame counter +2200..2600) now and then; servos that are enabled lose DC sync -> errorstop; the QEC (station 1002) and the reel servo (1003) come back in INIT / SAFE-OP every time, the ASDA drives stay OP when disabled. Happens at standstill too and got more frequent (15 min -> 3 min -> 30 s). Software ruled out (old PLC program drops too; virtual run clean). Handover: xPLCCore/doc_review/ethercat_dropout_2026-10-03.md.

**Why:** suspected power of the QEC + reel (shared supply / wiring) or one of them rebooting; the CODESYS libraries cannot read slave ESC error counters, and the owner has no EtherCAT diagnostic tool.

**How to apply:** don't run long real-servo soaks until the owner has checked the QEC/reel power. Watch with tools/bus_watch.py (standstill, re-downloads after a dropout). After a dropout: delta virtual + off, then tools/safe_install.py. Related: [[asda-stale-target-investigation]], [[machine-motion-safety]].

**Update 2026-10-05:** the owner found the QEC wiring loose and fixed it
(09:30). Before: slow Z soak dropped at 324 min with `watchdog for opmode
expired. Address: 1002` and Frames Lost 0 (the QEC left the bus). After the
fix: round 30 %, square dip 70 % (which dropped at 14.9 min twice on 10-03)
and a 2 h triangle 70 % soak, 3 h 31 min in all, 0 dropouts. Treat the QEC
wiring as the likely root cause; confirm with a longer soak before closing.

**Closed 2026-10-05 19:18:** 6 h triangle 70 % soak clean (0 dropouts, lost 0,
98,714 stops); 9.5 h of motion clean after the fix. Long real-servo soaks are
fine again. If a dropout comes back, check the QEC connector first.

**Reopened 2026-10-06:** one dropout at standstill 22 min after the owner's
power cycle (same signature). Ruled out on the healthy bus: CODESYS login
reads and UI reconnects (0 frames lost). Recovery without a download: GA_EV 8
from UnInited (restarts a faulted bus). Evidence folder
codesys_scripts/jobs/incidents/20261006-082809_after_power_cycle. Watch with
`tools/bus_watch.py --owner-ok` (now stops and keeps evidence; no auto
re-download).
