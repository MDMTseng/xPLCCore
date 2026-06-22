# Coupling invariants

Implicit cross-layer constraints in the PLC + UI stack. Each entry pairs sites
that **must** change together — break either end and the other rots silently.
Add new entries here whenever you find one. Don't rely on people remembering.

Entries are ordered roughly by blast radius (most dangerous first).

---

## A-axis `/10` scaling
**Sites:**
- [`ProcessMotionPacket.st`](../../codesys_code/Application/APPs/AxisGroupSM/ProcessMotionPacket.st) — `MotionPose.c.A := MotionPoseTempReal / A_AXIS_KIN_WRAP_SCALE`
- [`AxisGroupSM.st`](../../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) VAR_INPUT — `A_AXIS_KIN_WRAP_SCALE : LREAL := 10` (the constant)
- `SM_Drive_GenericDSP402` axis scaling (CODESYS GUI) — has matching `A_AXIS_KIN_WRAP_SCALE`x to undo it

**Constraint:** Either both ends use `/10` and `*10`, or neither.

**Why it exists:** SpiderR's chained `Kin_CAxis` structurally wraps `c.A` to ±180°.
UI commands ±360°. The 10x scale dodges the wrap by sending ±36° through the kinematic
and reversing it at the axis. See [`memory/a_axis_div10_workaround.md`](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/a_axis_div10_workaround.md).

**Failure mode if broken:** A axis silently moves at 10x or 0.1x the commanded angle.

---

## Coordinate-system gate
**Sites:**
- [`UpdateAxisGroupState.st`](../../codesys_code/Application/APPs/AxisGroupSM/UpdateAxisGroupState.st) / `Update.st` — `GVL.CoordSystemConfigured := FALSE` on UnInited entry
- [`ProcessMotionPacket.st`](../../codesys_code/Application/APPs/AxisGroupSM/ProcessMotionPacket.st) — G1 NAKs with `coord_not_configured` if gate is FALSE
- UI flows (e.g. `OperationPage.tsx` `init_plc_motion`) — must call `cmd.SetCoord0()` or `cmd.SetCoord1()` after Ready, before any G1

**Constraint:** Every Ready entry that wasn't preceded by an explicit SetCoord call → first G1 NAKs. Every reset/Error→UnInited transition resets the gate.

**Failure mode if broken:** Cryptic `err='coord_not_configured'` on first motion after recovery, looking like the operator's UI is broken.

---

## E_RobotEvent ordinals
**Sites:**
- [`E_RobotEvent.st`](../../codesys_code/Application/Robot_FBs/E_RobotEvent.st) — PLC enum, positional UDINT
- [`lib/protocol.ts`](../../lib/protocol.ts) — `Event` const mirror (full set: NONE..ER)
- [`codesys_scripts/test_movement_sequence.py`](../../codesys_scripts/test_movement_sequence.py) — `EV_*` constants

**Constraint:** Three sites must agree. PLC is authoritative.

**Failure mode if broken:** Wrong event fired, FSM transitions silently to the wrong next state.

**History:** Until 2026-04-27 there were two duplicate local `PlcMotionEvent` enums in `OperationPage.tsx` and `MiscControlsPage.tsx`. Consolidated to import `Event` from `lib/protocol.ts`.

---

## A3 host-silence supervisor
**Sites:**
- [`AxisGroupSM.st:295-307`](../../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) — trips Ready→Error after `UI_HEARTBEAT_TIMEOUT_MS` of no PING
- [`GVL.st`](../../codesys_code/Application/GVL.st) — `UI_HEARTBEAT_TIMEOUT_MS : LINT := 5000`
- [`PluginHello.tsx`](../../PluginHello.tsx) — 1Hz `cmd.Ping()` keepalive
- ONLY `SYS/PING` packets stamp `GVL.LastUiPingMs` — `GA_EV` / `GET_MACHINE_STATE` / `G1` do NOT
- Scripted tests need their own PING heartbeat thread (see [`test_movement_sequence.py`](../../codesys_scripts/test_movement_sequence.py) `heartbeat_loop`)

**Constraint:** Anything that puts the FSM in Ready and runs longer than `UI_HEARTBEAT_TIMEOUT_MS` without sending a PING will be tripped to Error.

**Failure mode if broken:** Backgrounded UI window, slow harness call, or test script without keepalive → Ready→Error mid-operation, looks like a random fault.

---

## Reply-ring layout vs UI parser
**Sites:**
- PLC `MessagePackerFb` ack/event packets — keys like `ack`, `err`, `st`, `st_str`, `motion_buffer_size`, `axes_err_id`, etc.
- [`lib/protocol.ts`](../../lib/protocol.ts) — TS interfaces (`MachineState`, `G1Reply`, `GaEvReply`, …)
- UI consumers (`OperationPage.tsx`, `DiagPanel.tsx`, …)

**Constraint:** Adding a field on the PLC is harmless; renaming or removing one breaks every UI consumer silently (TS sees `unknown` and falls through).

**Failure mode if broken:** UI shows `undefined`, old value, or wrong number — typically only caught when an operator notices a wrong display.

---

## `axes_err_id` axis ordering
**Sites (post-2026-04-27 self-describing reply):**
- [`AxisGroupSM.st`](../../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) — packs `axes_err_id` AND `axes_labels` in the same iteration; both arrays must walk the axes in identical order
- [`GVL.st`](../../codesys_code/Application/GVL.st) — `LastAxesErrorIDs` array populated cyclically in that same order
- `axes_err_mask` bit positions 0..3 must match the array indices

**Constraint:** PLC is now the single source of truth — UI consumes
`axes_labels` from `GET_MACHINE_STATE` and indexes into `axes_err_id` /
`axes_err_mask` by the same i. Hardcoded label arrays in the UI are gone
(except for a tiny fallback used only before the first snapshot lands).

**Failure mode if broken:** PLC packs `axes_labels` and `axes_err_id` in
different orders — operator sees the wrong axis name on a fault. Lower
risk than before because they're packed in the same handler in
AxisGroupSM.st; a reorder requires editing two adjacent blocks.

---

## Virtual-motors gate TON quirk
**Sites:**
- `GVL.bVirtualMotorsMode_Request` — externally forced
- TON gate inside PLC — produces `bVirtualMotorsMode` only while `Q` is high
- [`virtual_motors_force.py`](../../codesys_scripts/jobs/templates/virtual_motors_force.py) / `virtual_motors_unforce.py`

**Constraint:** TON `Q` stays latched after the TTL expires. If you re-force `Request := TRUE` while `Q` is still latched from a previous run, no rising edge → TON doesn't re-arm → mode stays whatever it was.

**Recovery:** Cycle Request FALSE first (`virtual_motors_unforce.py`), then re-force TRUE. See [`memory/virtual_motors_gate_ton_reset.md`](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/virtual_motors_gate_ton_reset.md).

---

## TCP packet `type` field
**Sites:**
- All builders in [`lib/protocol.ts`](../../lib/protocol.ts) — `type:'M'` for motion, `type:'SYS'` for system
- PLC dispatcher branches on `type` first

**Constraint:** SYS commands (PING, GA_EV, GET_MACHINE_STATE, GET_DIAG, RESET_DBG_INFO) sent with `type:'M'` (or no type) hit the motion `minfo_buf` queue and NAK as `group_not_ready` — they look like motion bugs but are routing bugs.

**Failure mode if broken:** Comm tools that hand-craft packets without `type:'SYS'` see silent NAKs that look like FSM problems.

See [`memory/tcp_type_sys_required.md`](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/tcp_type_sys_required.md).

---

## `import_all` before `online_change`
**Sites:**
- `.st` files on disk (this repo's `codesys_code/`)
- The CODESYS project's in-memory model
- The running PLC

**Constraint:** `online_change` builds from the in-memory model, not from disk. Editing `.st` files alone does nothing — you must `import_all` first to push file changes into the project, then `online_change` to push the project to the PLC.

**Failure mode if broken:** "I changed the code but the PLC still does the old thing" — usually from forgetting `import_all`.

See [`memory/codesys_import_then_online_change.md`](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/codesys_import_then_online_change.md).

---

## Scratchpad intent movement_id timing
**Sites:**
- [`orchestrator/resume.ts`](../../orchestrator/resume.ts) `writeIntent()` — host stamps `intent_movement_id` after an action is acked
- [`orchestrator/resume.ts`](../../orchestrator/resume.ts) `reconcileOnResume()` — compares `scratchpad.intent_movement_id` vs `machineState.last_completed_movement_id` (NOT `machineState.movement_id`)
- [`AxisGroupSM.st`](../../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) GET_MACHINE_STATE packer — `last_completed_movement_id` from `PrevCompletedMovementId` (latched at MOVE_DONE), `movement_id` from `GroupReadPositionFb.MovementId` (bumps when a move starts executing)

**Constraint:** Three pieces have to agree on which id is being compared:
1. Host writes `intent_movement_id` = the movement_id echoed in the action's ack reply (e.g. `G1Reply.movement_id`). MUST happen AFTER the ack, NEVER BEFORE.
2. PLC tracks `last_completed_movement_id` separately from `movement_id`. The completed one bumps only at MOVE_DONE; the current one bumps when execution starts.
3. Resume reconcile uses `last_completed_movement_id` for the `>= intent_movement_id` comparison.

**Failure mode if broken:** State-2 (blow-off) detection becomes a constant TRUE (intent always looks completed), so the renderer skips the safety blow-off after every resume — duplicate reel advance, skipped carrier slot, **scrap**. This is the exact failure mode `decisions §4 (2)` exists to prevent.

**History:** `last_completed_movement_id` was added 2026-06-22 after [`implementation_review §2`](../../doc_review/implementation_review_2026-06-22.md) caught the comparison aliased to `movement_id` (which on virtual axes snaps to target instantly and would have masked all unfinished-move scenarios).

---

## Action: keeping this doc honest

When you add or change a coupling, **add the entry here in the same commit**. The
moment this file lags reality, it becomes worse than no doc at all — people will
trust an outdated invariant and get bitten.

If an invariant gets refactored away (e.g. a magic number becomes a single
constant), delete the entry rather than leaving a stale "no longer applies"
note.
