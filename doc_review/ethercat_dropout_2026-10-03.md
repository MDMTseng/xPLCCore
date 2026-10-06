# EtherCAT dropouts with the QEC and reel resetting (2026-10-03)

Status: **reopened 2026-10-06**: after the QEC wiring fix (9.5 h of motion
clean, section 2) it happened again at standstill, 22 min after a power
cycle (section 2, last tables), and again 2026-10-06 21:21 PC, 1.5 h after
an app start with no first contact nearby; GA_EV 8 recovered it. Original report: since the afternoon of 2026-10-03 the bus
loses all frames for ~2.5 s now and then: the servos lose DC sync and go
to errorstop if they are enabled, and the QEC (station 1002) and the reel
servo (1003) come back in INIT / SAFE-OP, as if they had rebooted. It
happens with the delta moving or standing still. Software is ruled out;
the next step is on the machine (power of the QEC and the reel).

## 1. Symptom

PLC log at each dropout (PLC clock ~4 min ahead of the PC):
- `Fieldbus lost synchronicity` on every SoftMotion drive, then
  `Error 11005: An axis of the axis group is in state error`;
- `Frames Lost/Tx err/Rx err: 43/0/44` (or 16/0/17) and `DC phaseDist min
  -43006 us` (or -10996, -15993): no frame came back for 11-43 ms;
- then `more than 100 packets lost, perhaps communication lost ! check the
  cables !`; the master's lost-frame counter (SYS EC_STATS `lost`) ends
  ~2200-2600 higher, i.e. ~2.5 s without frames;
- `ETC device is no longer in mode operational`.

Task jitter stays normal (+-35..44 us): the PLC keeps cycling.

Slave states afterwards (`IoConfig_Globals.<slave>.wState`):

| When | EC0808DN | QEC | reel | EAxis0 | EAxis1 | EAxis2 | EasyCAT |
|---|---|---|---|---|---|---|---|
| 13:06 (dip 70 %, moving) | OP | 0 | 0 | SAFE-OP | 0 | 0 | OP |
| 17:1x (standstill) | ? | SAFE-OP | INIT | OP | OP | OP | OP |
| 17:47 (standstill) | OP | 0 | 0 | OP | OP | OP | OP |

The QEC and the reel drop every time; the ASDA drives drop only when they
were enabled (DC sync loss). ~2.5 s without frames is the time a slave
needs to reboot and bring its link back, not a cable glitch (ms).

The ASDA alarm history (P4.000) shows only AL180 (EtherCAT heartbeat,
0x8130) on all three: a consequence. QEC 0x603F = 0. The reel's error
object could not be read (not in OP).

## 2. Runs

All 2026-10-03, after the slave reorder of 2026-10-02 (EC0808DN first on
the wire, tree = wire, no slave optional; doc_review/asda_stale_target
7l).

| Start | Run (`tools/circle_soak.py`) | PLC program | Result |
|---|---|---|---|
| 07:47 | round 30 %, 60 min | before DWELL | clean |
| 11:31 | square dip 70 %, 60 min | before DWELL | clean |
| 12:35 | dip 70 % | before DWELL | stopped by hand at 9 min |
| 12:51 | dip 70 % + dwell CSV | DWELL | **dropout at 14.9 min** |
| 13:32 | dip 70 % + dwell CSV | DWELL | **dropout at 14.9 min** |
| 13:52 | dip 70 %, `--virtual`, 20 min | DWELL | clean |
| 14:18 | dip 30 % + dwell CSV | DWELL | **dropout at 30.4 min** |
| 15:01 | dip 30 % | **before DWELL** | **dropout at 2.9 min** |
| 15:26 | Z 0 <-> -20 at 10 %, 60 min | DWELL | clean |
| 16:32 | Z 0 <-> -20 at 10 % (6 h planned) | DWELL | **dropout at 4 min** |
| 17:1x | standstill, delta virtual and off | DWELL | **dropout** |
| 17:47 | standstill (`tools/bus_watch.py`) | DWELL | **dropout at 0.5 min** |
| 17:47-21:59 | standstill watch, 4 h 12 min | DWELL | 12 dropouts, all between 17:47 and 18:51 (every 1.5-8 min), then none for 3 h; QEC and reel only every time |
| 22:06 (10-03) | round 10 %, 3 h (`soak_segments.py --hours 3 --recover 10`) | DWELL | clean for 170 min, **dropout at 170.7 min** (00:56) |
| 03:47 (10-05) | Z 0 <-> -20 at 10 %, 6 h (`soak_segments.py --hours 6 --recover 10`) | fe5028a (diag in Comm task) | **clean for 323.8 min**, dropout at 09:10 in the 6th segment; stopped by hand at 09:13, the owner found the QEC wiring loose |

After the QEC wiring fix (2026-10-05 09:30, re-download):

| Start | Run | Result |
|---|---|---|
| 09:31 | round 30 %, 20 min (stopped by hand to switch shapes) | clean |
| 09:52 | square dip 70 %, 59 min | clean (10-03 dropped twice at 14.9 min in this condition) |
| 10:53 | square dip 70 %, 12 min (stopped by hand) | clean |
| 11:06 | triangle 70 % (`--shape tri`), 2 h, with dwell CSV | **clean, 0 dropouts**, 33,166 stops |
| 13:17 | triangle 70 %, 6 h (`soak_segments.py --hours 6 --recover 10`, 6 segments) | **clean, 0 dropouts**, 98,714 stops; `lost` / rx / tx errors 0 at the end |

9 h 31 min of motion after the fix, `lost` 0 throughout (before it, the
longest clean run was 323.8 min and most dropped within 0.5-30 min): the
QEC wiring was the cause of the dropouts. Status: **solved**, unless one
comes back.

2026-10-06 (after a PLC power cycle at ~08:05): all slaves OP at 08:10
(PLC clock, ~4 min 19 s ahead of the PC); at 08:32:06 PLC (08:27:47 PC),
FSM UnInited, drives off, nothing moving: `Fieldbus lost synchronicity`,
`more than 100 packets lost`, lost frames 2532 (~2.5 s), QEC and reel back in
state 0, master xError, the ASDA drives AL 0x8130 (heartbeat). Same signature
as 10-03. It coincided with my first connection after the restart, so it was
checked: 5 CODESYS login reads and 3 UI reconnects on the healthy bus lost
0 frames -- not caused by either. Recovered without a download: GA_EV 8
(UnInited entry restarts a faulted bus), all 7 slaves OP. Evidence:
`codesys_scripts/jobs/incidents/20261006-082809_after_power_cycle/`. So the
QEC wiring was at least not the only cause; the QEC / reel supply and their
cables are suspects again (section 4).

Timeline of 2026-10-06 (PC time; the PLC clock is 4 min 40 s +-3 s ahead,
from the 08:34 download logged at 08:38:50 PLC):

| PC time | PLC log / event | What I did |
|---|---|---|
| ~08:05:40 | power cycle, startup, all OP (08:10:23 PLC); only the usual startup SDO warnings | -- (UI on the sim, no client on the PLC) |
| 08:05-08:27 | nothing in the PLC log for 21.5 min | -- |
| 08:27:07 | | real CODESYS daemon started (opens the project, no PLC contact) |
| ~08:27:20-30 | | UI link moved from the sim to 192.168.1.70 (first client since the boot) |
| **~08:27:26** | 08:32:06 PLC: QEC drives (EAXIS_A, GenericDSP402) and reel "lost synchronicity", 40 ms then 101 ms without frames, "more than 100 packets lost", slaves leave OP; 3 s later AL 0x8130 from the 3 ASDA | (seconds after the UI connected) |
| ~08:27:35 | | EC_STATS: lost 2532 already; then a CODESYS login read |
| ~08:27:50-08:28:07 | | slave states read (QEC 0, reel 0, master xError); lost still 2532 |
| **~08:28:05-08:28:08** | 08:32:45 PLC: a second "more than 100 packets lost" (lost 2532 -> 4958, ~2.4 s) | read_plc_log job ran 08:28:09-08:28:10 (evidence capture) |
| ~08:28:53 | 08:33:33 PLC: all slaves OP again | GA_EV 8 (bus restart through UnInited); lost stayed 4958 |
| 08:29-08:31 | no loss | 60 s idle, 5 login reads, 3 UI reconnects: 0 frames lost |

Correction: lost 4958 was a second burst before the restart, not the
restart itself. Both bursts fell within seconds of my first contacts with
the freshly booted PLC (the UI's first connection; a PLC log read job).
The controlled test afterwards (logins, reconnects) did not reproduce it,
but the log-read job was not part of that test, and nothing has been
proven either way; the earlier dropouts (10-03) also happened with nobody
connecting. To test next: the PLC log read job repeated on a healthy bus,
and a first UI connection after a PLC restart.

**2026-10-06 evening (new PC), a third one, not near any first contact.**
The owner downloaded from the IDE (19:22:44 PC; bootinfo written) and the
app was started 19:46:57 (`app_start.py`, Keep login; 19:51:40 PLC all
OP). ESP32 not reset: SYNC0-driven (`sync` 1, 999-1001 us). PLC clock
4 min 43 s ahead of the PC (app start <-> "Startup finished").

| PC time | PLC log / event | What I did |
|---|---|---|
| 19:47-21:20 | clean, lost 0 | UI connected ~20:45 (first client after the start), EC_STATS reads, 9 daemon login reads ~19:50, ESP32 serial read (dtr/rts off) 20:58: 0 lost |
| 21:20:13-18 | | `bus_watch.py` start: UI reconnect, daemon login, 9 symbol reads |
| 21:20-21:21:13 | | EC_STATS every 10 s via the UI; no daemon contact |
| **~21:21:19** | 21:26:02 PLC: EAXIS_A, QEC (GenericDSP402), reel "lost synchronicity" (23 lost), "more than 100 packets lost", slaves leave OP; 21:26:04.9 AL 0x8130 from 1004/1005/1006 | (none: next daemon read was due after 21:21:18 but not started) |
| 21:21:23 | | EC_STATS: lost 0 -> 2538, rx 0 -> 2539; QEC 0, reel 0, xError TRUE, others OP |
| 21:22:50 | | lost 2538, rx 2539; ESP32 still OP / sync 1 but interrupt interval 805-6057 us, stale / skip climbing (master in error, irregular frames) |
| by 21:30:42 | (no loss entry) | rx errors 2539 -> 4903, lost unchanged (cf. the second burst in the morning) |
| 21:30:34 | 21:35:21-24 PLC: bus restart, all slaves OP; then `OnlineLicenseManager: Demo mode expired.` | GA_EV 8 from UnInited (expect_st 10) |
| 21:32-23:32 | 2 h clean (lost 2538, rx 4903 unchanged) | `bus_watch.py --hours 2` |
| 00:42- | overnight watch, two 3.8 h legs | |

So the dropout came 1 h 34 min after the app start and ~60 s after the
last daemon login, with only the UI's routine traffic on the wire: not a
"first contact" effect. Same signature as before (QEC + reel leave OP,
~2.5 s, 0x8130 from the three ASDA).

Open: the `Demo mode expired` line (first time in the log; the PLC log
only reaches back to 15:50 today, so no comparison with older runs).
SoftMotion finds its legacy licenses at every start and the project uses
SM3 4.18 throughout; which component ran in demo is unknown. The dropout
fell ~2 h after the IDE download (19:27 PLC -> 21:26 PLC), but the 2 h
after the GA_EV 8 restart (21:30 -> 23:30 PC) passed clean, so "2 h after
a (re)start" does not hold. Evidence:
`codesys_scripts/jobs/incidents/20261006-212128_bus_watch/` (with the PLC
log, saved afterwards: read_plc_log.py had a hard-coded old-PC path).

6 h triangle (`soak_logs/tri6h_1005.log`, `dwell_tri6h.csv`): stop error
mean 23.2-23.6 um and +10 ms 5.6-6.3 um in every 15-min window, flat. The
+5 ms mean rose slowly from 9.2 (first minute) to 10.2 um (last hour): the
share of stops in the upper mode of the bimodal +5 ms distribution went from
58 % to 66 % within the first hour and then stayed, while the upper mode
itself moved up (median 10 -> 12 um) steadily over the 6 h. The residual
oscillation at 5 ms grows a little as the machine warms; it has died out
by 10 ms either way. EAxis0's rate of torque steps >= 20 % rated fell from
~112-118 to ~95 per 10k moving cycles in the last two hours (EAxis1 / 2
flat), also consistent with warming. 4 stops of 1-3 ms (not the planned
10 ms dwell; likely the exact stop at the top of the Cor 0 rise held for 1-3
cycles) carry the arrival error in all four columns (max 33 um); ~1 unplanned
pause of 40-877 ms per 10 min (queue ran dry or a G4 retry; not yet told
apart).

Triangle stop errors (33,166 stops, um): at arrival mean 23.3 (17-30),
+5 ms 9.3 (3-13), +10 ms 5.8 (1-11), leaving 5.8. The three corners (one per
arm direction) have identical distributions; the two modes of the 5 / 10 ms
histograms are anti-correlated within one stop (a residual oscillation of
about +-2.5 um around 7 um, period ~10 ms, sampled at a varying phase
because the 10 ms dwell lands on 9 or 10 cycles), not an axis difference.
Torque RMS share on the triangle 33 / 35 / 32 % (square: 31 / 42 / 28 %,
the square's geometry, not EAxis1).

The 2026-10-05 09:10 dropout, with the PLC log readable again (section 3): the
master's `Frames Lost` stayed **0**; `Drive=0xB: ETC device is no longer in
mode operational`, then every 2 s `watchdog for opmode expired. Address:
1002` (the QEC) with `unexpected working counters: number of slaves has
changed` until the re-download. So that one was the QEC leaving the bus
(its link), not a frame loss on the line. The owner found the QEC wiring
unstable and is fixing it (09:13).

Ruled out:
- the every-stop log (SYS DWELL) and the CSV readout: the program without
  it dropped too (15:01);
- motion and load: the bus drops at standstill;
- the detached UI / harness: the virtual run used them and stayed clean.

Getting more frequent through the afternoon. Not yet ruled out: a
supply shared by the QEC and the reel, their wiring, either device
itself, the EC0808 -> QEC cable re-plugged on 2026-10-02.

## 3. What cannot be read

The CODESYS EtherCAT libraries (EtherCATStack 4.9, IODrvEtherCATDriver)
offer SDO / FoE / SoE / VoE and master statistics only: no access to a
slave's ESC registers, so the per-port RX error and lost-link counters
(0x0300-0x0313) that would name the failing link cannot be read from the
PLC. A separate EtherCAT diagnostic tool (e.g. TwinCAT) on the bus in
place of the PLC could read them; a power cycle of the slaves clears
them. The owner has no such tool.

## 4. Next steps (on the machine)

1. The QEC's and the reel driver's power: one shared supply? Terminals,
   voltage, temperature; do their LEDs show a reboot now and then?
2. Power the reel driver (or the QEC) from a separate supply, or bypass
   one of them (`jobs/templates/set_slaves_bypass.py`), and watch with
   `tools/bus_watch.py`.
3. Replace the EC0808DN -> QEC cable.

## 5. Tools

- `tools/circle_soak.py`: long soak (`--shape round|dip|zud`, `--speed`,
  `--minutes`, `--virtual`, `--dwell-log CSV`); per minute late %, torque
  change, EtherCAT lost frames and the every-stop summary; stops on a
  dropout and puts the delta back to virtual.
- `tools/soak_segments.py`: soaks over 2 h in 60 min segments,
  `--hours H --recover N` re-downloads after a dropout and goes on. Run it
  detached (PowerShell `Start-Process`), output in
  `codesys_scripts/jobs/soak_logs/`.
- `tools/bus_watch.py`: standstill watch, every 10 s; on a dropout logs the
  slave states and re-downloads.
- PLC SYS DWELL (GVL.Dw*): every stop of the delta, 10-15 ms PnP dwells
  too: dwell, settle time to 0.2 mm, error at the stop and when leaving.
  Square dip 70 %: errors at the stop ~23 um, when leaving ~5 um, all
  within 0.2 mm before leaving.

After a dropout: the delta is virtual and off (circle_soak does it,
`DELTA_MODE real:0` when the axes sit in errorstop), the bus needs a
re-download (`tools/safe_install.py`); SDO to the slaves fails (error 4)
until then. After a machine power cycle the USB-powered ESP32 of the
EasyCAT must be reset too, then a re-download.
