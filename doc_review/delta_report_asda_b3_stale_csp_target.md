# ASDA-B3-E in CSP mode intermittently uses the previous cycle's target position

**To:** Delta Electronics technical support (servo / EtherCAT)
**Drives:** ASDA-B3-E (three units, EtherCAT), firmware shown in ASDA-Soft as
`B3-E-Ver22106`.
**Date of tests:** 2026-09-30 to 2026-10-01.

## Summary

In CSP mode with DC SYNC0 synchronisation, the drives intermittently use the
target position of one cycle earlier than usual. Their own position demand
value (0x6062) then:
1. repeats the previous increment, or stays still for one cycle;
2. catches up with a double step in the next cycle.

On a delta robot this is felt as small knocks and vibration.

The master's targets are smooth every cycle. This was verified at the PLC
and on the bus with an independent DC slave in the same frame. The drives'
own sync counters stay at 0 (0x1C32:0B, :0C, :20). The fraction of affected
cycles:
- varies between EtherCAT starts: 0.04 % to 4.5 % in identical conditions;
- drifts slowly within one start.

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

## What we observe

Every cycle we compare the drive's position demand value **0x6062**
(TxPDO) with the target position **0x607A** we sent:

- **Normally,** 0x6062 equals the target sent **3 cycles** earlier, every
  cycle (a constant pipeline delay).
- **Sometimes,** for one cycle, 0x6062 equals the target sent **4 cycles**
  earlier. The drive then either repeats the previous increment or moves
  ~0, and the next cycle catches up with ~2 increments.

Example, single joint at constant speed (no path planning). These are
per-cycle increments in PUU; the master sends 144 or 145 every cycle:

```
sent (607A):    145 144 145 144 145 144 145 144 144 145 144 145 144 145
drive (6062):     0 145 145  -1 145 144 289 289   0 145 145 143 288   1
```

Example, robot path at higher speed (per-cycle increments, PUU):

```
sent (607A):    41043 43404 44989 45792 45844 45662 45591 45613
drive (6062):   41043 43404 43404  1585 91636 45662 45662   -71
```

The same is visible on the ASDA-Soft scope (8 kHz, Cmd Pos). One 1 ms
interpolation segment repeats the previous slope, followed by a correction
step. The following error and motor current spike at the same instant.

## What we ruled out

| Suspect | How it was checked | Result |
|---|---|---|
| Master trajectory | Second difference of the commanded position every cycle (float and integer) | Smooth; no glitch in > 1,000,000 cycles |
| Data written into the frame | Read-only tap on the 0x607A process image every cycle, compared with the axis set value | Equal every cycle (131,012 / 131,012) |
| Frames lost / bus errors | Master statistics | 0 lost, 0 TX / RX errors |
| Frame timing vs SYNC0 at the end of the line | ESP32 + LAN9252 slave (last on the line, DC SYNC0) reading a cycle counter and a copy of the drive's target at every SYNC0, and timing the frame arrival | 0 stale, 0 skipped. The frame arrives 660-825 us after SYNC0, >= 175 us before the next SYNC0 |
| Path planning | Single joint at constant velocity, no planner | Still 2-5 % of cycles |
| Feeding pattern | Rounds of 8 moves with stops vs continuous | Same rate |
| Cycle time | 2 ms instead of 1 ms | Same fraction of segments |
| P3.009.Z (read delay) | 0 -> 3 on one drive | No change |
| ASDA-Soft USB monitoring (AL304 hint) | USB unplugged | Still 3-4 % |
| Drive's own sync diagnostics | 0x1C32:0B, :0C, :20 read during the events | Always 0 |

## Rate of the effect

Single joint at 0.1 deg/s, SyncOffset 50 %. The rate is the share of moving
cycles where 0x6062 is one cycle late.

| Condition | Rate |
|---|---|
| Three EtherCAT starts, identical settings | 0.04 %, 3.13 %, 4.46 % |
| One start, four servo-off/on cycles over ~5 min | 3.05 %, 3.43 %, 3.87 %, 4.16 % |
| Master SyncOffset 30 / 40 / 50 % (one start each) | 5.62 % / 3.42 % / 0 % (50 % gave 4.3 % on another start) |

All three drives show it at similar rates.

## Questions

1. Is this behaviour known for ASDA-B3-E firmware Ver22106 in CSP + DC
   SYNC0? Is there newer firmware that fixes it?
2. When does the drive latch the RxPDO relative to SYNC0, and how does its
   internal control cycle lock to SYNC0? The phase seems to change with
   each EtherCAT start and to drift slowly within one start.
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

We can provide the ASDA-Soft scope recordings (.parscp), the per-cycle data
captures, and the drive parameter files.
