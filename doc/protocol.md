# UI ↔ PLC wire protocol

Authoritative reference for the MessagePack TCP protocol spoken between
the renderer (`PluginHello.tsx` → `sendTcpMsgPack`) and the PLC
dispatcher (`AxisGroupSM.st`). When this file and the code disagree,
the code wins and this file is wrong — fix it.

Cross-refs:
- PLC handlers: [`plc.md`](./plc.md) §"Command protocol".
- Architecture (transport, ring buffers, push events):
  [`architecture.md`](./architecture.md).
- Typed builders: [`../lib/protocol.ts`](../lib/protocol.ts) — every
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
| `type` | string | **yes** | One of `"M"` (motion / IO / dispatch), `"SYS"` (heartbeat / diagnostics), `"AUX"` (reserved). Missing → NAK `err='missing_type_field'`. |
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
| `block_timeout` | `BLOCK_FOR_*` exceeded its `timeout_ms`. |
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
| `G4` | `P` (REAL, seconds) | `{ack:true}` | Dwell. Sub-millisecond `P` (e.g. `0.001`) is used as a one-scan yield. |
| `M4` | `pin, state` (uint bitmasks); `reset_ms?` (uint, ms); `motion_id_offset?` (int); `motion_progress?` (REAL 0–1); `group?` (uint); `event_id?` (int — caller-supplied tag echoed back in reply); `ttl_ms?` (int — fly-event TTL, default −1 = no expiry) | `{ack:true, event_id?}` | Schedule pin-pulse "fly event" relative to a future motion id. `motion_progress` fires the pulse at X% into that move. Rejected when `FlyEventAvailableCount <= 3`. `pin_op_seq` advanced form (multi-pulse schedule on one M4) — see PLC source. |
| `ReelGo` | `Distance` (REAL); `F?, ACC?, DEA?, JERK?` (REAL) | `{ack:true}` | Incremental reel-pull move. Dispatched to whichever `reelMoveRelative*` FB is idle. Always pass non-zero `JERK` (≥10000 typical) — zero throws `SMC_MR_INVALID_VELACC_VALUES`. |
| `SetCoord0` | — | `{ack:true}` | Zero current coord transform. Sets `CoordSystemConfigured := TRUE` (unblocks `G1`). Also fires `COORD_SET` push. |
| `SetCoord1` | — | `{ack:true}` | Preset coord transform (A := 60°). Same gating effect as `SetCoord0`. |
| `BLOCK_FOR_MOTION_STOP` / `WAIT_FOR_MOTION_STOP` | `timeout_ms?` (LINT, ms) | `{ack:true}` | Wait until motion buffer drains. `0`/absent = wait forever. NAK `block_timeout` on expiry. The two names are aliases; UI has historically used `WAIT_FOR_MOTION_STOP`. |
| `BLOCK_FOR_DIGITAL_INPUT` | `pin` (uint), `state` (uint), `group?` (uint), `timeout_ms?` (LINT) | `{ack:true}` | Wait for digital input bit to match `state`. Returns 0/no-op if `HECAT_1616` unwired. Same timeout semantics as `BLOCK_FOR_MOTION_STOP`. **No `WAIT_FOR_DIGITAL_INPUT` alias** — unlike `WAIT_FOR_MOTION_STOP`, the dispatcher does not accept a `WAIT_FOR_…` form here ([AxisGroupSM.st:1218](../codesys_code/Application/APPs/AxisGroupSM.st#L1218)). |
| `WAIT_FOR_TRIGGER_MOTION_PROGRESS` | `motion_id_offset?` (int), `motion_progress` (REAL) | `{ack:true}` | Renderer-side await of a fly-event firing point. NAKs with `TRIGGER_TIMEOUT_ERR` if the referenced motion never fires the trigger. |
| `READ_LATEST_CMD_LOCATION` | — | `{X, Y, Z, A, ...}` | Last commanded Cartesian pose (post-coord-transform). |
| `GET_DIGITAL_INPUT` | `group?` | `{value}` | Current digital input word. Returns 0 when `HECAT_1616` unwired. |
| `getDigitalInputFlipCount` | — | `{...flip_counts}` | Accumulated edge counts per pin. |
| `RESET_DBG_INFO` | — | `{ack:true}` | M-side branch — also resets `IoTrigger*` / `IoCommandCount`. Only fires when FSM=Ready (use the SYS variant if you need the unconditional path). |

### System (`type:"SYS"`)

| `cmd` | Params | Reply on ack | Notes |
|---|---|---|---|
| `PING` | — | `{pong:true, runtime_ms}` | Heartbeat. Stamps `GVL.LastUiPingMs`, bumps `UiPingCount`. Sent by `PluginHello` every 1 s; PLC trips Error after 5 s of silence. |
| `GET_MACHINE_STATE` | — | `{st, st_str, err_src, err_id, motion_buffer_size, movement_id, runtime_ms, coord_set, axes_err_mask, axes_state}` | Pure read; doesn't touch FSM. Renderer fires automatically on every `tcpConnected` false→true. `axes_err_mask`: bit 0–3 = `EAxis0/1/2/reelpullmotor.bError`. `axes_state`: byte 0–3 = each axis's `nAxisState` ordinal. |
| `GET_DIAG` | — | `{runtime_ms, sm_scans, remp_drop, overlen_drop, send_stall_drop, group_not_ready_nak, missing_type_nak, coord_not_cfg_nak, proto_mismatch_nak, idle_reset, read_err_reset, parser_err_reset, ui_ping_count, ui_hb_stale_count, ping_max_gap_ms, last_ui_ping_ms, st_chg_event_count}` | Comm-stability counter dump for `DiagPanel`. Pure read. Resettable via `RESET_DBG_INFO`. |
| `RESET_DBG_INFO` | — | `{reset:true}` | SYS-side branch — clears all counters above. Does **not** require FSM=Ready (the M-side variant does). |
| `GA_EV` | `ev` (int — `E_RobotEvent` ordinal) | `{st, st_str, err_src, err_id}` | Drive FSM transition. UI uses this to step `EV_POWER_ON` / `EV_GROUP_ENABLE` / `EV_HOME_GO` / `EV_RESET`. SYS-only — there is no `type:'M'` GA_EV handler (the dead `IF FALSE` block at [AxisGroupSM.st:910](../codesys_code/Application/APPs/AxisGroupSM.st#L910) is not live). See [memory: E_RobotEvent numeric values](../../../../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/plc_event_numeric_values.md). |

### Auxiliary (`type:"AUX"`)

Reserved. Currently drained by AxisGroupSM with no handlers; do not
send from the UI.

---

## Push events (PLC → UI, no `id`)

| `name` | Payload | Fired when |
|---|---|---|
| `ST_CHG` | `{st, st_str, from, runtime_ms}` | FSM `_eState` changes (including supervisor-triggered transitions like Ready→Error on heartbeat stale). |
| `COORD_SET` | `{runtime_ms}` | `GVL.CoordSystemConfigured` FALSE→TRUE (i.e. first `SetCoord0`/`SetCoord1` after UnInited). |
| `MOVE_DONE` | `{movement_id, runtime_ms}` | `MotionBufferSize` transitions >0 → 0 with a fresh `LastAcceptedMovementId` ("queue drained"). |

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
