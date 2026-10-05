# UI ↔ PLC wire protocol

Authoritative reference for the MessagePack TCP protocol spoken between
the renderer (`PluginHello.tsx` → `sendTcpMsgPack`) and the PLC
dispatcher (`AxisGroupSM.st`). When this file and the code disagree,
the code wins and this file is wrong — fix it.

Cross-refs:
- PLC handlers: [`plc.md`](../3-subsystems/plc.md) §"Command protocol".
- Architecture (transport, ring buffers, push events):
  [`architecture.md`](../1-concepts/architecture.md).
- Typed builders: [`../../lib/protocol.ts`](../../lib/protocol.ts) — every
  outbound packet should go through one of these; raw object literals
  in component code are migration debt.

---

## Transport

- **TCP** to PLC `:8125`, no length framing — one MessagePack value per
  send (the PLC level-parser frames on map nesting).
- **No TLS, no auth.** Trusted LAN only. Any client on the LAN that takes
  the single slot can drive the machine (2026-10-05 integration review,
  safety P0; doc_review/plc_ui_integration_review_2026-10-05.md).
- **Single connection.** `udiMaxConnections := 1` on the PLC; second
  client gets connection refused until idle-watchdog reclaims (~7 s).
- **Connection loss** (corrected 2026-10-05): the PLC does NOT go to
  Error on silence. With motion queued it only counts the silent spell
  (`UiHeartbeatStaleCount`, `ui_hb_stale_count`); the socket's idle
  watchdog (`HostIdleTimeoutMs`, 5 s) resets the link, the disconnect
  cleanup drops unprocessed packets, waits and the TAPE sequence, and
  motion already accepted (at most 12 moves) runs to its end. The renderer
  does NOT reconnect by itself: the operator clicks Disconnect / Connect;
  on connect it fetches `GET_MACHINE_STATE`.

## Envelope

Every packet is a top-level **map** (msgpack `fixmap` / `map 16`). The
required keys depend on direction.

### Outbound (UI → PLC)

| Key | Type | Required | Notes |
|---|---|---|---|
| `type` | string | **yes** | One of `"M"` (motion / IO / dispatch) or `"SYS"` (heartbeat / diagnostics). Missing or unrecognised → NAK `err='missing_type_field'`. |
| `cmd` | string | **yes** | Command name. See §"Commands" below. |
| `id` | int | recommended | Echoed back in the ack/NAK so the renderer can correlate replies to awaited promises. Auto-stamped by `sendTcpMsgPack`. |
| `protocol_version` | uint | recommended | Currently `1`. Auto-stamped by `sendTcpMsgPack`. PLC NAKs `err='protocol_version_mismatch'` if present and not `1`. Absent (legacy) is allowed. |
| _command-specific…_ | — | — | See per-command sections. |

### Inbound (PLC → UI)

Three shapes:

1. **Reply** (ack or NAK to a previous outbound `id`):
   ```
   { ...payload, id, ack, seq }            // ack=true: payload is per-command
   { err, ...payload, id, ack:false, seq } // ack=false: err is short string
   ```
   Renderer routes by `id` to the awaiting promise. The tail is `id`,
   `ack`, `seq` only; `runtime_ms` is in a reply only where the command
   packs it (PING, GET_MACHINE_STATE, GET_DIAG...). `seq` is not usable
   for gap detection yet: it is incremented from two tasks and the
   diagnostic replies bypass the reply ring (review 2026-10-05, P1).

2. **Server-push event** (unsolicited, no `id`):
   ```
   { kind:'event', name, runtime_ms, ...payload }
   ```
   `name` is one of `ST_CHG`, `COORD_SET`, `MOVE_DONE`, `DI`,
   `TRIGGER_ERR`, `COORD1_ERROR`, `HEARTBEAT` (see §"Push events"). Renderer dispatches a `window` `CustomEvent('plc:event')`.

3. **Reconnect snapshot** (from `SYS/GET_MACHINE_STATE`, technically a
   reply but treated specially): cached in `lastMachineSnapshotRef` and
   re-broadcast as `CustomEvent('plc:machine-state')`.

### Error strings

`err` field on a NAK is one of (open set; grep `'err':=` in
`AxisGroupSM.st` for the live list):

| `err` | When |
|---|---|
| `group_not_ready` | Motion command sent while FSM ≠ Ready. |
| `coord_not_configured` | `G1` while no coordinate system is set since the last UnInited / Error entry. The PLC applies the machine frame (SetCoord1's) itself on Ready entry, so this means that apply failed or a `SetCoord` was refused. |
| `flushed` | A wait, a pending trigger wait or a TAPE_CYCLE ended by SYS `FLUSH` (the host's run ended). |
| `coord_mismatch` | `G1` while the group's actual MCS transform (read back every scan) differs from the one set and none is being applied. A G1 sent while the frame is being applied waits for it instead. |
| `block_timeout` | A `BLOCK_FOR_*` / `WAIT_FOR_*` wait exceeded its `timeout_ms`. |
| `wait_busy` | A wait of the same kind (motion / reel / input) is already pending. |
| `missing_type_field` | Outbound packet without `type` (or unrecognised). |
| `protocol_version_mismatch` | Outbound `protocol_version` ≠ PLC's. Reply also includes `err_got`. |
| `group_error_stop` / `group_read_status_error` | MC group FB reported error before move accept. |
| `unknown_cmd` / `unknown SYS cmd` | The command is not in this PLC program: `unknown_cmd` on the motion path (ProcessMotionPacket), `unknown SYS cmd` on the SYS / diagnostic paths. The renderer's "older PLC program" fallback matches exactly these two (CalibPage `isUnknownCommand`). |

---

## Commands

All commands include `type` + `cmd` in the table below for clarity, but
those fields are stamped by the typed builder; callers pass only the
parameters.

### Motion (`type:"M"`)

| `cmd` | Params | Reply on ack | Notes |
|---|---|---|---|
| `G1` | `X?, Y?, Z?, A?, B?, C?` (REAL); `F?, Cor?, ACC?, DEA?, JERK?` (REAL); `abort?` (bool) | `{movement_id, ack:true}` | Linear move in active coord system. Omitted axis = "hold current". `A` uses `G1_A_UNSET_SENTINEL` to detect "not supplied" — passing `A:0` ≠ omitting `A`. **Gated on `CoordSystemConfigured`**: NAK `coord_not_configured` until SetCoord0/1 ran. |
| `G4` | `P` (REAL, seconds) | `{movement_id, ack:true}` | Dwell. Sub-millisecond `P` (e.g. `0.001`) is used as a one-scan yield. **Exactly one reply per id**: the ack is emitted only after `SMC_GroupWait` accepts (a refused attempt retries internally after the 100 ms cooldown with no reply — pre-2026-07-03 it acked prematurely and re-acked every cooldown). `P <= 0` / missing NAKs `bad_dwell`. |
| `M4` | `pin, state` (uint bitmasks); `reset_ms?` (uint, ms); `motion_id_offset?` (int); `motion_progress?` (REAL 0–1); `group?` (uint); `event_id?` (int — caller-supplied tag echoed back in reply); `ttl_ms?` (int — fly-event TTL, default −1 = no expiry) | `{ack:true, event_id?}` | Schedule pin-pulse "fly event" relative to a future motion id. `motion_progress` fires the pulse at X% into that move. NAK `flyevent_buffer_full` when `FlyEventAvailableCount <= 3`; NAK `flyevent_reject` when the registration gate refuses (e.g. `coord1_bind` without `trig:130`+`exit_pulse_offset>0`, or a pin-op with zero net stages). `action:'coord1_bind'` fires-then-validates through the same `Coord1CommitBind` as SYS `COORD1_BIND`: a bind that fires while refused (rebind while bound, **motion queued**, `scale[0]=0` / non-axial scale) takes the fault path — `COORD1_ERROR` push + FSM→Error via `Transition(EV_ERROR)`, never a silent no-op or silent bind (see [conveyor_pick.md](../3-subsystems/conveyor_pick.md)). `pin_op_seq` advanced form (multi-pulse schedule on one M4) — see PLC source. |
| `ReelGo` | `Distance` (REAL); `F?, ACC?, DEA?, JERK?` (REAL) | `{ack:true}` | Incremental reel-pull move. Dispatched to whichever `reelMoveRelative*` FB is idle. Always pass non-zero `JERK` (≥10000 typical) — zero throws `SMC_MR_INVALID_VELACC_VALUES`. |
| `SetCoord0` | — | `{ack:true}` | Zero current coord transform. Sets `CoordSystemConfigured := TRUE` (unblocks `G1`). Also fires `COORD_SET` push. |
| `SetCoord1` | — | `{ack:true}` | Preset coord transform (A := 60°). Same gating effect as `SetCoord0`. |
| `BLOCK_FOR_MOTION_STOP` / `WAIT_FOR_MOTION_STOP` | `timeout_ms?` (LINT, ms) | `{ack:true}` | Wait until the delta group is idle (`MovementId = 0`). `0`/absent = no timeout. NAK `block_timeout` on expiry. **Deferred reply, not a queue barrier** (since 2026-09-24): the PLC takes the wait off the inbound queue at once and replies when it resolves, so commands sent after it -- G1 included -- run meanwhile; `await` the reply before sending what must follow. One pending wait per kind (motion / reel / input); another of the same kind NAKs `wait_busy`. A wait still pending when the FSM leaves Ready NAKs `group_not_ready`; one pending when the host disconnects is dropped without a reply. The two names are aliases; UI has historically used `WAIT_FOR_MOTION_STOP`. |
| `WAIT_FOR_REEL_STOP` / `BLOCK_FOR_REEL_STOP` | `timeout_ms?` (LINT, ms) | `{ack:true}` | Wait until the **reel axis** is idle (it is NOT in the delta group, so `WAIT_FOR_MOTION_STOP` doesn't cover it): `reelGoRequest*` OR `reelMoveRelative*.Busy` all FALSE. Lets the host `await` a `ReelGo` without polling `reel_pos`, while arm moves keep running. `0`/absent = no timeout; NAK `block_timeout` on expiry. **Deferred reply, not a queue barrier** (since 2026-09-24): the PLC takes the wait off the inbound queue at once and replies when it resolves, so commands sent after it -- G1 included -- run meanwhile; `await` the reply before sending what must follow. One pending wait per kind (motion / reel / input); another of the same kind NAKs `wait_busy`. A wait still pending when the FSM leaves Ready NAKs `group_not_ready`; one pending when the host disconnects is dropped without a reply. |
| `TAPE_CYCLE` | `motion_id_offset`, `motion_progress` (trigger, relative to the last accepted move); `Distance` (reel, 0 = no advance), `F?` `ACC?` `DEA?` `JERK?`; `tx` `ty` `tz` `td` `tin` (shot gate sphere, `tin`=0: fire on leaving); `pin_op_seq`; `event_id`; `ttl_ms?` (shot gate, default 3000); `timeout_ms?` (sequence, default 6000) | `{ack:true, wait_ms, reel_ms}` when the shots are armed | The tape step as one command, sequenced on the PLC task: wait for the trigger move, advance the reel and wait for it to stop, then arm the `pin_op_seq` shots as a DistanceTrigger fly event. Deferred reply (not a queue barrier); one at a time. NAK `tape_busy`, `flyevent_buffer_full`, `tape_timeout`, `reel_busy`, `group_not_ready` (with `state`: 1 waiting for the move, 2 waiting for the reel, 3 arming). |
| `BLOCK_FOR_DIGITAL_INPUT` | `pin` (uint), `state` (uint), `group?` (uint), `timeout_ms?` (LINT) | `{ack:true}` | Wait for the digital input bits in `pin` (byte `group`) to match `state`. `0`/absent `timeout_ms` = **30 s ceiling** (never waits forever); NAK `block_timeout` on expiry. **Deferred reply, not a queue barrier** (since 2026-09-24): the PLC takes the wait off the inbound queue at once and replies when it resolves, so commands sent after it -- G1 included -- run meanwhile; `await` the reply before sending what must follow. One pending wait per kind (motion / reel / input); another of the same kind NAKs `wait_busy`. A wait still pending when the FSM leaves Ready NAKs `group_not_ready`; one pending when the host disconnects is dropped without a reply. **No `WAIT_FOR_DIGITAL_INPUT` alias.** |
| `WAIT_FOR_TRIGGER_MOTION_PROGRESS` | `motion_id?` (uint, defaults to last accepted), `motion_id_offset?` (int), `motion_progress` (REAL), `ttl_ms?` | *(deferred)* `{ack:true}` via `ACK_SRC_ID` when the trigger fires | Renderer-side await of a fly-event firing point. No immediate reply on successful registration. NAK `flyevent_reject` when refused (e.g. resolved `motion_id`=0), NAK `flyevent_buffer_full` when `FlyEventAvailableCount <= 3`, If the trigger's TTL expires the PLC pushes `TRIGGER_ERR` (error_code 100) and, since 2026-10-05, also NAKs this waiting id with `trigger_timeout` (before, the id stayed unanswered until the host's own timeout). `FLUSH` NAKs it with `flushed`. |
| `READ_LATEST_CMD_LOCATION` | — | `{X, Y, Z, A, ...}` | Last commanded Cartesian pose (post-coord-transform). |
| `GET_DIGITAL_INPUT` | `group?` (ignored: the read is commented out in the PLC) | `{state}` | Current digital input word. Returns 0 when `HECAT_1616` unwired. |
| `getDigitalInputFlipCount` | — | `{...flip_counts}` | Accumulated edge counts per pin. |
| `RESET_DBG_INFO` | — | `{ack:true}` | M-side branch. Only fires when FSM=Ready (use the SYS variant if you need the unconditional path). Both branches delegate to the same `ResetDiagCounters()` — one canonical list, no drift. |

### System (`type:"SYS"`)

| `cmd` | Params | Reply on ack | Notes |
|---|---|---|---|
| `PING` | — | `{pong:true, runtime_ms}` | Heartbeat. Stamps `GVL.LastUiPingMs`, bumps `UiPingCount`. Sent by `PluginHello` every 1 s when nothing else was sent. The PLC does not trip Error on silence (see Transport). |
| `GET_MACHINE_STATE` | — | `{st, st_str, err_src, err_id, motion_buffer_size, movement_id, last_completed_movement_id, runtime_ms, coord_set, axes_err_mask, axes_state, axes_err_id, axes_labels, axes_sim_mask, reel_pos, scratchpad:{...}, boot_epoch_now}` | Pure read; doesn't touch FSM. Renderer fires automatically on every `tcpConnected` false→true. `axes_err_mask`: bit 0–3 = `EAxis0/1/2/reelpullmotor.bError`. `axes_state`: byte 0–3 = each axis's `nAxisState` ordinal. `axes_err_id`: 4-element msgpack array of `uiDriveInterfaceError` per axis (DS402 drive-interface fault code; same ordering as the mask bits); 0 = no fault. `reel_pos`: REAL, `reelpullmotor.fActPosition` in axis-scaled user units (modulo 200); `reel_odo_counts` / `reel_odo_jumps` / `reel_counts_per_mm` / `reel_counts_per_turn`: the reel odometer -- the reel encoder in counts since the PLC booted (per-scan differences of the drive's raw position, so unwrapped and immune to the 32-bit counter wrap), never reset -- the position resets left out of it, and the numbers to convert: mm = counts / reel_counts_per_mm (-256 here: the counts run down as the tape goes forward), one reel turn = reel_counts_per_turn (51200 = 200 mm); renderer derives carrier-tape cell number — see [decisions_2026-06-22.md §4 (1)](../../doc_review/decisions_2026-06-22.md). `scratchpad`: nested map `{schema_version, plan_id, plan_index, intent_kind, intent_movement_id, last_vision_pulse, boot_epoch}` — host-owned resume cursor, PLC opaque (§4 (2)). `boot_epoch_now`: current PLC boot counter; mismatch vs `scratchpad.boot_epoch` ⇒ stale cursor. `last_completed_movement_id`: latched at MOVE_DONE emit; distinct from `movement_id` (which bumps when a move *starts*) — resume reconcile must compare `scratchpad.intent_movement_id` against this one. See [implementation_review §2](../../doc_review/implementation_review_2026-06-22.md). `axes_sim_mask`: effective per-axis simulation mask (see `SET_AXIS_SIM` below); bit order matches `axes_labels`. `fault_seq` / `fault_src` / `fault_id` (since 2026-10-05): bumped and copied at every Error entry and kept past the reset to UnInited (unlike `err_src`, which the reset clears); also in `ST_CHG`. `maint_left_ms`: the maintenance gate's time left (`MAINT_ARM`). |
| `SCRATCHPAD_WRITE` | `plan_id`, `plan_index`, `intent_kind`, `intent_movement_id`, `last_vision_pulse` (all DINT/UDINT) | `{ack:true}` | Writes the host-owned resume cursor. v1 wire contract: caller sends ALL five fields per write (no partial updates — buys nothing because the host writes the whole cursor before each unrecoverable action anyway). PLC stamps `schema_version=1` and `boot_epoch=GVL.BootEpochCount` on every write so subsequent reads are validatable. NAK `partial_scratchpad` if any of the five fields is missing or negative; NAK `scratchpad_range` if a value exceeds its round-trippable width (2³¹-1; 255 for `intent_kind`) — rejected writes leave the stored cursor untouched. See [decisions §4 (2)](../../doc_review/decisions_2026-06-22.md). |
| `GET_DIAG` | — | `{runtime_ms, sm_scans, remp_overflow_drop, remp_drop, remp_drop_reply, remp_drop_trig, overlen_drop, send_stall_drop, pending_stchg_drop, pending_movedone_drop, group_not_ready_nak, missing_type_nak, coord_not_cfg_nak, proto_mismatch_nak, unknown_cmd_nak, flyevent_reject_nak, flyevent_full_nak, idle_reset, read_err_reset, parser_err_reset, write_err_reset, client_connect_count, server_long_idle_count, server_active, bind_addr, ui_ping_count, ui_hb_stale_count, group_error_stop_trips, ping_max_gap_ms, last_ui_ping_ms, st_chg_event_count, self_reentry, dupe_cmd, io_cmd_count, io_trig_count, flyevent_avail}` | Comm-stability counter dump for `DiagPanel`. Pure read. Every counter here is cleared by `RESET_DBG_INFO`; non-counters (`runtime_ms`, `sm_scans`, `server_active`, `bind_addr`, `last_ui_ping_ms`, `flyevent_avail`) are not. |
| `RESET_DBG_INFO` | — | `{reset:true}` | SYS-side branch — clears every counter `GET_DIAG` publishes via the canonical `ResetDiagCounters()`. Does **not** require FSM=Ready (the M-side variant does). |
| `GA_EV` | `ev` (int — `E_RobotEvent` ordinal); `expect_st?` (int — the state the event was decided on, since 2026-10-05: if the FSM has moved on, the event is NOT applied and the reply is a NAK `state_changed` with the current state) | `{st, st_str, err_src, err_id, fault_seq}` | Drive FSM transition. `ev=0` (`EV_NONE`) is a **no-op state poll** — ack:true + current state, no transition (the renderer's `init_plc_motion` loop uses it to read `st_str` before stepping the sequence). Host-postable **transition** events are **exactly** `{2,4,6,7,8,9}` (`EV_POWER_ON`/`EV_GROUP_ENABLE`/`EV_HOME_GO`/`EV_HOME_GO_FORCE_SKIP`/`EV_RESET`/`EV_ERROR`); anything else — the sparse-enum gaps 1/3/5 and the internal `EV_OK`=10/`EV_ER`=11 — NAKs `unknown_event`. SYS-only. See [memory: E_RobotEvent numeric values](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/plc_event_numeric_values.md). |
| `EVT_MARK` | `code` (uint) | `{ack:true}` only when an `id` is sent | Host timestamp mark in the PLC event log (`GVL.EvHead`, kind 10, served at `GET /e` on :8126). Order-free SYS ring. Send without an id (fire-and-forget); codes are `CalibPage.tsx` `EVT` / `tools/sim/event_log.py` `MARKS`. |
| `MAINT_ARM` | `ttl_s` (1..600; 0 = disarm) | `{armed, left_ms}` | Maintenance gate (since 2026-10-05). Needed by `DIRECT` start, `DRV_SDO` write, `DELTA_MODE real:1`, `JOINT_MOVE` on a real axis and `SET_AXIS_LIMITS` above the downloaded limits; without it they NAK `maint_not_armed`. Closes after `ttl_s`, on host disconnect and on Error entry. Not an authentication (any client can arm it); it makes the intent explicit and is counted (`GET_DIAG maint_arm_count / maint_refused`). The Python tools arm it only in a run with the owner's OK (`machine.sys_cmd`); the UI arms it from the path test's "site is clear" Real button and the motor test's x2 / x4 runs. |
| `SET_AXIS_SIM` | `mask` (uint 0–15) | `{ack:true, mask}` | Per-axis simulation mask (bench mode). Bit layout matches `axes_labels` order: bit 0–2 = `EAxis0/1/2` (delta trio), bit 3 = `reelpullmotor`; 1 = simulated. Honored **only while FSM=UnInited** (drives are powerless there, so flipping virtual mode is safe) — NAK `not_uninited` otherwise; NAK `bad_mask` for out-of-range. Reply echoes the **effective** mask: request OR the project's configured mask (`GVL.AxisSimConfigMask`, each axis's device-tree virtual mode captured on the first scan) OR `0x0F` when the legacy `GVL.bVirtualMotorsMode` gate is open. The mask can only **add** simulation: an axis that is virtual in the project stays virtual whatever the request, so the delta trio can never be turned real from the host; that takes a project change and download. The request self-expires after 10 min (same TTL policy as the legacy gate); the next `EV_RESET` after that reverts to the configured mask, not to all-real. Current mask is published as `axes_sim_mask` in `GET_MACHINE_STATE`. |
| `DI_WATCH` | `mask` (uint16 pins), `edge` (1 rising / 2 falling / 3 both / 0 stop watching), `throttle_ms` (0–60000, default 200) | `{ack:true, mask, state}` | Push a `DI` event when these inputs change, sampled every 1 ms scan (`UpdateDiWatch`). Per pin: the first matching edge after a quiet throttle period goes out at once, later changes are summarised into one event per `throttle_ms`, so no change is lost to the throttle. Re-registering a pin resets its summary. Cleared when the host disconnects. Order-free SYS ring. `mask`/`state` in the reply: pins watched, input levels now. Used by the input watchdog (`lib/production/inputs.ts`). |
| `PLAN_SET` / `PLAN_GET` | see `lib/protocol.ts` | | The production plan kept by the PLC (retained): the whole plan and the tape cells advanced. `PLAN_GET` also reports an open tape move: `reel_open`, `reel_moving`, `reel_pos_lost`, `reel_rest_mm`, `reel_cells`, `reel_counted`, `reel_interrupts`, and the reel odometer `reel_odo_counts` / `reel_odo_jumps` / `reel_counts_per_mm` / `reel_counts_per_turn` (see `GET_MACHINE_STATE`) ([estop_recovery](../../doc_review/estop_recovery_2026-09-25.md)). |
| `REEL_RESUME` | `F, ACC, DEA, JERK` (reel move dynamics) | `{ack:true, rest_mm}` | Finish a tape move a fault stopped short: moves the rest of the way, counting cells as they pass. Poll `PLAN_GET` until `reel_open` is false. NAK `reel_position_lost` (PLC restarted while open), `tape_busy`, `group_not_ready`, `reel_busy`. While a move is open, `TAPE_CYCLE` NAKs `reel_interrupted`. |
| `REEL_CLEAR` | `count` | `{ack:true, cells_done}` | Close an interrupted tape move without moving (tape aligned by hand), counting `count` more of its cells. |
| `FLUSH` | -- | `{ack:true, fly_flushed}` | Drop what an ended host sequence left: fly events (unfired pin op -> `TRIGGER_ERR` 101 with its `event_id`; pending trigger wait -> NAK `flushed`), pending waits and the TAPE sequence (NAK `flushed`). FSM untouched. Ordered with the motion packets: drops only what was sent before it. The UI sends it at every run start and end. |
| `SET_FLY_TTL` | `ttl_ms` (100..3 600 000; negative = never) | `{ack:true, ttl_ms}` | TTL of an M4 sent without `ttl_ms` (`GVL.FlyEventDefaultTtlMs`, 10 000 at start). The UI sets `FLY_EVENT.DEFAULT_TTL_MS` / speed override at run start and speed changes. |
| `AXIS_INFO` | `axis` (0-2 delta joints, 3 A, 4 EAXIS_A, 5 reel), `reset_peaks` (1 = reset all axes' peaks) | `{axis, virt, st, err_flag, pos, act, vel, lv, la, ld, lj, cv, ca, cd, cj, sf, pv, pa, pj, arm_ok, arm_x, arm_y, arm_z}` | One axis for the Motors page, axis units: limits in force (`l*`) and as downloaded (`c*`), peaks of the set values since reset (`p*`), increments per unit (`sf`), the arm's TCP in the G1 frame. Order-free SYS ring. NAK `bad_axis`. |
| `SET_AXIS_LIMITS` | `axis`; `v`, `a`, `d`, `j` (> 0, each optional) or `restore:1` | `{ack:true, lv, la, ld, lj}` | Run-time dynamic limits (not saved; download / restart restores). Only in UnInited: the group takes them when enabled, and a change under an enabled group stops it. NAK `bad_axis`, `not_uninited`, `bad_value`. |
| `IO_STATE` | `reset` (1 = clear the change marks after this reply) | `{ack:true, in0, in1, in2, chg0, chg1, chg2, out, sim}` | The IO slave's input bytes as read, in any FSM state: `in1`/`in2` = input pins 0-7 / 8-15 (terminal CH1 / CH2), `in0` = the EC0808DN's own CH1; `chg*` = bits changed since the last reset; `out` = output bits; `sim` = simulated inputs in use by the program. Order-free SYS ring. The Motors page's input lamps (`InputMonitor`). |

## Push events (PLC → UI, no `id`)

| `name` | Payload | Fired when |
|---|---|---|
| `ST_CHG` | `{st, st_str, from, runtime_ms, fault_seq, fault_src}` | FSM `_eState` changes (including supervisor-triggered transitions like Ready→Error on heartbeat stale). |
| `COORD_SET` | `{value}` | `GVL.CoordSystemConfigured` changes, both edges: `value:true` on the first `SetCoord0`/`SetCoord1` (or the automatic apply on Ready entry), `value:false` when it is cleared (UnInited / Error entry). |
| `MOVE_DONE` | `{movement_id, runtime_ms}` | `MotionBufferSize` transitions >0 → 0 with a fresh `LastAcceptedMovementId` ("queue drained") — **only while FSM=Ready and the group is healthy**. An error/abort flush (EV_ERROR, GroupErrorStop, read-FB error) emits no MOVE_DONE and does not advance `last_completed_movement_id`, so the §4(2) resume reconcile correctly sees aborted moves as unfinished. |
| `DI` | `{pin, state, flips, high_ms, max_high_ms, max_low_ms, t_first, reel_odo_counts, t}` | A pin registered with `DI_WATCH` changed (see there). `state`: level now; `flips`: changes since the pin's last event; `high_ms`: time high since then; `max_high_ms`/`max_low_ms`: longest run at each level that ended since then or is still going; `t_first`/`reel_odo_counts`: PLC `RuntimeMs` and the reel odometer (counts) at the first of those changes; `t`: `RuntimeMs` at the push. |
| `TRIGGER_ERR` | `{error_code, src_id, event_id}` | A registered FlyEvent's trigger errored instead of firing — currently only TTL expiry (`error_code`=100 `TRIGGER_TIMEOUT_ERR`), including the B2c invalid-position decay path. `event_id` echoes the M4 registration. Added 2026-07-08 — the packet previously carried no `kind`/`name` and the renderer dropped it, so a timed-out pin op was invisible to the UI. |
| `COORD1_ERROR` | `{event_id, ref_pulse, exit_pulse, pulse_at_err, movement_id, mv_progress, arm_x, arm_y, ...}` | A belt-follow (COORD1) bind or window fault; the FSM goes to Error with it. |
| `HEARTBEAT` | `{runtime_ms, remp_drop_count}` | Every 500 ms (`HEARTBEAT_INTERVAL_MS`), so a client can see a dead PLC socket without its own PING. The renderer ignores it today (review 2026-10-05). |

Renderer subscribes via `window.addEventListener('plc:event', ...)` and
filters on `e.detail.name`.

---

## Builder usage (`lib/protocol.ts`)

```ts
import { cmd } from '../lib/protocol';

// Before:
await sendTcpMsgPack({ type: 'M', cmd: 'G1', X: 10, Y: 20, F: 1000 });

// After:
await sendTcpMsgPack(cmd.G1({ X: 10, Y: 20, F: 1000 }));
```

The builders return a plain object with `type` + `cmd` + the named
params. They do not call `sendTcpMsgPack` — the caller still controls
fire-and-forget vs. await, timeouts, and reply handling. Migration is
mechanical and can happen one call site at a time.

**Rule going forward:** new `sendTcpMsgPack` call sites must use a
builder. If you need a command that doesn't have one, add it to
`protocol.ts` and update this doc in the same PR.
