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
- **No TLS, no auth.** Trusted LAN only.
- **Single connection.** `udiMaxConnections := 1` on the PLC; second
  client gets connection refused until idle-watchdog reclaims (~7 s).
- **Connection-loss recovery:** PLC supervisor trips Error after 5 s of
  silence (W1 A3); renderer reconnects automatically and refetches
  `GET_MACHINE_STATE`.

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
   { id, ack, runtime_ms, ...payload }     // ack=true: payload is per-command
   { id, ack:false, err, runtime_ms, ... } // ack=false: err is short string
   ```
   Renderer routes by `id` to the awaiting promise.

2. **Server-push event** (unsolicited, no `id`):
   ```
   { kind:'event', name, runtime_ms, ...payload }
   ```
   `name` is one of `ST_CHG`, `COORD_SET`, `MOVE_DONE` (see §"Push
   events"). Renderer dispatches a `window` `CustomEvent('plc:event')`.

3. **Reconnect snapshot** (from `SYS/GET_MACHINE_STATE`, technically a
   reply but treated specially): cached in `lastMachineSnapshotRef` and
   re-broadcast as `CustomEvent('plc:machine-state')`.

### Error strings

`err` field on a NAK is one of (open set; grep `'err':=` in
`AxisGroupSM.st` for the live list):

| `err` | When |
|---|---|
| `group_not_ready` | Motion command sent while FSM ≠ Ready. |
| `coord_not_configured` | `G1` sent before `SetCoord0`/`SetCoord1` since last UnInited entry. |
| `block_timeout` | A `BLOCK_FOR_*` / `WAIT_FOR_*` wait exceeded its `timeout_ms`. |
| `wait_busy` | A wait of the same kind (motion / reel / input) is already pending. |
| `missing_type_field` | Outbound packet without `type` (or unrecognised). |
| `protocol_version_mismatch` | Outbound `protocol_version` ≠ PLC's. Reply also includes `err_got`. |
| `group_error_stop` / `group_read_status_error` | MC group FB reported error before move accept. |
| _generic_ `ack:false` (no `err`) | Catch-all (e.g. fly-event buffer full, unknown cmd). |

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
| `BLOCK_FOR_DIGITAL_INPUT` | `pin` (uint), `state` (uint), `group?` (uint), `timeout_ms?` (LINT) | `{ack:true}` | Wait for the digital input bits in `pin` (byte `group`) to match `state`. `0`/absent `timeout_ms` = **30 s ceiling** (never waits forever); NAK `block_timeout` on expiry. **Deferred reply, not a queue barrier** (since 2026-09-24): the PLC takes the wait off the inbound queue at once and replies when it resolves, so commands sent after it -- G1 included -- run meanwhile; `await` the reply before sending what must follow. One pending wait per kind (motion / reel / input); another of the same kind NAKs `wait_busy`. A wait still pending when the FSM leaves Ready NAKs `group_not_ready`; one pending when the host disconnects is dropped without a reply. **No `WAIT_FOR_DIGITAL_INPUT` alias.** |
| `WAIT_FOR_TRIGGER_MOTION_PROGRESS` | `motion_id?` (uint, defaults to last accepted), `motion_id_offset?` (int), `motion_progress` (REAL), `ttl_ms?` | *(deferred)* `{ack:true}` via `ACK_SRC_ID` when the trigger fires | Renderer-side await of a fly-event firing point. No immediate reply on successful registration. NAK `flyevent_reject` when refused (e.g. resolved `motion_id`=0), NAK `flyevent_buffer_full` when `FlyEventAvailableCount <= 3`, NAK `TRIGGER_TIMEOUT_ERR` if the referenced motion never fires the trigger. |
| `READ_LATEST_CMD_LOCATION` | — | `{X, Y, Z, A, ...}` | Last commanded Cartesian pose (post-coord-transform). |
| `GET_DIGITAL_INPUT` | `group?` | `{value}` | Current digital input word. Returns 0 when `HECAT_1616` unwired. |
| `getDigitalInputFlipCount` | — | `{...flip_counts}` | Accumulated edge counts per pin. |
| `RESET_DBG_INFO` | — | `{ack:true}` | M-side branch. Only fires when FSM=Ready (use the SYS variant if you need the unconditional path). Both branches delegate to the same `ResetDiagCounters()` — one canonical list, no drift. |

### System (`type:"SYS"`)

| `cmd` | Params | Reply on ack | Notes |
|---|---|---|---|
| `PING` | — | `{pong:true, runtime_ms}` | Heartbeat. Stamps `GVL.LastUiPingMs`, bumps `UiPingCount`. Sent by `PluginHello` every 1 s; PLC trips Error after 5 s of silence. |
| `GET_MACHINE_STATE` | — | `{st, st_str, err_src, err_id, motion_buffer_size, movement_id, last_completed_movement_id, runtime_ms, coord_set, axes_err_mask, axes_state, axes_err_id, axes_labels, axes_sim_mask, reel_pos, scratchpad:{...}, boot_epoch_now}` | Pure read; doesn't touch FSM. Renderer fires automatically on every `tcpConnected` false→true. `axes_err_mask`: bit 0–3 = `EAxis0/1/2/reelpullmotor.bError`. `axes_state`: byte 0–3 = each axis's `nAxisState` ordinal. `axes_err_id`: 4-element msgpack array of `uiDriveInterfaceError` per axis (DS402 drive-interface fault code; same ordering as the mask bits); 0 = no fault. `reel_pos`: REAL, `reelpullmotor.fActPosition` in axis-scaled user units; renderer derives carrier-tape cell number — see [decisions_2026-06-22.md §4 (1)](../../doc_review/decisions_2026-06-22.md). `scratchpad`: nested map `{schema_version, plan_id, plan_index, intent_kind, intent_movement_id, last_vision_pulse, boot_epoch}` — host-owned resume cursor, PLC opaque (§4 (2)). `boot_epoch_now`: current PLC boot counter; mismatch vs `scratchpad.boot_epoch` ⇒ stale cursor. `last_completed_movement_id`: latched at MOVE_DONE emit; distinct from `movement_id` (which bumps when a move *starts*) — resume reconcile must compare `scratchpad.intent_movement_id` against this one. See [implementation_review §2](../../doc_review/implementation_review_2026-06-22.md). `axes_sim_mask`: effective per-axis simulation mask (see `SET_AXIS_SIM` below); bit order matches `axes_labels`. |
| `SCRATCHPAD_WRITE` | `plan_id`, `plan_index`, `intent_kind`, `intent_movement_id`, `last_vision_pulse` (all DINT/UDINT) | `{ack:true}` | Writes the host-owned resume cursor. v1 wire contract: caller sends ALL five fields per write (no partial updates — buys nothing because the host writes the whole cursor before each unrecoverable action anyway). PLC stamps `schema_version=1` and `boot_epoch=GVL.BootEpochCount` on every write so subsequent reads are validatable. NAK `partial_scratchpad` if any of the five fields is missing or negative; NAK `scratchpad_range` if a value exceeds its round-trippable width (2³¹-1; 255 for `intent_kind`) — rejected writes leave the stored cursor untouched. See [decisions §4 (2)](../../doc_review/decisions_2026-06-22.md). |
| `GET_DIAG` | — | `{runtime_ms, sm_scans, remp_overflow_drop, remp_drop, remp_drop_reply, remp_drop_trig, overlen_drop, send_stall_drop, pending_stchg_drop, pending_movedone_drop, group_not_ready_nak, missing_type_nak, coord_not_cfg_nak, proto_mismatch_nak, unknown_cmd_nak, flyevent_reject_nak, flyevent_full_nak, idle_reset, read_err_reset, parser_err_reset, write_err_reset, client_connect_count, server_long_idle_count, server_active, bind_addr, ui_ping_count, ui_hb_stale_count, group_error_stop_trips, ping_max_gap_ms, last_ui_ping_ms, st_chg_event_count, self_reentry, dupe_cmd, io_cmd_count, io_trig_count, flyevent_avail}` | Comm-stability counter dump for `DiagPanel`. Pure read. Every counter here is cleared by `RESET_DBG_INFO`; non-counters (`runtime_ms`, `sm_scans`, `server_active`, `bind_addr`, `last_ui_ping_ms`, `flyevent_avail`) are not. |
| `RESET_DBG_INFO` | — | `{reset:true}` | SYS-side branch — clears every counter `GET_DIAG` publishes via the canonical `ResetDiagCounters()`. Does **not** require FSM=Ready (the M-side variant does). |
| `GA_EV` | `ev` (int — `E_RobotEvent` ordinal) | `{st, st_str, err_src, err_id}` | Drive FSM transition. `ev=0` (`EV_NONE`) is a **no-op state poll** — ack:true + current state, no transition (the renderer's `init_plc_motion` loop uses it to read `st_str` before stepping the sequence). Host-postable **transition** events are **exactly** `{2,4,6,7,8,9}` (`EV_POWER_ON`/`EV_GROUP_ENABLE`/`EV_HOME_GO`/`EV_HOME_GO_FORCE_SKIP`/`EV_RESET`/`EV_ERROR`); anything else — the sparse-enum gaps 1/3/5 and the internal `EV_OK`=10/`EV_ER`=11 — NAKs `unknown_event`. SYS-only. See [memory: E_RobotEvent numeric values](../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/plc_event_numeric_values.md). |
| `EVT_MARK` | `code` (uint) | `{ack:true}` only when an `id` is sent | Host timestamp mark in the PLC event log (`GVL.EvHead`, kind 10, served at `GET /e` on :8126). Order-free SYS ring. Send without an id (fire-and-forget); codes are `CalibPage.tsx` `EVT` / `tools/sim/event_log.py` `MARKS`. |
| `SET_AXIS_SIM` | `mask` (uint 0–15) | `{ack:true, mask}` | Per-axis simulation mask (bench mode). Bit layout matches `axes_labels` order: bit 0–2 = `EAxis0/1/2` (delta trio), bit 3 = `reelpullmotor`; 1 = simulated. Honored **only while FSM=UnInited** (drives are powerless there, so flipping virtual mode is safe) — NAK `not_uninited` otherwise; NAK `bad_mask` for out-of-range. Reply echoes the **effective** mask: request OR the project's configured mask (`GVL.AxisSimConfigMask`, each axis's device-tree virtual mode captured on the first scan) OR `0x0F` when the legacy `GVL.bVirtualMotorsMode` gate is open. The mask can only **add** simulation: an axis that is virtual in the project stays virtual whatever the request, so the delta trio can never be turned real from the host; that takes a project change and download. The request self-expires after 10 min (same TTL policy as the legacy gate); the next `EV_RESET` after that reverts to the configured mask, not to all-real. Current mask is published as `axes_sim_mask` in `GET_MACHINE_STATE`. |

## Push events (PLC → UI, no `id`)

| `name` | Payload | Fired when |
|---|---|---|
| `ST_CHG` | `{st, st_str, from, runtime_ms}` | FSM `_eState` changes (including supervisor-triggered transitions like Ready→Error on heartbeat stale). |
| `COORD_SET` | `{runtime_ms}` | `GVL.CoordSystemConfigured` FALSE→TRUE (i.e. first `SetCoord0`/`SetCoord1` after UnInited). |
| `MOVE_DONE` | `{movement_id, runtime_ms}` | `MotionBufferSize` transitions >0 → 0 with a fresh `LastAcceptedMovementId` ("queue drained") — **only while FSM=Ready and the group is healthy**. An error/abort flush (EV_ERROR, GroupErrorStop, read-FB error) emits no MOVE_DONE and does not advance `last_completed_movement_id`, so the §4(2) resume reconcile correctly sees aborted moves as unfinished. |
| `TRIGGER_ERR` | `{error_code, src_id, event_id}` | A registered FlyEvent's trigger errored instead of firing — currently only TTL expiry (`error_code`=100 `TRIGGER_TIMEOUT_ERR`), including the B2c invalid-position decay path. `event_id` echoes the M4 registration. Added 2026-07-08 — the packet previously carried no `kind`/`name` and the renderer dropped it, so a timed-out pin op was invisible to the UI. |

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
