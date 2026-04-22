# PLC source (CODESYS)

Structured-Text source exported from `PackerX.project` via
`codesys_scripts/export_all.py`. Edit here, import back with
`codesys_scripts/tcp_client.py import_all`. See
[`../codesys_scripts/README.md`](../codesys_scripts/README.md) for the
round-trip flow, encoding rules, and known tooling gotchas.

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
│   ├── AxisGroupSM.st          *Large* command dispatcher + motion driver.
│   ├── EthercatPOU.st          EtherCAT bus management.
│   ├── POU_BUFFER_RUN.st       Buffered-motion execution loop.
│   ├── TCP_MSGPAK_Server.st    Active MessagePack command server.
│   ├── TCP_Server.st           Secondary TCP server on :8123 (motion path).
│   └── WebServer_SIMPLE.st     Minimal HTTP responder.
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
| `G1` | Linear move | axis targets, F (velocity) |
| `G4` | Dwell | duration |
| `ReelGo` | Reel-pull incremental move | `Distance`, `F`, `ACC`, `DEA`, `JERK` |
| `SetCoord0` | Zero current coord system | — |
| `M` | Misc (mode switch?) | — |
| `GA_EV` | Fly event (trigger on axis position) | stage-indexed pins |

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
  `space() >= 0` is always true. Use `> 0`. (See P1 finding in
  `TCP_Server.st`.)
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

### P3 — Readability / architecture

| # | Target | Action | Status |
|---|---|---|---|
| 12 | `APPs/AxisGroupSM.st` | Magic numbers → named constants. | **Partially fixed 2026-04-22** — added `MOTION_BUFFER_THRESHOLD`, `RETRY_COOLDOWN_G4_MS`, `RETRY_COOLDOWN_G1_MS`, `TRIGGER_TIMEOUT_ERR`, `G1_A_UNSET_SENTINEL`. (`FlyEventStageLimit` was already named.) Remaining: in-function literals like `'event_id'` keys and `16#FF` masks — acceptable as-is. |
| 13 | `APPs/AxisGroupSM.st` | Command strings → `E_CommandType` enum + `CASE` dispatch. | **Open** — invasive refactor; defer until the command set stabilizes. |
| 14 | Project-wide | Pick one naming convention. | **Open** — project-wide rename; do as part of #15 if ever. |
| 15 | `APPs/AxisGroupSM.st` | Split into `FB_MotionDispatcher`, `FB_FlyEventManager`, `FB_ReelMotorDriver`, `FB_IoTracker`. | **Open** — major surgery; only once behavior is fully understood. |
| 16 | `Robot_FBs/FB_Homing.st` | Parameterize axis refs via `FB_init` instead of hardcoding `IoConfig_Globals.EAxis0/1/2`. | **Open** — safety-critical; needs user coordination before touching homing. |
| 17 | `APPs/TCP_Server.st` | Replace blocking `WHILE` loop with non-blocking handshake. | **Open** — behavior change; needs client-side coordination. |

### Recently completed

| Date | Change | Files |
|---|---|---|
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
