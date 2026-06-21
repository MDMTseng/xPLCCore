# Vision contract (as-is, 2026-04-26)

This documents the **current** vision↔host↔PLC flow. It is descriptive,
not prescriptive: future changes (e.g. direct vision↔PLC fly-event
integration discussed in [solidification.md W7](../1-concepts/solidification.md))
should be a separate document on top of this one.

---

## Roles

| Component | Owns | Does NOT own |
|---|---|---|
| **Vision Plugin (VP)** | image acquisition, the inspection algorithm, pass/fail per-feature scoring, `is_clear` / `is_OK` arrays, hole position fixes (`locHole`) | trigger timing, motion, bin actuator, part-ID lifecycle |
| **Host (renderer)** | trigger timing (issues camera+light strobe via PLC `M4`), part-ID assignment per cycle, pass/fail interpretation (toss reasons, NG class), bin actuator commands (nozzle suck/blow via `M4`), production-plan walker | image processing, geometry calibration math (delegated to `utils/calibration.ts` but driven from host) |
| **PLC** | motion (`G1`), digital I/O pulses (`M4`), motion-progress synchronization (`WAIT_FOR_TRIGGER_MOTION_PROGRESS`), safety supervisors | inspection, vision, recipes |

The PLC is intentionally **vision-blind**: it pulses the strobe pin and
camera trigger pin on command and does not know the resulting verdict.
The verdict is interpreted entirely on the host.

---

## Channels

The host has **two independent TCP channels**, both managed by
`PluginHello.tsx`:

| Channel | Default endpoint | Wire format | Purpose |
|---|---|---|---|
| **PLC** (`sendTcpMsgPack` / `regTcpMsgCB`) | `192.168.1.70:8125` | msgpack (no length framing) | motion / IO / safety; commands defined in [`protocol.md`](./protocol.md) |
| **Vision Plugin** (`VP_sendTcpMsgPack` / `VP_regTcpMsgCB`) | `localhost:7950` (configurable in UI) | JSON, `;`-delimited | inspection commands and result push |

VP messages have a different envelope (`{ type, cmd_type, ... }`) and an
auto-stamped `id` for request/reply correlation (see [§VP request/reply
commands](#vp-requestreply-commands)). They are **not** subject to the
PLC `protocol.md` schema.

---

## Trigger timing (one inspection)

A single inspection (e.g. side-cam check on a picked part) follows this
exact ordering. The renderer is the orchestrator.

```
Renderer                               PLC                          Vision Plugin
   │                                    │                                │
   │  cmd.G1({Z: inspLocation.Z})       │                                │
   ├──────────────────────────────────▶ │ (move to inspection pose)      │
   │                                    │                                │
   │  waitForSideCheckData()            │                                │
   │  ─ pre-arms _this.SideCheckData_Promise                             │
   │                                    │                                │
   │  camTrig(CAM_Side, CAM_Side_Light0,│                                │
   │          {reset_ms: 5,             │                                │
   │           motion_progress: 1})     │                                │
   ├──────────────────────────────────▶ │                                │
   │                                    │  ─ at motion_progress = 1.0,   │
   │                                    │    PLC pulses both pins HIGH   │
   │                                    │    for 5 ms (auto-reset)       │
   │                                    │  ─ camera triggers on rising   │
   │                                    │    edge, captures frame        │
   │                                    │                                │ ─ VP processes
   │                                    │                                │   image
   │                                    │                                │
   │ ◀──────────────── push: SideCheckID (114500), data: {...}           │
   │  ─ VP_regTcpMsgCB callback fires:                                   │
   │    _this.SideCheckData = data,                                      │
   │    _this.SideCheckData_Promise.resolve(data)                        │
   │                                    │                                │
   │  await waitForSideCheckData() ───▶ resolves                         │
   │  ─ renderer reads is_clear/is_OK                                    │
   │  ─ if NG: push to toss-reasons, route to bin                        │
   │  ─ else: continue to place                                          │
```

Key points:

- The renderer pre-arms the result promise **before** issuing the
  trigger. The result push can race the next motion command.
- `camTrig(camPin, lightPin, {motion_progress})` is a thin wrapper for
  the M4 strobe pulse (defined in `CalibPage.tsx`); when
  `motion_progress` is set, the PLC delays the pulse until the buffered
  motion reaches that fractional progress (0..1) on the leading move.
  This is how trigger-on-the-fly works without bringing the axis to a
  full stop.

---

## Check IDs (push-message routing)

Vision Plugin pushes its results addressed by a numeric ID. The host
pre-registers a callback per ID via `VP_regTcpMsgCB(id, cb)`.

| Constant | ID | Source | Result fields used |
|---|---|---|---|
| `FFeederCheckID` | 104500 | flex-feeder bowl camera | object pose array (x, y, angle) |
| `SideCheckID` | 114500 | side camera (post-pick inspection) | `is_clear[]`, `is_OK[]`, `locHole`, angle/offset measurements |
| `BTMCheckID` | 124500 | bottom camera (post-pick inspection) | `is_clear[]`, `is_OK[]` |
| `TOPCheckID` | 134500 | top camera (slot / placement check) | `is_clear[]`, `is_OK[]`, `locHole` |

These IDs are defined as module-level constants in `CalibPage.tsx`. They
are paired with promise tunnels (`registerPromiseTunnel(id, name)` —
also in `CalibPage.tsx`), which stash the latest pushed data on
`_this.<name>CheckData` and resolve the matching `*_Promise` if a
caller is waiting.

---

## VP request/reply commands

`VP_sendTcpMsgPack(envelope)` is request/reply. The host stamps an
auto-incrementing `id` onto the outbound JSON; the VP echoes it on the
reply, and `PluginHello.tsx` resolves the matching pending promise.
Outbound encoding is `JSON.stringify(envelope) + ";"` over the
localhost:7950 socket.

Required envelope fields: `type` (group), `cmd_type` (subcommand within
the group). All other fields are per-command. Reply shape mirrors the
request `id` plus per-command result fields; no `ack` flag (a thrown
exception is the only failure mode visible to the caller, raised when
the socket isn't connected — see [PluginHello.tsx:784](../../PluginHello.tsx#L784)).

### `type:"TopInsp"`

| `cmd_type` | Request fields | Reply fields | Used at |
|---|---|---|---|
| `save_target` | `t0?: string`, `t1?: string`, `t2?: string` — image filename per slot; `undefined` slots are skipped | implementation-defined ack | [CalibPage.tsx:1253](../../components/CalibPage.tsx#L1253) (production save on NG-pick), [CalibPage.tsx:3043](../../components/CalibPage.tsx#L3043) (operator dev tool) |
| `revisit` | `index: number` (saved-object index, 0..buffer-1); `revisit_idx?: number` (per-object subview index, -1..2); `SL_sens_alpha?: number` (sensitivity override) | re-run inspection result on the saved frame; same shape as a `TOPCheckID` push but inline | [CalibPage.tsx:3034](../../components/CalibPage.tsx#L3034), [CalibPage.tsx:3056](../../components/CalibPage.tsx#L3056), [CalibPage.tsx:3072](../../components/CalibPage.tsx#L3072) (slider drag), [CalibPage.tsx:3076](../../components/CalibPage.tsx#L3076) (arrow key); also bottom-cam variant at [CalibPage.tsx:3002](../../components/CalibPage.tsx#L3002) |
| `BufferSize` | _none_ | number of saved targets currently in VP's buffer | [CalibPage.tsx:3087](../../components/CalibPage.tsx#L3087) |

Notes:

- `save_target` is fire-and-forget from the production path's perspective
  — the host `await`s only to backpressure the socket, not to consume
  the reply.
- `revisit` is the operator-side feedback loop for tuning `SL_sens_alpha`
  on a frozen frame; it does **not** reach the PLC and does **not**
  drive any motion.
- The push-side `*CheckID` constants ([§Check IDs](#check-ids-push-message-routing))
  share the wire but use a **different envelope** (no `cmd_type`,
  addressed by numeric ID) and route via `VP_regTcpMsgCB`, not the
  request/reply promise tunnel.

This section closes [open question #3](#open-questions-deferred-to-w7-phase-2)
at the documentation level. Promoting `{type, cmd_type}` into a typed
builder analogous to [`lib/protocol.ts`](../../lib/protocol.ts) is still
open and would be the next step if the VP command surface grows.

---

## Pass/fail decision flow

The renderer interprets the vision result. Per inspection:

1. `is_clear[i]` — slot `i` is empty / no debris (1 = clear).
2. `is_OK[i]` — slot `i` is OK (1 = OK).
3. The renderer uses these to choose:
   - `findIndex(clear === 1)` → place location for next part.
   - `findIndex(clear === 0 && OK === 0)` → pick location for a part to
     repick (the "revisit" path).
4. NG classification is currently inline in `runAllObjects` via
   `tossReasons` array; class 0/1/2 are emitted into the
   `runinng_checkpoint("NG_COUNT", ...)` stream. This is the spot
   [solidification.md W4 #8](../1-concepts/solidification.md) targets for
   data-driven taxonomy.

---

## Bin actuator (toss path)

The "bin" is the nozzle's blow line, not a separate actuator. To toss:

1. Renderer commands `cmd.G1(...)` to one of `TOSS_LOCATION_0/1/2`
   (currently keyed on NG class but using static module-scope locations
   — see [solidification.md W4 #6](../1-concepts/solidification.md)).
2. Renderer issues `cmd.M4({pin: Nozzle_suck, state: 0})` (suck off)
   then `cmd.M4({pin: Nozzle_blow, state: 1, reset_ms: 20})` (blow
   pulse) to release the part.

The PLC has no concept of "this was a toss." It is a normal move + pulse.

---

## Lifecycle / cleanup

`CalibPage.tsx` registers callbacks in a `useEffect` (lines 184–219)
and unregisters on unmount. There is no explicit reconnect handling on
the VP channel — if the VP socket bounces while a `*_Promise` is
pending, the promise stays unresolved until `delay()` timeouts upstream
trip an abort. This matches PLC-channel behavior pre-W1 and is a known
fragility.

---

## Open questions (deferred to W7 phase 2)

These are not answered by current code — they need a decision before
the contract can be considered complete:

1. **Trigger-timing tolerance.** What's the acceptable jitter between
   PLC pin rise and frame capture? Today nobody measures it.
2. **Frame-capture-to-verdict latency budget.** Currently the renderer
   waits indefinitely on `waitFor*CheckData()`. What should "vision is
   slow" look like — operator alarm, retry, abort?
3. **VP command schema.** ~~Undocumented.~~ **Documented 2026-04-26**
   in [§VP request/reply commands](#vp-requestreply-commands) above.
   Open follow-on: should `{type, cmd_type}` move into a typed builder
   analogous to `lib/protocol.ts`? Defer until the VP command surface
   grows beyond the current three subcommands.
4. ~~Direct vision↔PLC fly-events.~~ **Rejected (user, 2026-04-26):** the
   PLC stays vision-blind. Vision routes through the host; the renderer
   remains the only thing that interprets verdicts and commands the
   bin. Don't reopen this without revisiting the generic-PLC preference.

These are gating questions for [P3 #15](../3-subsystems/plc.md) (AxisGroupSM split).
