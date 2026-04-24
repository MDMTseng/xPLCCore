# PLC source (CODESYS)

Structured-Text source lives in [`../codesys_code/`](../codesys_code/),
exported from `PackerX.project` via `codesys_scripts/export_all.py`.
Edit `.st` files in [`../codesys_code/`](../codesys_code/), import
back with `codesys_scripts/tcp_client.py import_all`. See
[`scripting.md`](./scripting.md) for the round-trip flow, encoding
rules, and known tooling gotchas.

**Target:** CODESYS 3.5 SP21 Patch 20, SoftMotion, CiA 402 servos
(SM_Drive_GenericDSP402, CL3-E57H reel-pull motor).

**This doc tracks the structure, the open review findings, and progress
as we chip away at them.** Update alongside code changes.

---

## Directory map

```
Application/
├── PLC_PRG.st                  Main program entry (cyclic).
├── GVL.st                      Global variables, shared buffers.
├── APPs/                       Application-level POUs (cyclic drivers).
│   ├── AxisGroupSM.st          *Large* command dispatcher + motion driver
│   │                           (wired to EtherCAT_Task).
│   ├── TCP_MSGPAK_Server.st    MessagePack command server on :8125
│   │                           (wired to Comm task).
│   └── WebServer_SIMPLE.st     Minimal HTTP responder (not task-wired;
│                               kept as reference).
├── APP_COMM_FBs/               App-level comm DUTs + helper POUs.
│   ├── FlyEvent*.st            Fly-event data / enums.
│   ├── MsgPakInfo.st           Message descriptor struct.
│   └── MsgPakInfo{Init,Clear,Wrapup}.st
├── COMM_FBs/                   Reusable comm building blocks.
│   ├── FB_MpPacker/            MessagePack encoder (PackREAL, PackString, …).
│   ├── FB_MpUnpacker/          MessagePack decoder (TryReadREAL, TryReadINT64, …).
│   ├── FB_MsgPakLevelParser/   Level/nesting tracker while parsing.
│   ├── FB_DataBuffer/          Byte-buffer abstraction.
│   ├── FB_RingBufferIndex/     Head/tail index ring (capacity, push, consume).
│   ├── FB_TcpMsgPakServer/     TCP server that emits/consumes MessagePack.
│   ├── E_*.st                  Enums (parser state, mp types, errors).
│   ├── F_*.st                  Free functions (byte→hex, mp type→string).
│   ├── Swap{16,32,64}To.st     Endian swap helpers.
│   └── T_NestingContext.st     Nesting stack frame struct.
└── Robot_FBs/
    ├── AxisGroupManager/       Active axis-group state machine.
    │   ├── AxisGroupManager.st Declaration + body (cyclic Update call).
    │   ├── FB_init.st          Takes axReel : REFERENCE TO AXIS_REF_SM3.
    │   ├── Update.st           Per-scan state machine step.
    │   └── Transition.st       State transition guard.
    ├── FB_Homing.st            Homing sequence (hardcoded axis refs — P3).
    ├── E_RobotEvent.st         Event enum.
    └── E_RobotState.st         State enum.
```

## Machine sequence & parallelism constraint

This is a **delta-robot tape-and-reel packer with inline 4-surface +
side-projection inspection**. Per part:

1. **FlexBowl feeder** vibrates parts onto a plate. **Feeder cam**
   (top-down) locates candidates (x, y, angle, surround/center clear
   flags).
2. **Robot** picks first valid candidate with a suction nozzle.
3. **Side cam** — shot 1: pose + facing detection.
4. **Bottom cam** — underside defect + precise object pose (for angle
   correction).
5. Robot rotates part by the angle correction; **side cam** — shot 2:
   rectified dimension measurement.
6. **Reel top cam** inspects slots ahead of the current placement
   position. Two lighting configurations (`CAM_Top_SideLight` then
   `CAM_Top_Light0`, ~80 ms apart) — one for "is the slot empty,"
   one for "is an already-placed part correct." Returns
   `is_clear[]` / `is_OK[]` arrays for multi-slot look-ahead.
7. NG → toss to one of three bins (object-NG / feeder-return /
   cover-NG). OK → place into first clear slot.
8. **Reel advances** by the number of slots placed this cycle.

### The parallelism constraint (central design driver)

**The robot arm is the only serialized resource and physically occludes
every camera it shares a workspace with.** Throughput = robot cycle time
*iff* every auxiliary action (FlexBowl shake, feeder cam shot, reel top
cam two-shot, reel advance) completes inside the window where the robot
is moving away from that station. If any auxiliary action extends past
its window, it directly costs packs/hour.

Concretely:

- **Feeder cam shot** is fired while the robot is carrying the current
  part to the inspection station.
- **Reel top cam two-shot** is fired while the robot is moving from
  place-into-slot back toward the feeder, immediately after a reel
  advance. The 80 ms gap between the two shots must also fit inside
  that window.
- **FlexBowl shake** runs while the robot is doing side/bottom
  inspection.
- **Reel advance** is a pin-op sequence (M4) fired at a motion
  progress fraction so the reel servo starts moving before the robot
  fully clears.

### Mechanisms the code uses to achieve this

- **`WAIT_FOR_TRIGGER_MOTION_PROGRESS`** (PLC command) — renderer
  awaits a fractional progress into the current move (e.g. 0.01, 0.9,
  1.0). That's how it knows "the arm has cleared the slot; fire the
  top cam now."
- **`M4` fly events with `motion_progress`** — PLC-side trigger: pin
  pulse at X% of the current move. Zero renderer-side latency.
- **Promise-stashing in the renderer** — start a camera trigger +
  response wait, return the `Promise`, issue the next robot move, then
  `await` only when the data is actually needed. This is not a
  code-smell; it's the pipelining.
- **`pin_op_seq`** — a single `M4` can carry a whole pulse schedule
  (e.g. reel advance + top cam shot 1 + top cam shot 2), executed
  deterministically on the PLC without renderer round-trips.

**Implication for refactoring:** any cleanup of
[`../components/CalibPage.tsx`](../components/CalibPage.tsx) that
inadvertently serializes an auxiliary action behind a robot move
**costs throughput.** Cycle-time measurement before/after is the
acceptance test for every refactor step, not just "does it still
run." See [`calibpage.md`](./calibpage.md).

### Timing diagram (one steady-state cycle)

Derived from the call order in `runAllObjects`. Time goes
left-to-right; not to scale — the point is ordering and overlap, not
absolute ms.

```
Cycle N               t0    t1    t2    t3    t4    t5    t6    t7    t8    t9
─────────────────────────────────────────────────────────────────────────────────
ROBOT (serialized,    │═════════│═════│═════│═════│═════│═════│═════│═════│═════
blocks cameras)       │ to_ff   │pick │ascnd│to_  │sd1  │rotate sd2  │to_  │place+
                      │(long)   │desc │     │insp │desc │         to_slot descend
                      │         │     │     │     │     │             │    rise

REEL ADVANCE          │ pulses  │
(M4 pin_op_seq,       │ ═══     │
motion_id_offset:-1)  │         │
                      │         │
REEL TOP CAM 2-shot   │   shot1 │              (80 ms gap, both fire while
(same pin_op_seq)     │   ══    │               robot is traversing to feeder)
                      │     shot2
                      │     ══  │

FEEDER CAM            ⬇ await previous cycle's feeder promise resolves (~t0-t1)

FLEXBOWL SHAKE        │         │     │     │     │     │ ═════════│
(runs during robot    │         │     │     │     │     │ shake    │
inspection/place;     │         │     │     │     │     │          │ ═══
awaited at next t0)   │         │     │     │     │     │          │feeder cam

SIDE CAM shot 1       │         │     │     │     │ ═   │     data awaited ~t5
(motion_progress:1    │         │     │     │     │trig │
at end of insp        │         │     │     │     │     │
descent)

BOTTOM CAM            │         │     │     │     │  ══ │     data awaited ~t5
(M4 during G4 dwell)  │         │     │     │     │ trig│

SIDE CAM shot 2       │         │     │     │     │     │     ═    data await
(rectified, fires     │         │     │     │     │     │    trig  ~t7
at motion_progress:1
of rotate)

TOP CAM DATA          ⬇ slot data from reel-top-cam two-shot awaited ~t7-t8
(started at t0-t1,    (the whole cycle ran in parallel with this wait)
awaited right before
place decision)
```

**What it reveals:**

1. **The robot lane is fully saturated.** Every t-block is a robot
   motion. That's the target.
2. **Reel advance + top cam 2-shot fire at t0-t1** during the long
   traverse from place-slot back to feeder pick. Arm is carrying
   itself away from the slot area; cam view is clear. Tightest
   pack: pulses + two shots + 80 ms lighting gap inside one robot
   move.
3. **Feeder cam shot is pipelined across cycles.** Shake+shot runs
   late in cycle N (during inspection+place, arm nowhere near the
   plate); result is consumed at the start of cycle N+1. That's
   what `feederCheckPromise` is.
4. **Side and bottom cam shots fire at `motion_progress:1`** — PLC
   fires the `M4` pulse at the end of a motion, exactly when the
   arm is stationary at the inspection station. Zero renderer
   round-trip.
5. **Every data await happens later than its trigger** — several
   robot moves later in most cases. That's the pipelining. Moving
   the await next to the trigger balloons cycle time.

**Refactor-safety corollary:** any renderer step function that
triggers a camera must *return the Promise* and let the caller
`await` it later. See [`calibpage.md`](./calibpage.md) §Refactor
rules for the renderer-side pattern + examples.

## High-level data flow

```
┌──────────────────┐      ┌─────────────────┐     ┌──────────────────────┐
│  TCP client      │─TCP─►│ FB_TcpMsgPakSrv │────►│ FB_MpUnpacker /      │
│  (JS / Python)   │      │ (+ LevelParser) │     │ FB_MsgPakLevelParser │
└──────────────────┘      └─────────────────┘     └─────────┬────────────┘
                                                            │ decoded cmd
                                                            ▼
                          ┌──────────────────────────────────────┐
                          │ GVL.minfo_buf_ridx  (ring buffer of  │
                          │   decoded MsgPakInfo command slots)  │
                          └────────────────┬─────────────────────┘
                                           │ consumed each scan
                                           ▼
                          ┌──────────────────────────────────────┐
                          │ AxisGroupSM  (command dispatch +     │
                          │   cyclic motion driver):             │
                          │   'G1','G4','ReelGo','SetCoord0',    │
                          │   'M','GA_EV', fly events, I/O pins  │
                          └──────┬──────────────────┬────────────┘
                                 │                  │
                 ┌───────────────▼──┐     ┌─────────▼─────────┐
                 │ MC_* motion FBs  │     │ AxisGroupManager  │
                 │ (MoveRelative,   │     │ (FB-level state   │
                 │  Power, Reset,   │     │  machine, homing  │
                 │  GroupReadStat)  │     │  coordination)    │
                 └──────────────────┘     └───────────────────┘
                                 │
                                 ▼ response MsgPack out
                          GVL.reMP_info_ridx ──► TCP back to client
```

Reel-pull motor uses **two MC_MoveRelative instances**
(`reelMoveRelative`, `reelMoveRelative2`) with `BufferMode := Buffered`,
dispatched to whichever is idle. This is the PLCopen-approved way to
queue back-to-back moves; a single FB rejects the second move with
`SMC_MORE_THAN_ONE_MOVEMENT_PER_INSTANCE`.

## Command protocol (MessagePack map)

Each client request is a map with at least `{"type": ..., "cmd": ...}`.
Known command strings (decoded by `AxisGroupSM`):

| `cmd` | Purpose | Notable params |
|---|---|---|
| `G1` | Linear move | axis targets (`X`/`Y`/`Z`/`A`/`B`/`C`), `F` velocity; `A` uses `G1_A_UNSET_SENTINEL` to detect "not supplied" |
| `G4` | Dwell | `P` (duration, sec) |
| `ReelGo` | Reel-pull incremental move | `Distance`, `F`, `ACC`, `DEA`, `JERK`; dispatched to whichever `reelMoveRelative*` FB is idle |
| `SetCoord0` | Zero current coord transform | — |
| `SetCoord1` | Preset coord transform (A := 60°) | — |
| `M4` | Schedule fly event (pin operations at a future motion id) | `motion_id`, `pin_op_seq`; rejected when `FlyEventAvailableCount <= 3` |
| `GA_EV` | Fly event (trigger on axis position) | stage-indexed pins |
| `BLOCK_FOR_MOTION_STOP` | Wait until motion buffer drains | — |
| `BLOCK_FOR_DIGITAL_INPUT` | Wait for a digital input bit | pin index; null-guarded when `HECAT_1616` unwired |
| `GET_DIGITAL_INPUT` | Read current digital input word | returns 0 when `HECAT_1616` unwired |
| `READ_LATEST_CMD_LOCATION` | Report last commanded Cartesian pose | — |
| `WAIT_FOR_TRIGGER_MOTION_PROGRESS` | Wait for a scheduled trigger to fire | uses `TRIGGER_TIMEOUT_ERR` |
| `RESET_DBG_INFO` | Reset `IoTrigger*` / `IoCommandCount` counters | — |
| `getDigitalInputFlipCount` | Report accumulated pin-flip counts | — |

**⚠ These are magic strings today — planned to become `E_CommandType`
(see P3 / §Pending improvements).**

## File-size landmarks

| File | Approx size | Notes |
|---|---:|---|
| `APPs/AxisGroupSM.st` | ~1100 lines | Biggest POU, mixed concerns — split candidate |
| `COMM_FBs/FB_MpUnpacker/*` | ~12 methods | Clean separation |
| `COMM_FBs/FB_MpPacker/*` | ~9 methods | Clean separation |
| `Robot_FBs/AxisGroupManager/*` | 4 files | Clean |
| `Robot_FBs/AxisGroupManager_BB/*` | 5 files | Dead — delete |

## Known invariants and gotchas

- **SoftMotion: one movement per FB instance.** Violating this throws
  `SMC_MORE_THAN_ONE_MOVEMENT_PER_INSTANCE` and kills the move (red
  triangle on the axis). Handled today via the two-FB dispatch in
  `AxisGroupSM` cyclic driver. If you add another moving element,
  repeat the pattern.
- **Jerk = 0 with jerk-limited profile** throws
  `SMC_MR_INVALID_VELACC_VALUES`. Always pass a non-zero `Jerk` (reel
  uses 10000 from the client).
- **MC_MoveRelative must be called every scan**, not once inside a
  command branch. The pattern is: command handler sets a
  `reelGoRequest` flag; cyclic driver calls the FB every scan and
  clears the flag after one scan (rising-edge trigger). If you only
  call the FB once, the move starts but state updates stall and the
  drive can fault silently.
- **`reelGoRequest`/`reelGoRequest2` are one-shot by design** — cleared
  every scan unconditionally so each new `ReelGo` command produces a
  fresh rising edge. (P1 review: add an explicit comment to prevent
  "fix" regressions.)
- **Ring-buffer index FBs (`FB_RingBufferIndex`) use UDINT.**
  `space() >= 0` is always true. Use `> 0`.
- **Response-packet writes must check buffer space first.** Calling
  `getHead()` on a full buffer returns an invalid slot; the subsequent
  pointer-deref write corrupts adjacent memory. (P2 finding in
  `AxisGroupSM`.)
- **DUTs and GVLs have no IMPL_MARKER** (decl-only); don't add one or
  import will split wrongly.

---

## Code review — status

Initial review completed **2026-04-22**. Issues are tracked by
priority. Update the **Status** column as we fix things.

### P0 — Safety / correctness

| # | File | Issue | Status |
|---|---|---|---|
| 1 | `APPs/AxisGroupSM.st` | `DigitalInputPointer` used unguarded in `BLOCK_FOR_DIGITAL_INPUT` and `GET_DIGITAL_INPUT` handlers; null deref crashes PLC if `HECAT_1616.InputData` isn't wired (currently commented out at line 371). | **Fixed 2026-04-22** — added `<> 0` guard on both handlers; `GET_DIGITAL_INPUT` returns 0 when unwired. |
| 2 | `APPs/AxisGroupSM.st` | Missing `.Error` checks on `MC_GroupReadStatus` / `MC_GroupReadActualPosition`; garbage propagates into progress tracking. | **Fixed 2026-04-22** — `UpdateMotionProgress` now freezes `MotionBufferSize := 0` when either FB reports `.Error`; also guards against `LastAcceptedMovementId - MovementId` underflow. |

### P1 — Bugs

| # | File | Issue | Status |
|---|---|---|---|
| 3 | `APPs/TCP_Server.st` | `space() >= 0` tautology on `UDINT` — should be `> 0`. | **Fixed 2026-04-22** |
| 4 | `APPs/AxisGroupSM.st` | Reel request flag one-shot is correct but should be documented. | **Already documented** at lines 1094–1095; closing. |
| 5 | `APPs/AxisGroupSM.st` | `DINT_TO_ULINT` on unpacked pin mask wraps negative values silently (bit-pattern corruption → unintended outputs). | **Fixed 2026-04-22** — clamp negatives to 0 before conversion, on both pin-mask and pin-state fields. |
| 6 | `APPs/AxisGroupSM.st` | FlyEvent buffer-full has no NAK. | **Not a bug** — generic `ack=FALSE` path at line 1051 covers it. Closing. |

### P2 — Dead code / duplication

| # | Target | Action | Status |
|---|---|---|---|
| 7 | `Robot_FBs/AxisGroupManager_BB/*` | Delete entire folder. | **Fixed 2026-04-22** via `delete_dead_pous.py` template. |
| 8 | `APPs/TCP_Server_Mult.st` | Delete echo-only prototype. | **Fixed 2026-04-22** via `delete_dead_pous.py`. |
| 9 | `APPs/AxisGroupSM.st` | Remove `IF FALSE THEN ... END_IF` blocks and commented-out VAR lines. | **Won't fix** — line 177 says *"IMPORTANT: following if TRUE... and if FALSE.... are mandatory, DO not try to remove/optimize it"*. |
| 10 | `APPs/AxisGroupSM.st` | Resolve AUX-thread `TODO`s (implement or delete). | **Clarified 2026-04-22** — replaced `TODO` with an explicit comment that the buffers are intentionally drained until the AUX feature is wired. Still tracked here as the real feature work. |
| 11 | `Robot_FBs/performanceTestPOU.st` | Move to debug folder or delete. | **Fixed 2026-04-22** — deleted. |

### A — Architecture / robustness

Gaps between the host-authority design intent (see
[`solidification.md`](./solidification.md) §Guiding principles) and
today's code.

| # | Area | Issue | Status |
|---|---|---|---|
| A1 | `APPs/AxisGroupSM.st` | **Verify PLC rejects motion commands while in `E_RobotState.Error`.** Load-bearing assumption for the host-can-crash model. If the dispatcher accepts commands in Error state, the invariant is broken. | **Open** — needs code verification. |
| A2 | `APPs/AxisGroupSM.st` | **`BLOCK_FOR_MOTION_STOP` / `BLOCK_FOR_DIGITAL_INPUT` have no timeout.** If the host dies mid-block, the PLC parks forever. Add an optional timeout param; on timeout, ack-failure and clear. | **Open** |
| A3 | `APPs/AxisGroupSM.st` + host | **No host↔PLC heartbeat.** PLC has no way to detect a dead host; host has no way to detect a silent PLC. On missing heartbeat (host→PLC), PLC should halt motion, drop pending fly events, enter recoverable idle. | **Open** — cross-cutting (ST + TS). |
| A4 | `APPs/AxisGroupSM.st` + host | **No reconnect handshake.** After host crash, new renderer needs to read PLC state (homed? last `movement_id`? pending triggers?) and reconcile before resuming. Today it's "reload and pray." | **Open** — cross-cutting. |
| A5 | `Robot_FBs/AxisGroupManager/*` | **Low-material handled renderer-side via polling loop** ([watchdog IIFE at CalibPage.tsx:809](../components/CalibPage.tsx#L809)). PLC should watch the `ReelLacking` bit and autonomously enter a `WaitingForMaterial` state that pauses motion at a safe point. Renderer becomes the feed-issuer, not the watchdog. Add a `FEED` command that's only accepted in `WaitingForMaterial`. | **Open** — mechanical deadline is relaxed (seconds), so not urgent, but belongs in PLC per host-authority model. |
| A6 | Project-wide | **No vision↔PLC contract documented.** Today the renderer is the bridge between vision results and PLC commands. Long-term, vision should drive fly-event timing and bin selection directly or via a documented protocol. Blocking item before splitting `AxisGroupSM` (P3 #15). | **Open** — design decision pending. |

**Sequencing note:** these items are grouped into workstreams in
[`solidification.md`](./solidification.md) (W1: A1–A4; W2: A5;
W7: A6). Don't tackle in ID order — follow the workstream phasing.

### P3 — Readability / architecture

| # | Target | Action | Status |
|---|---|---|---|
| 12 | `APPs/AxisGroupSM.st` | Magic numbers → named constants. | **Partially fixed 2026-04-22** — added `MOTION_BUFFER_THRESHOLD`, `RETRY_COOLDOWN_G4_MS`, `RETRY_COOLDOWN_G1_MS`, `TRIGGER_TIMEOUT_ERR`, `G1_A_UNSET_SENTINEL`. (`FlyEventStageLimit` was already named.) Remaining: in-function literals like `'event_id'` keys and `16#FF` masks — acceptable as-is. |
| 13 | `APPs/AxisGroupSM.st` | Command strings → `E_CommandType` enum + `CASE` dispatch. | **Open** — invasive. ST `CASE` needs ordinals, so refactor would add a string→enum decoder that still does every `strcmp`, just relocated. Dispatcher becomes cleaner but no correctness/perf win. Defer until command set stabilizes or until #15 split happens. |
| 14 | Project-wide | Pick one naming convention. | **Open** — project-wide rename; do as part of #15 if ever. |
| 15 | `APPs/AxisGroupSM.st` | Split into `FB_MotionDispatcher`, `FB_FlyEventManager`, `FB_ReelMotorDriver`, `FB_IoTracker`. | **Open** — major surgery; only once behavior is fully understood. |
| 16 | `Robot_FBs/FB_Homing.st` | Parameterize axis refs via `FB_init` instead of hardcoding `IoConfig_Globals.EAxis0/1/2`. | **Open** — safety-critical; needs user coordination before touching homing. |
| 17 | `APPs/TCP_Server.st` | Replace blocking `WHILE` loop with non-blocking handshake. | **Closed 2026-04-24** — POU deleted as dead code (see ringbuf-bugs.md #5/#6). |

### Recently completed

| Date | Change | Files |
|---|---|---|
| 2026-04-24 | **Ring-buffer audit bundles (ringbuf-bugs.md #1,2,4,5,6,7,8).** Length guard on minfo + aux ring producers (drops overlen packets and bumps `GVL.OverlenDropCount` instead of wrapping via `DINT_TO_BYTE`). reMP_info_ridx overflow guard at all `getHead()` call-sites in `AxisGroupSM` (consume-oldest + `ReMpDropCount`). Deleted unused POUs `TCP_Server`, `EthercatPOU`, `POU_BUFFER_RUN` (probed task wiring — only `AxisGroupSM` and `TCP_MSGPAK_Server` are wired). AUX ack now routed through reMP ring so failed `Send()` is retried instead of silently lost. Send-stall drop counter (`SendStallDropCount`) caps retries at `SEND_MAX_RETRIES` when socket reports `xActive=TRUE` but `Send()` keeps returning FALSE. Build warnings dropped 49 → clean. | `APPs/TCP_MSGPAK_Server.st`, `APPs/AxisGroupSM.st`, `GVL.st`; deletions under `APPs/` |
| 2026-04-22 | **Doc — command protocol table synced with code.** Added `SetCoord1`, `M4`, `BLOCK_FOR_MOTION_STOP`, `BLOCK_FOR_DIGITAL_INPUT`, `GET_DIGITAL_INPUT`, `READ_LATEST_CMD_LOCATION`, `WAIT_FOR_TRIGGER_MOTION_PROGRESS`, `RESET_DBG_INFO`, `getDigitalInputFlipCount`. | `codesys_code/README.md` |
| 2026-04-22 | **P0 #2 — freeze progress on MC FB error.** `UpdateMotionProgress` now sets `MotionBufferSize := 0` if `GroupReadStatusFb.Error` or `GroupReadPositionFb.Error`; downstream `ProcessMotionPacket` then won't accept new moves while errored. Also clamped `LastAccepted - MovementId` underflow. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **P2 #10 — clarified AUX command handlers.** Removed `TODO` noise; added explicit comment that buffers are intentionally drained until the AUX feature is implemented. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **P3 #12 — magic numbers → named constants.** `MOTION_BUFFER_THRESHOLD`, `RETRY_COOLDOWN_G4_MS`, `RETRY_COOLDOWN_G1_MS`, `TRIGGER_TIMEOUT_ERR`, `G1_A_UNSET_SENTINEL`. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **P2 #7/8/11 — deleted dead POUs.** `AxisGroupManager_BB` (5 files, stale backup), `TCP_Server_Mult.st` (echo prototype on same port as `TCP_Server`), `performanceTestPOU.st` (scratch). Build still clean. | project-wide |
| 2026-04-22 | **P0 #1 — null guard on `DigitalInputPointer` in `BLOCK_FOR_DIGITAL_INPUT` and `GET_DIGITAL_INPUT` handlers.** Prevents PLC crash when the HECAT_1616 device isn't present. `GET_DIGITAL_INPUT` now returns 0 in that case. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **P1 #3 — fix `space() >= 0` UDINT tautology in `TCP_Server`.** Was always true, letting packets drop into a full ring buffer. Changed to `> 0`. | `APPs/TCP_Server.st` |
| 2026-04-22 | **P1 #5 — clamp negative pin mask / pin state values to 0** before `DINT_TO_ULINT` in M4 `pin_op_seq` parser. Prevents wraparound into huge ULINT values that would trigger unintended outputs. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **Two-FB dispatch for back-to-back reel moves.** Added `reelMoveRelative2` + dispatcher selecting whichever FB is idle; `BufferMode := Buffered`. Fixes `SMC_MORE_THAN_ONE_MOVEMENT_PER_INSTANCE`. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **Reel motion cyclic driver.** Moved `MC_MoveRelative` call out of command branch into cyclic loop with `reelGoRequest` flag pattern. Fixes "motor dies silently mid-move." | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **ReelGo parameter plumbing.** Added `TryReadREAL` for `F`, `ACC`, `DEA`, `JERK`, `Distance` from client message. Fixes `SMC_MR_INVALID_VELACC_VALUES`. | `APPs/AxisGroupSM.st` |
| 2026-04-22 | **Parameterize reel-pull motor.** `AxisGroupManager` now takes `axReel : REFERENCE TO AXIS_REF_SM3` via `FB_init`; replaces global `reelpullmotor` reference. | `Robot_FBs/AxisGroupManager/*` |

---

## How to work on this tree

1. Edit `.st` files in place.
2. `python ../codesys_scripts/tcp_client.py import_all` — pushes changes
   into the open project and runs `generate_code()`. Check for 0
   errors in the output.
3. Review in the IDE, **File → Save** to persist.
4. Update the **Status** column in this README for any issue closed or
   added.
5. Move completed items from the priority tables into
   **Recently completed** with a date and one-line summary.

For new issues discovered during work, append a row in the appropriate
priority table rather than filing a ticket elsewhere — this file is the
single source of truth for PLC code progress.
