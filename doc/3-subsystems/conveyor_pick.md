# Conveyor pick architecture (snapshot 2026-06-21)

End-to-end picture of the conveyor pick-and-place feature as it stands
today. Covers the kernel-native belt-tracking layer (PLC Phase 4) and
the renderer orchestrator skeleton that consumes it (Phase 6 step 1).

For PLC source structure see [`plc.md`](./plc.md); for the base wire
shape see [`protocol.md`](../2-contracts/protocol.md).

---

## Why this exists

Pick-from-moving-belt needs the arm to chase an object whose pose is
known at one instant (vision-detected at pulse `P0`) and then evolves
with the belt encoder. We use SoftMotion's PCS_1 coordinate system
(`MC_TrackConveyorBelt`) — the SoftMotion kernel handles the moving-
frame math, the host stays in object-local coordinates.

The system is built around three hard contracts:

1. **Bind is a hard barrier.** Once a Coord1 bind is active, the host
   may not mutate belt-object parameters; any rebind attempt while
   bound trips SM Error.
2. **One window, one error.** Failure modes (arm can't catch up, host
   ran past the pick window) collapse into a single signal: belt
   overshoots `ref_pulse + exit_pulse_offset` → `COORD1_ERROR` event
   + state→Error + `MC_GroupStop`.
3. **PLC stays vision-blind.** All object pose / belt-encoder
   correlation happens on the host. PLC only knows pulses and the
   `ref_xyz` the host computes.

---

## Layered view

```
┌────────────────────────────────────────────────────────────────────┐
│ Renderer (orchestrator/conveyorPick.ts)                            │
│                                                                    │
│   runConveyorPick(req, {send, subscribeEvents})                    │
│      binding → tracking → picking → untracking → placing           │
│                                                                    │
│   COORD1_ERROR by event_id → structured failure                    │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  M4Bind + G1{frame:1} + G1{frame:0}
                               │  + push event COORD1_ERROR
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ Wire (msgpack over TCP 8125)                                       │
│                                                                    │
│   M  cmd=M4 trig=130 action='coord1_bind'                          │
│        pulse_target ref_xyz scale exit_pulse_offset event_id       │
│   M  cmd=G1 frame=0|1 X/Y/Z F                                      │
│   SYS cmd=COORD1_UNBIND  (dev path)                                │
│   SYS cmd=GET_COORD1_DEBUG  arm_x/y/z + pcs_x/y/z + pulse_raw      │
│   event {name:'COORD1_ERROR', event_id, ref_pulse, exit_pulse,     │
│          pulse_at_err, movement_id, mv_progress, arm_*, pcs_*}     │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ PLC (CODESYS, AxisGroupSM)                                         │
│                                                                    │
│   FlyEvent ring (PulseTrigger):                                    │
│     bind scheduled at pulse_target → fires once → atomic apply     │
│                                                                    │
│   MC_TrackConveyorBelt + PCS_1:                                    │
│     kernel updates PCS_1 origin each scan from ConveyorEncoderAxis │
│                                                                    │
│   Per-scan window-exit detector (after UpdateMotionProgress):      │
│     if Coord1Bound && PulseRaw > Coord1ExitPulse                   │
│       latch diagnostic snapshot                                    │
│       LastErrorSource := 'Coord1:window_exit'                      │
│       Transition(EV_ERROR) → state Error                           │
│                                                                    │
│   On Error entry:                                                  │
│     MC_GroupStop runs → arm freezes                                │
│     COORD1_ERROR push event emitted with snapshot                  │
│     Coord1Bound := FALSE; Coord1ExitPulse := -1                    │
│                                                                    │
│   ConveyorEncoderAxis (SMC_VirtualDrive):                          │
│     bridges GVL.ConveyorPulseRaw to SM3 axis position              │
│     dev mode: synthetic monotonic pulse generator                  │
│     production: real EC encoder (Phase 0, not yet wired)           │
└────────────────────────────────────────────────────────────────────┘
```

---

## Wire protocol — bind

Host issues a single `M` packet to schedule the bind. The bind doesn't
take effect until belt pulse crosses `pulse_target`; at that moment PLC
captures `ref_xyz` as the PCS_1 origin in WCS and arms the window.

```jsonc
{
  "type": "M", "cmd": "M4",
  "trig": 130,                 // PulseTrigger
  "action": "coord1_bind",
  "pulse_target": 2000,        // belt pulse that fires bind
  "ref_xyz": [10, 0, -150],    // WCS origin of moving object at fire
  "scale":   [100, 0, 0],      // X non-zero, Y/Z must be 0
  "exit_pulse_offset": 1500,   // pick window; >0 required
  "event_id": 99,              // host correlation id, echoed in reply
  "ttl_ms": 5000               // FlyEvent expiry if pulse never hits
}
```

Reply: `{ack:true, event_id:99, ev_buf_space:<n>}`.

Registration gate (in `ProcessMotionPacket.st`): the packet is only
queued if `action='coord1_bind' && trig=PulseTrigger &&
exit_pulse_offset>0`. Otherwise ack'd but silently dropped.

Rejection paths (all produce `LastErrorSource = 'Coord1:<reason>'` + SM
Error on bind fire — not at queue time):
- `rebind_active` — Coord1 already bound when this entry fires
- `bad_scale` — X is zero, or Y/Z is non-zero

---

## Wire protocol — fault event

Pushed once per fault. Always followed by a `ST_CHG` to Error (990).
Host correlates by `event_id`.

```jsonc
{
  "kind": "event", "name": "COORD1_ERROR",
  "event_id": 99,
  "ref_pulse": 2000,           // belt pulse when bind fired
  "exit_pulse": 3500,          // ref_pulse + exit_pulse_offset
  "pulse_at_err": 3502,        // belt pulse at fault
  "movement_id": 32,           // G1 in flight, 0 if none
  "mv_progress": 1.0,          // 0..1 of the active G1
  "arm_x": 95.83, "arm_y": 0.0, "arm_z": -145.0,   // WCS, ReadPosition.c
  "pcs_x": 5.0,   "pcs_y": 0.0, "pcs_z": -150.0,   // live PCS_1 origin
  "runtime_ms": 52086351
}
```

`pulse_at_err - exit_pulse` is typically 1 EC tick (1–2 pulses at
synthetic step=2), so detection latency is one PLC scan.

Failure classification (host-side from payload):
- `mv_progress < 1.0 && (arm vs pcs diff small)` → arm couldn't catch
  belt; review feedrate / accel headroom
- `mv_progress == 1.0` → host scheduled past the pick zone; widen
  `exit_pulse_offset` or fire bind earlier

---

## Wire protocol — motion

G1 frame tag selects coordinate system:
- `frame: 0` (default) — WCS, no bind required
- `frame: 1` — PCS_1; NAK `bad_frame` if no Coord1 bind active

Issuing `G1 frame=0` after `frame=1` does **not** implicitly unbind on
the PLC side — the window-exit detector keeps running until either the
window expires or host sends `SYS COORD1_UNBIND`. (Dev/test only — SYS
unbind bypasses the window guard.)

---

## Renderer orchestrator

`orchestrator/conveyorPick.ts` is the host-side state machine. Pure
async, no React imports — dependencies injected:

```ts
const result = await runConveyorPick(req, {
  send: sendTcpMsgPack,
  subscribeEvents: makeWindowEventSubscriber(),
  onPhase: (p) => console.log('phase', p),
});
```

Phases: `binding → tracking → picking → untracking → placing → done`.
On any `COORD1_ERROR` with matching `event_id`, the phase is latched as
`error` and the call resolves to:

```ts
{
  ok: false,
  phase: 'tracking' | 'picking' | ...,
  reason: 'coord1_window_exit',
  coord1_error: { event_id, ref_pulse, ..., arm_x, arm_y, arm_z, ... }
}
```

The orchestrator does not retry — recovery (EV_ERROR clear, re-home,
re-detect, re-schedule) lives one layer up because the right response
depends on the failure classification above.

---

## State machine summary

| State (PLC) | Numeric | Meaning |
|---|---|---|
| Ready | 70 | Normal; G1 frame=0 or frame=1 accepted |
| Error | 990 | `MC_GroupStop` running; arm frozen; needs EV_RESET |

| Coord1 flag | Set by | Cleared by |
|---|---|---|
| `Coord1Bound = TRUE` | bind fires from FlyEvent queue | window-exit detector OR SYS UNBIND |
| `Coord1ExitPulse` | bind fire (`PulseRaw + offset`) | window-exit detector resets to `-1` |

---

## What's NOT here yet

- **Real EC encoder** — `ConveyorEncoderAxis` is fed by a synthetic
  monotonic pulse generator (`GVL.ConveyorPulseSyntheticEnable/Step`).
  Production wiring is Phase 0.
- **Vision adapter on renderer** — `FFeederCheckID=104500` pose results
  need to be transformed into `(ref_xyz, pulse_target)` pairs. Not yet
  glued.
- **UI surface** — no page integration; trigger paths are scripted
  probes (`codesys_scripts/jobs/templates/probe_flyevent_bind.py`,
  `viz_full_cycle.py`).
- **Real belt calibration** — `scale=[100,0,0]` is the synthetic value;
  real-axis scale + belt-line orientation are Phase 7.
- **`MC_GroupStop` deceleration tuning** — currently uses
  `LatestDeceleration / LatestJerk` captured from the last motion;
  abort-from-tracking decel may want its own profile.

---

## File map

| Layer | File | Role |
|---|---|---|
| PLC | `Application/APP_COMM_FBs/FlyEventActType.st` | `COORD1_BIND := 3001` enum |
| PLC | `Application/APP_COMM_FBs/FlyEventData.st` | bind payload fields |
| PLC | `Application/GVL.st` | Coord1 state + diagnostic latches |
| PLC | `APPs/AxisGroupSM/AxisGroupSM.st` | window-exit detector, `MC_GroupStop`, `COORD1_ERROR` emit |
| PLC | `APPs/AxisGroupSM/ProcessMotionPacket.st` | M4 parse, registration gate |
| PLC | `APPs/AxisGroupSM/ProcessFlyEventsAndIo.st` | bind apply on fire |
| Wire | `lib/protocol.ts` | `cmd.M4Bind`, `G1Args.frame`, `Coord1ErrorEvent` |
| UI | `orchestrator/conveyorPick.ts` | `runConveyorPick` state machine |
| Test | `codesys_scripts/jobs/templates/probe_flyevent_bind.py` | end-to-end PLC probe (6/6 PASS) |
| Test | `codesys_scripts/jobs/templates/viz_full_cycle.py` | full cycle visualization |
| Test | `codesys_scripts/jobs/templates/viz_progress_tracking.py` | mv_progress under moving belt |
