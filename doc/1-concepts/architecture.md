# System architecture (snapshot 2026-04-26)

This is the integrated picture of the machine as it stands today. For
the moving-target detail (workstream phasing, per-item status), see
[`solidification.md`](./solidification.md). For PLC source structure,
[`plc.md`](../3-subsystems/plc.md). For renderer cycle structure, [`calibpage.md`](../3-subsystems/calibpage.md).
For the host↔vision flow, [`vision_contract.md`](../2-contracts/vision_contract.md).

This doc explicitly covers **what's there now**, not what's planned.
"Open" / "Future" callouts are deliberately minimised — they live in
solidification.md.

---

## Component map

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Renderer (Electron, React)                      │
│                                                                      │
│   PluginHello.tsx ── owns TCP socket, msgpack framing, heartbeat,    │
│                      protocol-version stamping, reconnect snapshot   │
│                      fetch, dispatches `plc:event` / `plc:machine-   │
│                      state` to window CustomEvents                   │
│        ▲                                                             │
│        │ window CustomEvent                                          │
│        ▼                                                             │
│   Pages (Welcome / Operation / Calib / MiscControls / Control)       │
│     - CalibPage.tsx → main runAllObjects loop (cycle pipelining)     │
│     - ControlPage.tsx → consumes reconnect snapshot, force-routes    │
│       tab when not Ready or coord_set=false                          │
│     - OperationPage.tsx / MiscControlsPage.tsx → init_plc_motion     │
│       (SetCoord1 first, then real-world G1s)                         │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │ msgpack over TCP, port 8125
                                   │ (no length framing; rely on stream)
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        PLC (CODESYS 3.5 SP21)                        │
│                                                                      │
│   FB_TcpMsgPakServer  (single-conn slot, idle watchdog 7s,           │
│   on Comm task 5ms)    parser/read-error reset paths, all with       │
│                        diagnostic counters)                          │
│        │                                                             │
│        ▼                                                             │
│   GVL.minfo_buf_ridx ── 6-slot ring of decoded MsgPakInfo            │
│        │                                                             │
│        ▼                                                             │
│   AxisGroupSM (EC task 1ms) ── single big dispatcher:                │
│     • SYS-drain WHILE loop (bounded 16/scan, peek-tail)              │
│       - dispatches SYS / NAKs protocol_mismatch /                    │
│         NAKs missing_type_field / NAKs group_not_ready /             │
│         EXITs when motion-at-tail and FSM=Ready                      │
│     • Motion dispatcher (only when AxisGroupReady=TRUE)              │
│     • Coord-system gate, fly-event scheduler, reel two-FB driver     │
│     • Supervisors: per-state error checks, UI-heartbeat watchdog,    │
│       bus-restart throttle                                           │
│     • Server-push: ST_CHG / COORD_SET / MOVE_DONE → reMP_info_ridx   │
│        │                                                             │
│        ▼                                                             │
│   AxisGroupManager (FB-level FSM) ─► MC_* motion FBs ─► EtherCAT     │
│                                                            │         │
│                                                            ▼         │
│                                            CiA 402 servos + reel     │
│                                            (+ optional HECAT_1616)   │
└──────────────────────────────────────────────────────────────────────┘
```

Reply path mirrors the request path: `reMP_info_ridx` (32-slot reply
ring) → `FB_TcpMsgPakServer.Send` → TCP → renderer. Server-push events
share the reply ring; on overflow the oldest reply is consumed
(`ReMpDropCount`) so a stalled reader can't block fresh state.

The renderer also holds a **second, independent TCP channel** to the
**Vision Plugin** (default `localhost:7950`, JSON `;`-delimited, managed
by the same `PluginHello.tsx` socket layer via `VP_sendTcpMsgPack` /
`VP_regTcpMsgCB`). It does **not** share the PLC envelope, schema, or
ring buffers. The PLC is vision-blind: the renderer is the only thing
that interprets verdicts and commands the bin/toss actuator (via PLC
`M4` pulses). See [`vision_contract.md`](../2-contracts/vision_contract.md) for the
full flow, check-ID table, and trigger-timing diagram.

---

## Concurrency model

| Task | Period | Owners |
|---|---|---|
| `EC_Task` | 1ms / prio 0 / WD off | `AxisGroupSM`, `AxisGroupManager.Update`, all motion FBs (every-scan calls) |
| `Planning` | 1ms | empty (reserved) |
| `Comm` | 5ms / prio 20 | `TCP_MSGPAK_Server` (parser, socket I/O, ring producer) |

Crucial property: the renderer never sees the EC task. Every PLC reply
goes through the 5ms Comm task. Worst-case PLC-RTT for a SYS round-trip
is therefore one Comm-task quantum on each direction (~10ms) plus the
scan that processes it (~1ms) — ignoring TCP/network.

The renderer side is single-threaded JS in the main process. The
heartbeat (`PluginHello.tsx`) and `runAllObjects` (`CalibPage.tsx`) share
the event loop; if a long synchronous block in JS stalls heartbeats past
the 5s PLC-side stale window, the supervisor halts motion. This is by
design — see [Safety contract](#safety-contract) below.

---

## Safety contract (W1 — host-authority model)

The PLC is the source of truth for "is it safe to do this." The host
can crash, reload, freeze, or get backgrounded; the machine stays in a
known-safe state.

| Invariant | Owner | Mechanism |
|---|---|---|
| Motion only runs in `Ready` | PLC | Dispatcher NAKs motion in non-Ready states with `err='group_not_ready'` (counter `GroupNotReadyNakCount`) |
| Group must be coord-configured before G1 | PLC | `CoordSystemConfigured` gate (cleared on UnInited entry, set by `SetCoord0`/`SetCoord1`); G1 NAKs `err='coord_not_configured'` (counter `CoordNotConfiguredNakCount`) |
| Host must be alive | PLC | UI-heartbeat supervisor: when Ready and `RuntimeMs - LastUiPingMs > UI_HEARTBEAT_TIMEOUT_MS` (5000ms), FSM→Error with `err_src='Supervisor:UiHeartbeatStale'` (counter `UiHeartbeatStaleCount`) |
| Blocking commands can't park forever | PLC | `BLOCK_FOR_*` accept optional `timeout_ms`; NAK `err='block_timeout'` on expiry |
| Wire format compatible | PLC | `protocol_version` field on every packet; mismatch → NAK `err='protocol_version_mismatch'` (counter `ProtocolVersionMismatchCount`) |
| Type tag present | PLC | SYS-drain NAKs `err='missing_type_field'` (counter `MissingTypeFieldNakCount`) |
| Reconnect doesn't blindly resume | Host | `SYS/GET_MACHINE_STATE` on every TCP false→true; `ControlPage` force-routes tab to Welcome if `st_str != 'Ready'` or `coord_set=false` |
| TCP slot doesn't get stuck after host crash | PLC | `FB_TcpMsgPakServer` idle watchdog (7s with no inbound bytes while `xActive=TRUE` → reset; counter `IdleResetCount`) |

The **5s PLC supervisor > 3.5s host stale > 1s host ping interval**
ladder is intentional: the UI flags itself stale and tries reconnect
*before* the PLC takes safety action, so a brief network blip is
recoverable without halting motion.

---

## Generic-PLC principle

Decided 2026-04-25: **the PLC stays generic** — motion, IO, safety
supervisors. Domain/business logic lives in the renderer.

This explicitly excludes from the PLC:
- Material-flow state (no `WaitingForMaterial`, no `FEED` command —
  W2 was rejected on these grounds; renderer-side watchdog at
  [CalibPage.tsx:809](../../components/CalibPage.tsx#L809) stays).
- NG classification, recipe parameters, vision result interpretation.
- Direct vision↔PLC integration (e.g. PLC-issued M4 triggers driven by
  vision verdicts). **Rejected 2026-04-26** on the same generic-PLC
  grounds; vision stays host-routed. If cycle-time pressure ever points
  here, fix it host-side first (tighter scheduling, pre-arming, batched
  M4) — don't move the verdict→trigger loop into the PLC.
- Anything named after a specific cycle phase.

This is acceptable to land on the PLC:
- Per-axis fault detail (`axes_err_mask`, `axes_state` in
  `GET_MACHINE_STATE`).
- Comm-stability counters and observability surfaces (`GET_DIAG`).
- Protocol versioning, ring-buffer drop counters, supervisors that
  protect the machine itself.
- Generic events the UI may want to subscribe to (`ST_CHG`,
  `COORD_SET`, `MOVE_DONE`).

Operational tradeoff: if the renderer dies, material-flow stops. The
W1 supervisors still keep the *machine* safe. "Material won't be fed
without a live UI" is a known operational constraint, not a safety
issue.

---

## Wire protocol (current)

Envelope: every packet is a MessagePack map. Required fields:
`type` ∈ {`SYS`, `M`}, `cmd` (string). Recommended: `id` (DINT echoed
in reply), `protocol_version` (UDINT, currently `1`).

Server replies always carry the original `id` plus `ack: bool`. NAKs
carry `err: <string>` and may carry diagnostic fields (`err_got`,
`err_got_type`, `err_id`, etc.).

Server-push events have `kind: 'event'`, `name: <string>`, `runtime_ms`,
and **no `id` / `ack`**. The host dispatches them via window
`CustomEvent('plc:event', {detail})`; UI components subscribe by name.

For the full command table (G1, G4, ReelGo, SetCoord*, M4, GA_EV,
BLOCK_*, GET_*, RESET_DBG_INFO, push events), see
[`plc.md` §Command protocol](../3-subsystems/plc.md#command-protocol-messagepack-map).
For the MessagePack library's known footguns (map32 / array32 / bin /
ext) see [`msgpack.md`](../2-contracts/msgpack.md).

**Typed builders (host side).** Renderer call sites no longer hand-roll
the envelope. [`lib/protocol.ts`](../../lib/protocol.ts) exports `cmd.*`
builders (`cmd.G1({X,Y,Z})`, `cmd.M4({...})`, `cmd.Ping()`,
`cmd.GetMachineState()`, etc.) that stamp `type` and `cmd` and strip
undefined fields. The `Envelope<R>` carries a phantom reply-shape
parameter; opting into the typed `send<R>(sendTcpMsgPack, env)` helper
recovers the reply type at the call site (e.g. `GET_MACHINE_STATE`
returns `MachineState`, `READ_LATEST_CMD_LOCATION` returns
`LocationReply`). `id` and `protocol_version` are still stamped by
`PluginHello.tsx`'s `sendTcpMsgPack`; builders do not set them.
Untyped sites continue to work unchanged — `Envelope` defaults `R` to
`unknown`.

---

## Diagnostic surfaces

Everything diagnostic is reachable through one of three channels:

1. **`SYS/GET_MACHINE_STATE`** — current machine state (FSM state,
   error latch, motion buffer depth, last movement_id, runtime_ms,
   coord_set, per-axis error mask + axis-state).
2. **`SYS/GET_DIAG`** — comm-stability counters dump (drops, NAKs,
   resets, heartbeat metrics, push-event count, scan counter,
   `ping_max_gap_ms`). Resettable via `SYS/RESET_DBG_INFO`.
3. **Server-push `ST_CHG` / `COORD_SET` / `MOVE_DONE`** — unsolicited
   notification of state changes. Lower-latency than polling.

Plus the heartbeat itself: `SYS/PING` reply carries `runtime_ms`, host
tracks RTT and `ms_since_last_pong` for the dashboard.

The `LastErrorSource` / `LastErrorID` latch (in GVL, written by every
state-machine error path and by motion-FB supervisors) gives a
human-readable cause for any FSM→Error transition; surfaced in
`GET_MACHINE_STATE` as `err_src` / `err_id`.

---

## Ring buffers

Three rings:

- `minfo_buf` (6 slots, 256 bytes each) — inbound msgpack command
  decode. Slot byte 0 is length, bytes 1..255 are payload. Producer
  is the parser in `TCP_MSGPAK_Server`; consumer is `AxisGroupSM`.
  Overlen packets are dropped with `OverlenDropCount` rather than
  silently wrapping via `DINT_TO_BYTE`.
- `reMP_info_ridx` (32 slots) — outbound msgpack reply + push
  events. On overflow, oldest is consumed and `ReMpDropCount` bumps.
  Send-stall counter (`SendStallDropCount`) caps retries when
  `xActive=TRUE` but `Send()` keeps returning FALSE — half-open peer
  protection.

---

## Renderer pipelining (one-line summary)

`runAllObjects` in `CalibPage.tsx` is **deliberately non-linear**:
camera triggers return Promises that are awaited many robot moves
later, so auxiliary actions (FlexBowl shake, feeder cam, reel top
cam two-shot, reel advance) fit inside the windows where the robot
is moving away from the relevant station. Throughput depends on
this. See [`plc.md` §Timing diagram](../3-subsystems/plc.md#timing-diagram-one-steady-state-cycle)
and [`calibpage.md`](../3-subsystems/calibpage.md) for the rules.

---

## What's verified live (not just code-reviewed)

- W1 A1/A2/A3/A4: motion NAK in non-Ready, BLOCK timeouts, heartbeat
  supervisor self-trip, reconnect snapshot fetch.
- W1 A5 protocol version: mismatched packets NAK pre-dispatch.
- Coord-gate: G1 NAKs before SetCoord, accepts after.
- SYS-drain refactor: PING behind motion in UnInited returns
  `pong:true` while motion gets `group_not_ready` (no false-NAK).
- Server-push events: `ST_CHG` on every transition including
  supervisor-triggered ones; `COORD_SET` on first SetCoord;
  `MOVE_DONE` on queue drain.
- Per-axis fault detail: `axes_err_mask` and `axes_state` packed
  correctly under E-stop (mask=0, all `power_off` except reel).
- `GET_DIAG`: 17-field reply confirmed; `ping_max_gap_ms` correctly
  measures host PING jitter; `RESET_DBG_INFO` (SYS) clears comm
  counters.

What's only code-reviewed (no live demo on this branch): renderer
reconcile-on-reconnect routing in `ControlPage` (the listener exists
and is unit-reasonable, but no full crash-and-relaunch was driven on
the test rig in this session).
