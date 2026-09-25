# SoftMotion setup review (2026-09-25)

Scope: the axis group, the axes and how the PLC and the renderer command
motion. Settings read from the project (device tree dump of 2196
parameters, `SpiderR` export); behaviour measured on the PC soft-PLC sim
with `run_virtual.py --path-test` (a circle of short G1s streamed by the
renderer). "Uncertain" marks SM3 behaviour not confirmed from the repo.

## 1. What is configured

**Axis group `SpiderR`** (Application object):
- Main kinematics `TRAFO.Kin_Tripod_Rotary` on EAxis0/1/2: arm 125 / 240
  mm, arm-1 radius 55, Stewart radius 41.04, distance 56, max ball-joint
  angle 45°.
- Tool kinematics `TRAFO.Kin_CAxis` on **`SM_Drive_GenericDSP402`**
  (drive 7), `dOffsetC` 0.
- Planning task `SoftMotion_PlanningTask` (1 ms, prio 5); bus task
  EtherCAT_Task 1 ms. No additional axes.

**Axes** (all `eRampType` 0, `bSWLimitEnable` FALSE, `eCheckPositionLag` 0):

| Axis | virtual in project | max vel | max acc | max jerk | movement |
|---|---|---|---|---|---|
| EAxis0/1/2 | yes | 100 000 °/s | 800 000 °/s² | 1e7 | finite, 31:1 |
| SM_Drive_GenericDSP402 (group A) | **yes** | 180 000 | 200 000 | 2e6 | finite |
| EAXIS_A (drive 6) | **no** | 1 800 | 20 000 | 2e5 | finite |
| reelpullmotor | per project | 5 000 | 100 000 | 1e5 | modulo 200 |

**Motion commands used:** one `MC_MoveLinearAbsolute` for every G1,
`SMC_GroupWait` for G4, `MC_GroupStop` in Error, `MC_SetCoordinateTransform`
(SetCoord0/1), `MC_TrackConveyorBelt` (PCS_1), `SMC_Homing` +
`MC_MoveAbsolute` for homing, two `MC_MoveRelative` for the reel.
BufferMode: Aborting when `abort`, else `Cor >= 1` → BlendingNext,
`Cor < 1` → Buffered; TransitionMode `TMCornerDistance`, parameter = Cor.
Pose, F/ACC/DEA/JERK, FAC and Cor are all **modal** (kept when omitted).
The renderer sends `feedConfig(F)`: ACC = DEA = F·100, JERK = F·400.

## 2. Measurements (PC sim, delta trio virtual)

Circle of radius 20 mm, `Cor` 3 unless noted:

| Case | Result |
|---|---|
| transport (NoDelay, 1 vs 4 packets/scan, await vs send window) | no difference (16 combinations within noise, 0 G1 retries) |
| Cor 0.5 (Buffered: stop at every point) | 67-133 ms per segment |
| Cor 3, 25 x 5 mm, 200 mm/s | 768 ms vs 627 ms of pure motion (x1.2): blending works |
| Cor 3, 200 x 0.63 mm, 1000 mm/s, JERK = F·400 / F·2000 / F·10000 | 3188 / 1439 / 1325 ms |
| MOTION_BUFFER_THRESHOLD 40 instead of 12 | the group rejected G1s (101 ms retries), slower: its own queue is < 40 |
| production, plan 1,-10,60,-11,1, JERK = F·400 vs F·2000 | 60 s vs **53 s**, place-to-place 839 vs 683 ms |

The per-segment floor is the **jerk**: at F·400 the arm needs 0.25 s to
reach full acceleration, so short moves never do.

## 3. Findings

1. **The group's A axis is a virtual axis.** `Kin_CAxis` drives
   `SM_Drive_GenericDSP402` (drive 7, `bVirtual` TRUE in the project); the
   real rotation drive `EAXIS_A` (drive 6) is in no group, never powered
   or commanded, only named in the all-virtual check. Unless something
   outside the code couples them, G1 `A` corrections never reach the
   part on the machine. Also: the /10 wrap trick (`A_AXIS_KIN_WRAP_SCALE`)
   distorts A dynamics, drive-7 limits (180 000) do not match EAXIS_A's
   (1 800), and A is never homed. **Owner to confirm.**
2. **A G1 the planner refuses is retried forever.** `MoveLinearAbsolute.Error`
   is never read: CommandAccepted FALSE always means "retry in 101 ms",
   so an unreachable pose or bad dynamics blocks every packet behind it
   (`ProcessMotionPacket.st` G1 branch; G4 the same). Verified.
3. **Error cuts power in the same scan as the stop.** `MC_GroupStop` is
   issued while `Update.st` drops `bRegulatorOn`/`bDriveStart`: the
   planned stop never runs; the drives decide how the arm stops. Every
   fault (also soft ones: heartbeat, COORD1 window, SetCoord) then needs
   EV_RESET → UnInited → bus restart, power, re-home.
4. **Dynamics are one knob.** ACC and JERK are fixed multiples of F; JERK
   is so low that ACC hardly matters (see §2). Host and CalibPage's own
   bench buttons use different ratios (100/400 vs 200/800).
5. **Modal state leaks.**
   - Cor 45 is sent once and then applies to every move, including the
     short Z plunges at pick and place (the nozzle may enter diagonally
     while XY is still arriving). Whichever page sent Cor last (the burn
     test sends 15) sets production blending.
   - `MotionPose` is zeroed whenever the group is not Ready
     (`CheckAxisGroupReady.st`). After a recovery a partial G1 (`G1({Z})`)
     goes to X = Y = A = 0. Verified.
6. **No protection limits.** No joint software limits, position-lag
   monitoring off on every axis, joint limits that are not real
   (100 000 °/s). Nothing checks a pose before it is queued.
7. **Homing** runs with the group enabled, which leaves it in
   ErrorStop (handled by a 50-scan reset that may race the supervisor);
   switch polarity unverified. Hidden in the sim (homing skipped).
8. **Throughput plumbing**: one motion packet per 1 ms scan, 101 ms after
   any rejection, the group queue < 40 (12 used). Fine for production
   (~20 moves of 10-100 mm per part); not for dense paths.

## 4. Unused SM3 features worth having

- `MC_MoveDirectAbsolute`: joint-space point-to-point for the long hops
  (feeder ↔ inspection ↔ tape), keeping linear moves only for the
  vertical approaches.
- `MC_MoveCircularAbsolute`: arc hops instead of up/across/down.
- `MC_GroupSetOverride`: live speed scaling (commissioning, slow mode).
- `MC_GroupInterrupt` / `MC_GroupContinue`: pause without cutting power
  (STOP, step mode); `MC_GroupHalt` for recoverable faults.
- Other transition modes / per-move blending (uncertain which exist in
  this library version), tolerance-based blending for pick and place.
- Group and joint dynamic limits, joint software limits, workspace
  monitoring, a TCP/tool offset in the kinematics (uncertain names/versions).

## 5. Recommendations (ranked)

1. **Confirm and fix the A axis** (finding 1): map the real drive into
   `SpiderR`'s tool kinematics (or couple drive 7 to it), align limits,
   home it, replace the /10 trick with proper wrap handling.
2. **Read `.Error` on the move FBs**: NAK with the ErrorID and consume
   the packet; retry only when the queue is really full, with a cooldown
   of ~5-10 ms instead of 101.
3. **Stop properly**: `MC_GroupStop`/`Halt` to Done, then power off; a
   recoverable fault state without bus restart and re-homing.
4. **Tune dynamics on the machine**: raise `DYNAMICS.JERK_PER_FEED` in
   steps (400 → 1000 → 2000, sim: 60 s → 53 s per batch) while watching
   vibration and placement accuracy; then set ACC and JERK from measured
   joint limits instead of fixed feed ratios.
5. **Per-move blending, no modal leaks**: small Cor (1-3 mm) or Buffered
   for the plunges, large for travel; always send absolute X/Y/Z/A, or
   reset `MotionPose` from the actual position instead of 0.
6. **Limits**: joint software limits, lag monitoring, a group limit
   check, a host-side workspace check.
7. `MC_MoveDirectAbsolute` for long transfers; `MC_GroupSetOverride` and
   Interrupt/Continue for operator control.
8. Home with the group disabled; verify switch polarity; home A.
9. Guard COORD1_UNBIND against motion in flight; reel comment says
   Buffered but uses BlendingHigh; remove unused `GroupReadPositionFb2`.

Items 2, 5 (MotionPose) and 9 can be done and verified on the sim;
1, 3, 4, 6 and 8 need the machine.
