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
| `tools/limit_test.py` | back-and-forth strokes at absurd dynamics: limits hold? |
| `tools/reel_real_test.py` | the reel: cell count, odometer, fault mid move |
| `tools/ec_stress.py` | EtherCAT / task timing under load (`--rounds N`) |
| `tools/sim/run_virtual.py --plc 192.168.1.70 --peaks / --task-stats` | the real production flow against the machine, virtual arms, sensors simulated for the run |

## Open

1. The real project: A as additional axis (done on the sim, see below).
2. The UI speed override should also scale the axis limits
   (`VelFactor` x f, `AccFactor` x f^2, `JerkFactor` x f^3). Today a
   segment held back by A ignores the override.
3. Delta real again: check torque and following error at these limits.
4. A: step-loss margin at 8 turns/s, and homing with the QEC's Y HOME
   input.
5. The sim project has not got today's axis settings (they are device
   configuration, not sources): run the same jobs there when it matters.

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
