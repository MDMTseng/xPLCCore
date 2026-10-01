# ASDA-B3-E in CSP mode periodically uses target positions from the wrong cycle

**To:** Delta Electronics technical support (servo / EtherCAT)
**Drives:** ASDA-B3-E (three units, EtherCAT), firmware shown in ASDA-Soft as
`B3-E-Ver22106-Sub 212, 96, L=120`.
**Date of tests:** 2026-09-30 to 2026-10-01.

## Summary

In CSP mode with DC SYNC0 synchronisation, the drives periodically take the
target position (0x607A) from the wrong EtherCAT cycle. Every 2.4-3.6 s
there is a burst of a few to a few tens of milliseconds. During a burst:
- the drive's interpolated command position has slope steps (2x or 3x the
  normal slope);
- the command position jumps by up to 200 PUU within 1/8 ms.

On a delta robot this is felt as small knocks and vibration.

The fault remains when the motion library is bypassed completely. Our PLC
code writes the controlword and an exactly constant ramp (+145 PUU every
1 ms cycle) straight into the RxPDO, and the drive still shows the bursts.

The master side is clean:
- the targets are smooth every cycle, verified at the PLC and on the bus
  with an independent DC slave in the same frame;
- no frames are lost;
- the drives' own sync counters stay at 0 (0x1C32:0B, :0C, :20).

The bursts:
- do not depend on speed;
- appear at the same rate on all three drives;
- vary in rate between EtherCAT starts.

We would like to know whether this is a known issue, whether a firmware
update addresses it, and which settings you recommend.

## System

| Item | Value |
|---|---|
| Controller | CODESYS Control runtime (CODESYS V3.5 SP22 Patch 3 IDE, SoftMotion SM3_Basic 4.18, IODrvEtherCAT 4.11) on an Intewell RTOS industrial PC, Intel I210 EtherCAT port |
| Topology | Line, 7 slaves: stepper driver (DC reference), reel servo (other vendor), **ASDA-B3-E x3** (stations 1003-1005), IO coupler, ESP32 + LAN9252 test slave (last on the line) |
| Cycle | 1 ms (also tested 2 ms); DC SYNC0 = cycle; master SyncOffset 50 % (also 20 / 30 / 40 / 75 %) |
| Drive mode | CSP (0x6060 = 8); 0x1C32:01 = 2 (DC SYNC0); 0x60C2 = 1 ms (2 ms in the 2 ms test) |
| ESI | "Delta ASDA-x3-E rev0.04" (from the CODESYS SoftMotion package) |
| RxPDO 0x1601 | 6040, 607A, 60FF, 6071, 60B8 |
| TxPDO 0x1A01 | 6041, 6064, 606C, 6077, 60B9, **6062** (mapped in place of 60BA for this test), 60FD |
| Drive parameters | P3.009 = 0x5055, P3.022 = 0xFF04, P1.008 = 10/20/20 ms, P1.068 = 2/10/4 ms (now 6/6/6 ms), P2.000 = 351/479/351, P2.002 = 0 |

## Minimal test case: no motion library

- The motion library leaves the drive alone. The PLC application writes the
  controlword (0x6040) and target position (0x607A) directly.
- The sequence:
  1. CiA402 enable: 0x06, 0x07, 0x0F.
  2. Then exactly **+145 PUU every 1 ms cycle** (about 0.1 deg/s) up 3 deg,
     then back.
- Every cycle we compare the drive's position demand value **0x6062** (in
  the TxPDO) with the targets we sent.

Normally 0x6062 equals the target sent **3 cycles** earlier. Results:

| Run | Cycles | 0x6062 = target from 4 cycles back | from 2 cycles back |
|---|---|---|---|
| 1.5 deg, +145 / cycle | 29,892 | 1,563 (5.2 %) | 24 |
| 1.5 deg, +145 / cycle | 29,892 | 1,719 (5.8 %) | 21 |
| 3 deg, +145 / cycle | 59,782 | 1,997 (3.3 %) | 27 |
| 3 deg, **+72 / cycle (half speed)** | 120,394 | 4,366 (3.6 %) | 73 |

The rate does not depend on speed. Halving the speed doubles the time, and
the count doubles with it.

Per-cycle increments (PUU) during a burst:

```
sent  (607A):  145 145 145 145 145 145 145 145 145 145 145  145 145
drive (6062):  145 145 145   0 145 145 145 290 290 -145 145 145 145
```

In this burst the drive's delay goes 3 -> 4 -> 3 -> 2 -> 3 cycles. It takes
both older and newer targets than usual.

## ASDA-Soft scope (8 kHz) of the same test

Recording `scope22.parscp`: half speed, 120 s, channels feedback position,
command position, following error and motor current.

### Inside one burst

Normally the drive interpolates each 1 ms segment in 8 equal sub-steps of
-9 PUU (-72 per ms). Excerpt from 70838-70875 ms:

```
ms      cmd/ms   8 sub-samples of Cmd Pos (1/8 ms)      following error
70837     -72     -9  -9  -9  -9  -9  -9  -9  -9          -558
70838     -72     54 -18 -18 -18 -18 -18 -18 -18          -544
70839    -144    -18 -18 -18 -18 -18 -18 -18 -18          -535
70840       0    126 -18 -18 -18 -18 -18 -18 -18          -591
70841     -72     -9  -9  -9  -9  -9  -9  -9  -9          -529
 ...
70855       0    126 -18 -18 -18 -18 -18 -18 -18          -630
70856    -144    -18 -18 -18 -18 -18 -18 -18 -18          -548
70857       0    126 -18 -18 -18 -18 -18 -18 -18          -622
 ...
70871    -144     45 -27 -27 -27 -27 -27 -27 -27          -502
70872    -216    -27 -27 -27 -27 -27 -27 -27 -27          -556
70873      72    198 -18 -18 -18 -18 -18 -18 -18          -694
70874    -144    -18 -18 -18 -18 -18 -18 -18 -18          -562
70875      72    135  -9  -9  -9  -9  -9  -9  -9          -612
70876     -72     -9  -9  -9  -9  -9  -9  -9  -9          -463
```

What the excerpt shows:
- The interpolation slope becomes 2x (-18) or 3x (-27) the normal value.
  The segment end points come from targets of different cycles.
- At the start of a segment the command position jumps back by up to
  198 PUU in one 1/8 ms sample. The command is discontinuous.
- For about 37 ms, roughly half of the segments are wrong, alternating
  between old and new targets.
- The following error swings from about -530 to between -443 and -694 PUU.

### Periodicity

There are 44 bursts in 120 s. They are 2.37-3.57 s apart (2.8 s on average),
in a repeating sequence of about **2.37 -> 2.58 -> 3.5 s**, so about 8.5 s
per round. The largest bursts recur about every 8.3-8.5 s.

Burst start times (ms): 374, 2962, 5346, 8896, 11492, 13858, 17327, 19994,
22389, 25588, 28539, 30912, 33898, 37043, 39437, 42217, 45574, 47941,
50527, 54102, 56471, 59052, 62524, 64997, 67579, 70399, 73519, 76095,
78545, 82040, 84600, 86993, 90548, 93131, 95501, 99065, 101666, 104040,
107400, 110186, 112561, 115712, 118684.

This regular pattern suggests that the drive's internal point of taking
the new target drifts relative to SYNC0, periodically crossing the instant
when the new PDO data becomes valid. It might be a beat between the drive's
control clock and SYNC0, or a periodic correction of the drive's sync loop.

### Timing across the three drives (added 2026-10-01 afternoon)

We logged, per drive, the time of every burst (the start of a stretch
where 0x6062 is one cycle late or early). Test conditions:
- the three drives turn alike (pure Z move of the robot, 60 s);
- EAxis1 and EAxis2 run firmware Ver22106;
- EAxis0 was updated to a newer firmware [version: to fill in].

| Pair | Bursts within 50 ms | within 500 ms |
|---|---|---|
| EAxis1 vs EAxis2 (same firmware) | 8 of 13 | **13 of 13** |
| EAxis0 (new firmware) vs the others | 0 of 13 | 1-2 of 13 |

A second run with EAxis0 at factory parameters gave the same picture
(12 of 14 vs 2-5 of 15).

- The two drives with the same firmware burst at the same moments (mostly
  within 10-75 ms), with identical intervals.
- The drive with the newer firmware bursts at other moments, with intervals
  from the same family (2.4 / 2.6 / 3.5 s).

This suggests a slow periodic drift of the frame timing relative to SYNC0
that all drives see. Each firmware's pick-up point then fails at a
different phase of that drift. The frame arrival measured at the end of
the line varied between 660 and 825 us after SYNC0, which is more than
half a cycle away from SYNC0 in both directions. We would like to know:
- the drive's pick-up window relative to SYNC0;
- why a timing that stays inside the cycle can make the drive take the
  wrong target.

## What we ruled out

| Suspect | How it was checked | Result |
|---|---|---|
| Motion library / path planning | Bypassed: PLC code writes 0x6040 and 0x607A directly, constant +145 / +72 PUU per cycle | Still 3.3-5.8 % of cycles, periodic bursts |
| Master trajectory | Second difference of the commanded position every cycle (float and integer) | Smooth; no glitch in > 1,000,000 cycles |
| Data written into the frame | Read-only tap on the 0x607A process image every cycle | Equal to the intended value every cycle |
| Frames lost / bus errors | Master statistics | 0 lost, 0 TX / RX errors |
| Frame timing vs SYNC0 at the end of the line | ESP32 + LAN9252 slave (last on the line, DC SYNC0) reading a cycle counter and a copy of the drive's target at every SYNC0, and timing the frame arrival | 0 stale, 0 skipped. The frame arrives 660-825 us after SYNC0, >= 175 us before the next SYNC0 |
| Speed | +145 and +72 PUU per cycle | Same rate |
| Feeding pattern | Rounds of 8 moves with stops vs continuous | Same rate |
| Cycle time | 2 ms instead of 1 ms | Same fraction of segments |
| P3.009.Z (read delay) | 0 -> 3 on one drive | No change |
| ASDA-Soft USB monitoring (AL304 hint) | USB unplugged | Still 3-4 % |
| Drive's own sync diagnostics | 0x1C32:0B, :0C, :20 read during the events | Always 0 |

## Variation between starts

Single joint, SyncOffset 50 %. The rate is the share of moving cycles where
0x6062 is one cycle late.

| Condition | Rate |
|---|---|
| Three EtherCAT starts, identical settings | 0.04 %, 3.13 %, 4.46 % |
| One start, four servo-off/on cycles over ~5 min | 3.05 %, 3.43 %, 3.87 %, 4.16 % |
| Master SyncOffset 30 / 40 / 50 % (one start each) | 5.62 % / 3.42 % / 0 % (50 % gave 4.3 % on another start) |

All three drives show it at similar rates.

## Questions

1. Is this behaviour known for ASDA-B3-E firmware Ver22106 in CSP + DC
   SYNC0? Is there newer firmware that fixes it?
2. When does the drive latch the RxPDO relative to SYNC0? How does its
   internal control cycle lock to SYNC0? Is there a periodic correction
   (about every 2.4-3.6 s) in that lock?
3. Which settings do you recommend to make the target pick-up deterministic?
   - P3.009 and its digits;
   - the master's SYNC0 shift;
   - SYNC1;
   - other parameters.
4. Is there an object or monitor variable that reports, per cycle, whether
   the drive got a new target at SYNC0?
   - Statusword bit 14 with P3.019.Z = 1 conflicts with our motion library,
     which reads that bit as the positive limit.
   - 0x1C32:0B stays 0.
5. Is ESI "ASDA-x3-E rev0.04" current for this firmware?
6. Two drives with the same firmware burst at the same moments, while a
   drive with newer firmware bursts at different moments (see "Timing
   across the three drives"). What changed in the newer firmware's
   RxPDO / SYNC0 handling? What frame-arrival window relative to SYNC0
   does each version require?

We can provide the ASDA-Soft scope recordings (.parscp), the per-cycle data
captures, the drive parameter files, and the test program.
