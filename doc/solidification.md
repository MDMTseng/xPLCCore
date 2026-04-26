# Integrated solidification plan

The machine is one system; half-a-fix on one side doesn't count. This
file organizes the work by **outcome across both sides**, not by
"PLC cleanup" vs. "renderer cleanup."

Per-side detail still lives in:

- [`architecture.md`](./architecture.md) — **integrated snapshot of
  what the system is right now** (component map, concurrency, safety
  contract, generic-PLC principle, diagnostic surfaces). Read this
  first if you're new to the tree.
- [`plc.md`](./plc.md) — PLC item
  tables (P0–P3, A1–A6) + machine sequence + timing diagram.
- [`calibpage.md`](./calibpage.md) — renderer
  main-loop cleanup (items #1–#9).
- [`redesign.md`](./redesign.md) — scope / architectural direction.

This file is the **execution plan**: what gets done together, in
what order, and how we know it's done.

---

## Guiding principles

1. **Ship workstreams, not sides.** A workstream is done when the
   host and PLC agree on the new behavior and the acceptance test
   passes. Don't ship the ST half of a heartbeat without the TS
   half.
2. **Cycle time is the acceptance gate.** Any workstream that
   measurably slows the machine is wrong until proven otherwise.
   See [timing diagram](./plc.md#timing-diagram-one-steady-state-cycle).
3. **Invariants belong on the PLC.** Whenever host and PLC both
   check something, delete the host check. The host should only
   *display* invariants, not enforce them.
4. **Renderer cleanup is behavior-preserving until the state
   machine lands.** Cut-and-paste first, change meaning second.

---

## Workstreams

### W1 — Host-authority safety contract

**Outcome:** the renderer can crash, reload, or hang for minutes, and
the machine stays in a known-safe state and resumes cleanly.

| Side | Item | Ref | Status |
|---|---|---|---|
| PLC | Verify motion commands rejected in Error state | [plc.md A1](./plc.md) | **Done 2026-04-24** — per-packet NAK with `err='group_not_ready'`. |
| PLC | `BLOCK_FOR_*` timeout parameter; NAK on timeout | A2 | **Done 2026-04-24** — optional `timeout_ms` on both BLOCK handlers; NAK with `err='block_timeout'`. Fixed UI↔PLC name-mismatch by aliasing `WAIT_FOR_MOTION_STOP`. |
| PLC | Heartbeat watchdog; on miss → halt motion, drop fly events, enter recoverable idle | A3 (PLC half) | **Done 2026-04-24** — Supervisor in `AxisGroupSM` transitions FSM to Error when `RuntimeMs - LastUiPingMs > UI_HEARTBEAT_TIMEOUT_MS` (5000ms, gated to Ready state). Motion winds down via Error-state `GroupDisable + Reset` cycle; counter on `GVL.UiHeartbeatStaleCount`. |
| PLC | Expose `GET_MACHINE_STATE` — homed flag, current state, last accepted `movement_id`, last completed `movement_id`, fault info | A4 (PLC half) | **Done 2026-04-24** — SYS/`GET_MACHINE_STATE` returns `{st, st_str, err_src, err_id, motion_buffer_size, movement_id, runtime_ms}`. Follow-on (from [plc.md A6/follow-on](./plc.md)): per-axis fault detail. |
| PLC | Protocol version field enforced; NAK mismatched version | new, see W3 | **Done 2026-04-25** — PLC reads `protocol_version` off every packet; if present and != `GVL.PROTOCOL_VERSION` (currently 1), packet is NAK'd pre-dispatch with `err='protocol_version_mismatch', err_got=<got>`. Absent is legacy-allowed. Host half: `PluginHello.sendTcpMsgPack` stamps `protocol_version: 1` on every outbound packet. Counter: `GVL.ProtocolVersionMismatchCount`. |
| Host | Heartbeat tx (every N ms, interval TBD — probably 200 ms) | A3 (host half) | **Done 2026-04-24** — 1s interval, 3.5s stale threshold, exposed via `get_heartbeat_status` harness action. Interval can be tuned by adjusting `HEARTBEAT_INTERVAL_MS` / `HEARTBEAT_STALE_MS` in `PluginHello.tsx`. |
| Host | On reconnect: query `GET_MACHINE_STATE`, reconcile renderer state, resume or prompt operator | A4 (host half) | **Done 2026-04-24** — snapshot auto-fetched on reconnect, cached in `lastMachineSnapshotRef`, broadcast on `window` as `plc:machine-state` event. `ControlPage` force-routes tab to `Welcome` when `st_str != 'Ready'` or `coord_set=false`. Reconcile result exposed via `get_tab` harness action as `lastReconcile`. |
| Host | Delete host-side checks that the PLC now enforces | — | **Done 2026-04-26** — `init_plc_motion` leans on `CoordSystemConfigured` being PLC-enforced (no host-side coord guard); `BLOCK_FOR_MOTION_STOP` aliases `WAIT_FOR_MOTION_STOP` so the host doesn't need a name shim; group-not-ready handling moved to PLC NAK. **Audit sweep (2026-04-26):** grepped for `coord_set` / `CoordSet` / `isReady` / `group_ready` / `GroupEnabled` / connection guards. Findings: all remaining host state checks are either (a) UI affordance (`ControlPage.tsx` tab disable + reconcile-to-Welcome banner — display-only, doesn't replace PLC enforcement), (b) host driving the start sequence forward (`init_plc_motion`'s event walker), (c) renderer-internal locks (`isRunning`, `BurnRunning`, `calibParams==null`), or (d) liveness on the host's own sockets (`tcpConnected`, `tcp2Status`). No dead enforcement-duplicate guards remain. |

**Acceptance tests:**

- Kill the renderer mid-cycle (Task Manager). PLC enters recoverable
  idle within `heartbeat_timeout_ms`; no drive fault.
- Relaunch renderer. It reads PLC state, matches displayed state to
  actual state, and either offers "resume" or routes to a safe
  restart.
- Send a `G1` with wrong `protocol_version` — PLC NAKs it, doesn't
  move.
- `BLOCK_FOR_DIGITAL_INPUT` with timeout of 500 ms and the input
  never fires — PLC NAKs after 500 ms, doesn't park.

**Dependencies:** none. Start here.

---

### W2 — Material-flow autonomy ~~(deferred)~~ **Rejected 2026-04-25**

**Status: rejected.** The user wants the PLC to stay generic — motion,
IO, safety supervisors only. Material-flow is domain/business logic
and belongs in the renderer. The renderer-side watchdog at
[CalibPage.tsx:809](../components/CalibPage.tsx#L809) stays.

Tradeoff accepted: if the renderer dies, material flow stops. The
W1 host-authority safety contract still ensures the *machine* stays
safe (PLC's heartbeat supervisor halts motion when UI goes silent),
which is what matters. "Material won't be fed without a live UI" is
a known operational constraint, not a safety issue.

If we ever revisit, the original sketch was:
- PLC watches `ReelLacking` input, auto-transitions to a
  `WaitingForMaterial` state at a safe pause point.
- New `FEED` command, only accepted in `WaitingForMaterial`,
  transitions back to `Ready` on success.
- Renderer becomes the FEED issuer, not the watchdog.

Don't pick this up without revisiting the generic-PLC preference
first.

---

### W3 — Protocol solidification

**Outcome:** every command has a typed builder on the host and a
known schema on the PLC. Schema drift is compile-time on one side and
runtime-NAK on the other.

| Side | Item | Ref |
|---|---|---|
| Shared | [`protocol.md`](./protocol.md) — authoritative list of commands, params, return shapes, error strings, push events. | **Done 2026-04-26** |
| Shared | Version field in envelope (both send + check) | **Done 2026-04-25** (W1 A5) |
| Host | [`lib/protocol.ts`](../lib/protocol.ts) — typed builders (`cmd.G1({X, Y, Z, A})`, etc.) for every command in `protocol.md`. | **Done 2026-04-26** — module published, all current commands covered. Builder return type now carries a phantom reply-shape (`Envelope<R>`); opt-in `send<R>(fn, env)` helper recovers typed replies (e.g. `await send(sendTcpMsgPack, cmd.GetMachineState())` → `MachineState`). Plain `await sendTcpMsgPack(cmd.X(...))` calls continue to work unchanged. |
| Host | Replace every `sendTcpMsgPack({...literal...})` call with `cmd.X(...)`. Grep for raw MsgPack object literals returns empty. | **Done 2026-04-26** — all active call sites migrated across `CalibPage.tsx`, `MiscControlsPage.tsx`, `OperationPage.tsx`, `JoggingPad.tsx`, `PluginHello.tsx`, `DiagPanel.tsx`. Remaining literal matches are in JSX block comments only. `tsc --noEmit` clean. |
| PLC | Commands listed in `protocol.md` ↔ ST dispatcher in sync. | **Done 2026-04-26** — table cross-checked against `AxisGroupSM.st` while writing protocol.md. |
| PLC | Dispatcher NAKs unknown commands cleanly with a diagnostic response (not silent drop). | **Done 2026-04-25** (`missing_type_field` for missing `type`; generic `ack:false` for unknown `cmd`). |
| PLC | MsgPack library correctness + symmetry fixes (Phase A/B in [`msgpack.md`](./msgpack.md)). | **Done 2026-04-26** — UnpackNext/SkipValue bounds-checked, PackLINT compact. |

**Acceptance tests:**

- Every `sendTcpMsgPack({...})` call in the codebase is replaced by
  a `cmd.X(...)` call. Grep for raw MsgPack object literals returns
  empty.
- Adding a new optional field to a command doesn't require touching
  the PLC (additive compatibility confirmed).
- Sending a command not in `protocol.md` from the host is a
  compile-time error.

**Dependencies:** none, but do after W1's version field is in.

---

### W4 — Renderer main-loop readability

**Outcome:** `runAllObjects` is pipelining-preserving named stages + a
state machine. Cycle time unchanged.

| Side | Item | Ref |
|---|---|---|
| Host | Extract named step functions (triggers return Promises) | solidify.md #1 — Open. Structural refactor of ~2400-line `runAllObjects`; needs cycle-time baseline on hardware before pulling stages out. |
| Host | `trig(cam, light, opts)` helper wrapping M4 bitmask | #2 — **Done 2026-04-26**. `camTrig(camPinIdx, lightPinIdx, opts)` in `CalibPage.tsx`; 4 static cam+light sites migrated. 3 dynamic-mask sites compose pin masks at runtime, left in-place. |
| Host | Type `_this` ref | #3 — **Done 2026-04-26**. `RunCtx` type at `CalibPage.tsx`; covers loop control, vision-handoff promises, production-plan walker, throughput counters, revisit state, jog helper. Index signature retained for ad-hoc debug fields. |
| Host | `Number.isNaN` cleanup | #4 — **Done 2026-04-26**. Replaced 5 `x==x`/`x!=x` patterns with explicit `Number.isNaN(x)`. |
| Host | Input watchdog → standalone (interim before W2 deletes it) | #5 — **Done 2026-04-26**. Extracted from anonymous IIFE into named local `inputWatchdog()`. Same call shape (`inputWatchdog()` invoked once at start of run). |
| Host | Config extraction (`IO_Pins`, check IDs, locations) | #6 — **Done 2026-04-26**. Module-scope: `IO_Pins as const`, `FFeederCheckID/SideCheckID/BTMCheckID/TOPCheckID`, `SAFE_Z`, `OBJECT_HEIGHT`, `PICK_Z_LIFT`, `INSP/SLOT/TOSS_0/1/2/WAIT_FLEXFEEDER` locations under shared `XYZ` type. |
| Host | Delete commented-out experiments | #7 — Open. Deferred: repo not under version control, bulk deletion is irreversible; needs explicit go-ahead. |
| Host | Data-drive NG classification | #8 — Open. Needs NG-taxonomy decision (which codes, retry vs. abort vs. operator-prompt). Blocks W5 SQLite schema. |
| Host | State machine rewrite | #9 — Open. Gates Phase 4. Needs cycle-time baseline on hardware as the acceptance criterion. |
| PLC | Nothing direct. But stages #1 and #8 may expose gaps in what the PLC currently reports — add readbacks as needed. | — |

**Acceptance tests:**

- Baseline cycle time captured before any change.
- After each step, cycle time delta ≤ 2% (noise band).
- Full production run (N parts) produces the same NG counts and
  types as baseline run with same input.

**Dependencies:** W1 is nice-to-have before the state-machine rewrite
(#9), because the state machine's reconcile-on-reconnect path needs
W1.

---

### W5 — Operator-facing observability

**Outcome:** operator and developer can see what the machine is
doing, what it did, and why it rejected parts. No more guessing.

| Side | Item | Ref |
|---|---|---|
| Host | SQLite (better-sqlite3) for part history — one row per cycle, timestamps, NG reason ID, measurements, saved image filenames. | new |
| Host | Structured logging wrapper (cycle / state / level tags) → JSON lines on disk. Kill `console.log`. | new |
| Host | Live counters panel: pack count, NG by reason, recent cycle-time trend. | new |
| Host | i18n: Chinese strings in control flow ([CalibPage.tsx:854](../components/CalibPage.tsx#L854)) become IDs; UI translates. | new |
| PLC | Expose error detail in `GET_MACHINE_STATE` (axis that faulted, SMC error code). Today errors are "Error state" with no categorization. | **Partial 2026-04-25** — added `axes_err_mask` (bit i = axis i `bError`) and `axes_state` (packed `nAxisState` per axis) to the SYS/`GET_MACHINE_STATE` reply. UI can now identify which axis faulted. **Open follow-on:** SMC error code per axis — `nErrorID`/`LastError` are not exposed on this DS402 drive type (verified via probe); needs either drive-specific SDO reads or per-axis `MC_ReadAxisError` FB instances. |
| PLC | Optional: push structured events on state change instead of host polling. Not required; cheaper than every-N-ms polls. | **Done 2026-04-25** — `ST_CHG`, `COORD_SET`, `MOVE_DONE` server-push events emitted into `reMP_info_ridx` with the same envelope (`kind:'event', name, ..., runtime_ms`). Host dispatches via `window` `plc:event`. |
| PLC | `SYS/GET_DIAG` — single-call dump of comm-stability counters for UI dashboard | **Done 2026-04-26** — 17-field reply (drop/NAK/reset counters + `ping_max_gap_ms`). Resettable via `SYS/RESET_DBG_INFO`. |
| Host | UI diagnostic panel that polls `GET_DIAG` and surfaces drop/NAK/reset counters + Δ/poll | **Done 2026-04-26** — `components/DiagPanel.tsx` mounted at the bottom of the Welcome tab (MiscControlsPage). Toggleable poll (500ms / 1s / 2s / 5s), warn-coloring on non-zero drop/NAK/reset rows, manual refresh + reset-counters buttons. |

**Acceptance tests:**

- Operator can answer "how many parts did we pack today, and how
  many NG, by reason" from the UI without opening logs.
- Developer can answer "what happened in cycle 4193" from SQLite +
  log files.
- A renderer crash loses at most the current in-flight cycle's
  record; everything prior is durable.

**Dependencies:** W4 #8 (data-driven NG classification) makes the
NG-reason taxonomy concrete, which SQLite needs. Do #8 first.

---

### W6 — Recipe system

**Outcome:** the machine runs different part types without code
changes. Config and recipe are separate concerns.

| Side | Item | Ref |
|---|---|---|
| Host | Recipe schema (pick height, inspection thresholds, placement angle bias, per-part tolerances). Stored in SQLite. | new |
| Host | UI to pick / edit / export recipe. | new |
| Host | Calibration data per recipe (today `calib.json` is global). | new |
| PLC | Nothing directly. Confirm that no ST code assumes part geometry; all geometry comes from host. | audit |

**Acceptance tests:**

- Two recipes A and B; switching between them without restart
  produces correct parts for each.
- Recipe export/import round-trip is lossless.

**Dependencies:** W4 (state machine needs to exist before adding
recipe-switching states), W5 (SQLite).

---

### W7 — Vision contract clarification

**Outcome:** vision ↔ host ↔ PLC responsibilities are documented.
Whether vision stays host-routed or moves to direct PLC fly-event
integration is an explicit decision.

| Side | Item | Ref |
|---|---|---|
| Shared | [`vision_contract.md`](./vision_contract.md) — who decides pass/fail, who owns part-ID, who commands the bin actuator, trigger timing tolerances. | **Phase-1 done 2026-04-26** — as-is documented (channels, IDs, trigger timing diagram, pass/fail interpretation, bin path). Open questions section enumerates what's still unanswered. |
| Host | Today's flow documented as-is first. | **Done 2026-04-26** — see vision_contract.md sections 1–6. |
| ~~Future~~ | ~~Possibly direct vision ↔ PLC M4 triggers for sub-cycle latency.~~ | **Rejected 2026-04-26** — PLC stays vision-blind; vision must route through the host. Same generic-PLC reasoning as W2. |

**Acceptance tests:**

- Document exists, reviewed, agreed. That's it for phase 1.
- Timing measurements captured for current trigger → image →
  verdict path so a future change has a baseline.

**Dependencies:** none. Can be written anytime. Blocks P3 #15
(AxisGroupSM split) — don't split until the contract is clear.

---

## Phasing (what to ship in what order)

```
Phase 1  ──  W1 (safety contract)           ◄── foundation
Phase 2  ──  W3 (protocol)  ─────────────┐
             W4 #1–#7 (mechanical cleanup)│  in parallel
                                         │
Phase 3  ──  W2 (material autonomy)  ────┘
             W4 #8 (NG classification)
             W5 (observability + i18n)
             W7 (vision contract doc — write anytime)

Phase 4  ──  W4 #9 (state machine rewrite)
             
Phase 5  ──  W6 (recipes)
             P3 #15 (AxisGroupSM split, if still wanted)
```

**Phase 1 is load-bearing.** Nothing else in phase 2+ is trustworthy
until the renderer can actually crash safely.

**Phase 2 is two tracks in parallel** because protocol work and
mechanical cleanup don't collide.

**Phase 3** converges them and makes the machine visible to the
operator.

**Phase 4** is the state-machine rewrite that the previous phases
made safe and possible.

**Phase 5** is the payoff: multi-recipe production on a maintainable
codebase.

---

## Cross-cutting rules

- **One workstream, one branch, one PR** where possible. Mixing
  workstreams in one commit makes bisecting cycle-time regressions
  hard.
- **Cycle time captured before every phase**, compared after.
  Regression ≥ 2% blocks the merge until understood.
- **Update the sub-docs as you go.** When a P/A item lands, mark it
  Fixed in [`plc.md`](./plc.md) *and*
  reference the workstream here. Keep one source of truth per item.
- **This file is the index.** Don't duplicate item detail here; link
  out.
