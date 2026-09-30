# Axes: map, units, limits, how they were measured (2026-09-29)

State of the real project (`NewPrj/PackerX.project`) after today's work on
the machine, and the tools to check it again. Details and the raw
measurements are in `flow_review_2026-09-26.md` (sections from "Reel on
the machine" on).

## Axis map

| SoftMotion axis | drive | wired to | mode now | in group SpiderR |
|---|---|---|---|---|
| EAxis0 / 1 / 2 | ASDA-B3-E x3 (EtherCAT) | the delta arms, 31:1 | **virtual** (keep until the owner says otherwise) | main kinematics `Kin_Tripod_Rotary` |
| SM_Drive_GenericDSP402 | QEC 3-axis stepper driver, logical device 1 = **axis 2 (M2 / "Y")** | the A rotation (open-loop stepper, no encoder) | **real** | **additional axis 0** (was tool kinematics `Kin_CAxis`, see below) |
| EAXIS_A | same QEC, logical device 0 = axis 1 (M1 / "X") | nothing | real, never commanded | no |
| reelpullmotor | CL3-E57H closed-loop stepper | the tape reel (sprocket) | **real** | no (single axis, `MC_MoveRelative`) |

`axes_sim_mask` / `SET_AXIS_SIM` cover EAxis0-2 and the reel only.

## Units

| axis | unit | scaling |
|---|---|---|
| delta joints | degree at the joint | motor turns x31 |
| A | **degree** | 6400 steps (one motor turn) = 360 u |
| reel | mm of tape | 51200 counts = 200 mm, a cell 8 mm, modulo 200 |

A: `G1 A` is in degrees and goes to the axis 1:1, no wrap. Before, A was
the tool kinematics `Kin_CAxis`, which normalises to +-180; the PLC
divided by 10 and the scaling (36 u per turn) multiplied back. See "A as
additional axis" below.

## Dynamic limits (`jobs/templates/set_axis_limits.py`)

| axis | velocity | acceleration / deceleration | jerk | basis |
|---|---|---|---|---|
| delta joints | 1160 deg/s (~6000 motor rpm) | 100 000 deg/s^2 | 1e7 deg/s^3 | production peaks 946-1084 deg/s, <= 78 500 deg/s^2, ~1e7 jerk; the ASDA-B3's ~6000 rpm |
| A | 2880 deg/s = 8 turns/s | 43 200 deg/s^2 = 120 turns/s^2 | 864 000 deg/s^3 | production reached 92-97 %; step loss watched with the bottom camera |
| reel | 5000 mm/s | 100 000 mm/s^2 | 1e5 mm/s^3 | = `TAPE.REEL_MOVE` |

Before: delta 100 000 / 800 000 / 1e7 and A 180 000 u/s (no limits), and
A virtual. `jobs/templates/set_a_additional_axis.py` sets A's group
role, scaling and limits (`set_group_a_axis.py` was the Kin_CAxis setup).

How the planner uses them (measured):
- The planner holds every axis of the group within its limits. When a
  segment needs more, the whole segment slows; nothing faults.
  `tools/limit_test.py` at F 100 000 / ACC 1e7 / JERK 1e9 found no axis
  over its limits. Delta joints reached 88-95 %; short strokes are
  jerk-bound. A reached 100 %.
- It treats the delta joint limits conservatively. 1100 deg/s +
  80 000 deg/s^2 cost 3-5 % though no joint reached them. 1160 / 100 000
  run as fast as no limits.
- A segment that turns A takes at least A's time. An A-only segment has
  no path length, so its time is set by A's limits alone.

## Motion time per part vs A (`tools/a_speed_compare.py`)

The production sequence, queued, timed to stop (20 parts, same angles):

| A limits | A as in production | A held | group without A |
|---|---|---|---|
| none (old virtual A) | 0.68 s | 0.59 s | 0.59 s |
| 1 / 3 / 5 / 8 turns/s | 2.31 / 1.34 / 1.11 / 0.91 s | 0.59 s | |
| 8 turns/s, joint limits set | 0.90 s | 0.595 s | |

A in the group costs nothing while it does not turn. Its cost is the
rotations under A's limits. Moving them onto the long arm moves (no
A-only G1) saves about 0.08 s at 8 turns/s.

## Tools

| tool | what |
|---|---|
| `tools/plc_direct.py` | msgpack client for the PLC, no UI (close the UI link first) |
| `tools/a_axis_test.py` | A moves, positions, peaks against the limits |
| `tools/a_speed_compare.py` | motion time per part, with / without A rotations (`--held-only` for a group without A) |
| `tools/a_blend_stress.py` | queued blended G1s with A, many rounds: FSM, A end position, task times |
| `tools/intewell_watch.py` | logs the PLC's RTOS task table / cpuuse over telnet, for post-mortem of a hang |
| `tools/limit_test.py` | back-and-forth strokes at absurd dynamics: limits hold? |
| `tools/reel_real_test.py` | the reel: cell count, odometer, fault mid move |
| `tools/ec_stress.py` | EtherCAT / task timing under load (`--rounds N`) |
| `tools/sim/run_virtual.py --plc 192.168.1.70 --peaks / --task-stats` | the real production flow against the machine, virtual arms, sensors simulated for the run |

## Open

1. The real project: A as additional axis. Installed, single moves OK;
   the PLC hung on the first queued blended sequence (see "On the
   machine"). Waits for a power cycle.
2. The UI speed override should also scale the axis limits
   (`VelFactor` x f, `AccFactor` x f^2, `JerkFactor` x f^3). Today a
   segment held back by A ignores the override.
3. Delta real again: check torque and following error at these limits.
4. A: step-loss margin, and homing with the QEC's Y HOME input. The
   step-loss test decides how far the A limits can go up (see "A limits:
   how much faster").
5. The sim project has SM3 4.20, CmpIecTask and A as additional axis
   (limits x1), but still the old delta limits (100 000 / 800 000 / 1e7).
   Compare timings within the sim only.
6. The PLC hang: cause open (see "On the machine").

## A as additional axis (2026-09-29, sim)

SpiderR has no tool kinematics now; `SM_Drive_GenericDSP402` is its
additional axis 0. Each G1 passes an `SMC_MoveAdditionalAxesAbsolute`
(`AdditionalAxes` input) with the A target. The planner moves A with the
path in the same movement, so the group's progress triggers and blending
cover A as before.

What it takes:
- SoftMotion 4.20. SM3_Robotics 4.18 has no `AdditionalAxes` input.
  `set_sm3_420.py` redirects every SM3 placeholder of the Application to
  the newest installed version (4.20.x; drive libraries 4.19). This
  includes the nested ones (SM3_RBase, SM3_Math, ...). Redirecting only
  the top level gave 501 build errors.
- 4.20's `TransitionParameter` has 3 elements (`[2]`: the additional axes'
  share of the blending). It is declared as
  `ARRAY [0..SMC_RCNST.MAX_TRANS_PARAMS-1]`, `[0, 0, 1]`.
- `set_a_additional_axis.py`: the group, the scaling (360 u per turn) and
  the limits in degrees.
- PLC: no `A_AXIS_KIN_WRAP_SCALE`. `AddAxTargetA` is sticky (a G1 without
  A keeps it) and is reset to 0 like the pose when the group is not
  Ready. There is a ring of 128 `SMC_MoveAdditionalAxesAbsolute`, one per
  accepted G1, so a queued move never shares an instance. At most 12 are
  queued. `READ_LATEST_CMD_LOCATION` reports A in degrees.

Checked on the sim (`tools/a_axis_test.py`, `limit_test.py`,
`a_speed_compare.py`, `run_virtual.py --peaks`):
- A to 90 / 0 / 270 / -90 / 540 / -720 / 0 deg: exact, beyond +-180.
- X + A in one G1: done together. A G1 without A leaves A where it was.
- 5 queued G1s at Cor 45 with different A: ends at the last A.
- The limits hold: A peaks 2880 deg/s, 43 061 deg/s^2.
- Motion per part, same sim, same limits, old vs new:

  | | Kin_CAxis (/10) | additional axis |
  |---|---|---|
  | A rotating | 1.058 s mean (max 1.486) | 1.040 s mean (max 1.278) |
  | A on long moves | 1.035 s | 1.011 s |
  | A held | 0.661 s | 0.660 s |

- Production flow, 31 tape checks, 22 cells: no error. The top-shot
  triggers are on time.

The sim is about 10 % slower than the machine in absolute terms. Its 1 ms
tasks ran 9539 cycles in 10 s. SoftMotion advances one fixed cycle per
call, so the motion stretches in wall time. The planned motion is the
same. Compare old and new on the same PLC only.

## On the machine (2026-09-29 22:55)

`set_sm3_420.py` + `set_a_additional_axis.py` on the real project, build
0 errors, `install --on-site`: download 19.7 s, app in RUN, EtherCAT OK,
DC in sync (no new out-of-sync cycles after start-up), EtherCAT_Task avg
93 / max 288 us, planning max 26 us. `a_axis_test.py` with the real A:
every single move exact (90 / 270 / -90 / 540 / -720 deg, X + A, sticky
A), peaks 2880 deg/s, 43 081 deg/s^2.

Then the 5 queued Cor 45 G1s with A: the PLC stopped answering. Ping
still answers, every TCP port (23 telnet, 1217, 11740, 8125/8126) times
out. The Intewell shell is gone too, so no remote recovery; it needs a
power cycle. The same symptom happened once before on the SM3 4.18 build,
so the cause is open.

The sim does not reproduce it: `tools/a_blend_stress.py --rounds 200`
(the hang sequence, then 200 rounds of 3-12 queued G1s with A, Cor 0 / 5
/ 45): 0 failures, planning max 3.3 ms. The differences to the machine:
the RTOS (Intewell, fixed task stacks), the real A drive path
(GenericDSP402 in the EtherCAT task; the sim's A is virtual).

Reproduced 2026-09-30 08:32 after the owner's power cycle. The PLC
booted the 4.20 build: FSM UnInited, EtherCAT OK, task times normal
(EtherCAT avg 85 / max 305 us). `a_axis_test.py` was rerun with
`tools/intewell_watch.py` logging the RTOS task table every ~1 s and
`cpuuse` every 5 s over telnet. It hung at the same step:
- All single moves were exact again: A only, X + A in one G1, sticky A.
  These are Cor 0 moves, no blending.
- Then the 5 queued Cor 45 G1s were sent (08:32:38). The msgpack
  connection closed. The shell answered until 08:32:36 and was gone by
  08:32:40.
- The last blocks (08:32:33-36) show nothing unusual: all tasks in their
  normal state, idle 95-98 %. The crash took the whole OS (shell,
  tcp/ip) within ~2 s of the first blended G1; a 1 s log cannot show more.
- The per-task tick counts are lumpy (a task gets ~1 s of ticks in one
  block, then 0), so they cannot pin down which task ran away.

So: deterministic on the machine (2 of 2), at the first blended
(BlendingNext) movement with A as additional axis on SM3 4.20; not on the
sim (Windows, 200 rounds). Production blends every G1 (Cor 45), so this
build cannot run production. The 4.18 build blended for months; its one
earlier hang is not known to be the same trigger.

Next (each hang costs a power cycle), to split the cause:
1. 4.20 build, queued Cor 45 G1s without A: blending in 4.20 itself?
2. 4.20 build, queued Cor 0 G1s with A: queueing with A, no blend?
Or roll back to the 4.18 build (snapshot
`jobs/snapshots/20260929-225357_sm3_420.project`, sources at git
d62b891^) and keep Kin_CAxis with /10: the same speed (see the A/B
table).

## Shorter A rotations in the cycle (2026-09-29, sim)

Per part, A turns three times. From the place angle to the next pick
angle happens on the way to the feeder. From the pick angle to 90 deg
happens on the way to the cameras. The half turn plus correction happens
at the inspection station, standing still: it must be done before the
rectified side shot. The pick angle was the feeder's angle as is, so
after a flipped part A could turn up to ~440 deg on the way to the
feeder.

Now `pickAngle()` (`nozzle.ts`) picks at the feeder angle or a whole turn
off it (the round nozzle holds the part the same way), whichever makes
the longer of the two travel rotations shortest. A stays within about a
turn of 0. The inspection half turn goes the shorter way (-180 when the
correction is positive). A:0 before the pick is sent only at the start of
a run.

Estimate at the current A limits (random angles): to the feeder, mean
0.179 -> 0.145 s, worst 0.272 -> 0.215 s. To the cameras, about the
same. The half turn at the station is unchanged (mean 0.125 s). Sim
production run: place to next place, median 1176 -> 1135 ms, p90 1375 ->
1318 ms. A peak speed in the run 2880 -> 2010 deg/s.

The standing half turn is now the largest A cost. Only higher A limits
shorten it: with jerk x5, 180 deg takes 0.14 s instead of 0.19 s. That
needs a step-loss test on the machine.

## How the planner times a segment with A (measured)

The planner scales the slowest axis's time, not a combined speed. `F` is
the speed along the XYZ path only; A is not part of the path length (as
an additional axis now, and as the Kin_CAxis orientation before). A
follows the path in proportion: at 30 % of the path, 30 % of the
rotation. The planner then slows the whole segment until no axis
(delta joints, A) is over its velocity / acceleration / jerk limit. So a
segment takes about max(XYZ time at F, A time at A's limits). A segment
with only A has no path length; A's limits alone set its time.

| measurement | time |
|---|---|
| machine, A at 1 turn/s: X only | 0.15 s |
| machine, A at 1 turn/s: A 90 deg only | 0.46 s |
| machine, A at 1 turn/s: X and A 90 deg in one G1 | 0.46 s |
| sim, today's limits: A 90 deg only | 0.176 s |
| sim, today's limits: X 30 mm + A 90 deg in one G1 | 0.177 s |

A turning with an arm move is free while it takes less time than the
move. When it takes longer, the whole segment slows, X included, so both
arrive together. The standing half turn at the inspection station has no
arm move to ride on, so all of it counts.

## A limits: how much faster (2026-09-30, sim)

### Rest-to-rest time of one A move (jerk-limited S-curve, computed)

| A limits (deg/s, deg/s^2, deg/s^3) | 90 deg | 180 deg | 270 deg | 450 deg |
|---|---|---|---|---|
| x1: 2880 / 43 200 / 864 000 (8 turns/s, 120 turns/s^2) | 0.149 s | 0.188 s | 0.216 s | 0.273 s |
| jerk x5 only | 0.102 | 0.139 | 0.170 | 0.233 |
| 3600 / 72 000 / 4.32e6 (10 turns/s, 200 turns/s^2) | 0.089 | 0.118 | 0.142 | 0.192 |
| x2 / x2 / x2: 5760 / 86 400 / 1.728e6 | 0.119 | 0.149 | 0.171 | 0.203 |
| x2 / x4 / x8: 5760 / 172 800 / 6.912e6 | 0.075 | 0.094 | 0.108 | 0.136 |
| 4320 / 108 000 / 8.64e6 (12 turns/s, 300 turns/s^2) | 0.072 | 0.095 | 0.115 | 0.157 |

The computed x1 times match the machine: A 90 deg measured 0.160 s,
round trip included.

Why x2 / x4 / x8: the same motion profile at half the time needs 2x the
speed, 4x the acceleration and 8x the jerk. With all three x2, only the
speed scales fully. The A moves here (90-270 deg) are short and spend
most of their time accelerating and braking, so x2 / x2 / x2 gives only
about 1.26x.

### Measured on the sim

The same sim, one setting after another (`set_axis_limits.py`-style job
on SM_Drive_GenericDSP402 only, install, then both tools). The cycle
code with `pickAngle()`. Medians: `a_speed_compare.py`'s means swing
with one fixed outlier part (up to 2.6 s).

| | x1 | x2 / x2 / x2 | x2 / x4 / x8 |
|---|---|---|---|
| `a_speed_compare.py`, A as in production, median per part | 1.025 s | 0.892 s | 0.751 s |
| `a_speed_compare.py`, A on long moves, median | 0.936 s | 0.841 s | 0.718 s |
| `a_speed_compare.py`, A held, median | 0.660 s | 0.661 s | 0.658 s |
| `run_virtual.py --cycles 30`, place to next place, median | 1135 ms | 999 ms (-12 %) | 867 ms (-24 %) |
| `run_virtual.py`, place to next place, p90 | 1318 ms | 1146 ms | 942 ms |
| A peak in the production run, velocity | 2010 deg/s | 2560 deg/s | 3778 deg/s (630 rpm) |
| A peak in the production run, acceleration | ~40 500 deg/s^2 | 64 326 deg/s^2 | 146 145 deg/s^2 (406 turns/s^2) |

At x2 / x4 / x8 the part with A turning is only ~0.09 s slower than with
A held. A is no longer the main cost; the delta moves and the camera
waits are. The sim is back at x1 (the repo's limits are x1).

### On the machine: what it takes

A is an open-loop stepper. The acceleration needs torque: x4
acceleration is about 4x the torque for the rotor and the nozzle
inertia. A stepper's torque falls with speed. A missing step is not seen
by the PLC. Before raising the limits on the machine:
1. Raise one step at a time. Jerk first: it costs no extra torque at
   peak and gives most of the first gain (180 deg: 0.188 -> 0.139 s at
   jerk x5). Then acceleration, then speed.
2. At each step, run the production rotations many times, then check the
   angle: the bottom camera's angle error on a known part, or a move back
   to the Y HOME input.
3. Settle below the first setting that loses steps, with margin.

`set_axis_limits.py` holds the chosen A limits (degrees).

## Numbers at a glance (2026-09-29 / 30)

| what | value | where |
|---|---|---|
| per-part motion, A held (sim) | 0.66 s | same with Kin_CAxis and additional axis |
| per-part motion, A turning, x1 (sim, median) | 1.03 s | Kin_CAxis 1.03 s too |
| place to next place, sim production, x1 before / after `pickAngle` | 1176 / 1135 ms median | |
| same, x2 / x2 / x2 and x2 / x4 / x8 | 999 / 867 ms | |
| A 180 deg, x1 / x2x4x8 | 0.188 / 0.094 s | computed; machine matches at x1 |
| sim vs machine | sim ~5-10 % slower | 1 ms tasks run 95 % of their cycles |
| real PLC with SM3 4.20: task times | EtherCAT avg 93 / max 288 us, planning max 26 us | before the hang |
| sim blended stress | 200 rounds, 0 failures, planning max 3.3 ms | `a_blend_stress.py` |
