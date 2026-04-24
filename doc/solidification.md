# Integrated solidification plan

The machine is one system; half-a-fix on one side doesn't count. This
file organizes the work by **outcome across both sides**, not by
"PLC cleanup" vs. "renderer cleanup."

Per-side detail still lives in:

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
| Host | Delete host-side checks that the PLC now enforces | — | Open |

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

### W2 — Material-flow autonomy

**Outcome:** low-material detection and recovery don't depend on a
healthy renderer loop.

| Side | Item | Ref |
|---|---|---|
| PLC | Watch `ReelLacking` input; auto-transition to `WaitingForMaterial` at safe pause point | A5 (PLC half) |
| PLC | `FEED` command, only accepted in `WaitingForMaterial`; transitions back to `Ready` on success | A5 (PLC half) |
| PLC | Timeout in `WaitingForMaterial` — stays parked, doesn't fault | A5 |
| Host | Delete renderer-side input watchdog loop ([CalibPage.tsx:809](../components/CalibPage.tsx#L809)); replace with "when PLC state = WaitingForMaterial, send FEED" | solidify.md #5 (supersedes) |
| Host | Show `WaitingForMaterial` clearly in UI with operator-clear error strings | depends on W5 |

**Acceptance tests:**

- Physically block the feeder output while running; machine pauses
  at a clean stop, UI shows "waiting for material," no motion
  fault.
- Clear blockage; UI confirms `FEED` issued; machine resumes; no
  loss of reel position or `movement_id` continuity.

**Dependencies:** W1 (needs heartbeat + reconnect before we can
trust state to be recoverable).

---

### W3 — Protocol solidification

**Outcome:** every command has a typed builder on the host and a
known schema on the PLC. Schema drift is compile-time on one side and
runtime-NAK on the other.

| Side | Item | Ref |
|---|---|---|
| Shared | `protocol.md` — authoritative list of commands, params, return shapes. Lives in repo root. | new |
| Shared | Version field in envelope (both send + check) | used by W1 |
| Host | `protocol.ts` — typed builders (`cmd.G1({X, Y, Z, A})`, etc.) for every command in `protocol.md`. Replace renderer's hand-built MsgPack literals. | new, complements solidify.md #2 |
| PLC | Commands listed in `protocol.md` ↔ ST dispatcher in sync. Walk the dispatch chain, diff against `protocol.md`, fix mismatches. | P3 #13 (partially) |
| PLC | Dispatcher NAKs unknown commands cleanly with a diagnostic response (not silent drop). | new |
| PLC | MsgPack library correctness + symmetry fixes (Phase A/B in [`msgpack.md`](./msgpack.md)). Needed before the protocol tightens — today `map 32` / `array 32` / `bin` / `ext` are silent footguns on the read side. | msgpack.md Phase A–B |

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
| Host | Extract named step functions (triggers return Promises) | solidify.md #1 |
| Host | `trig(cam, light, opts)` helper wrapping M4 bitmask | #2 |
| Host | Type `_this` ref | #3 |
| Host | `Number.isNaN` cleanup | #4 |
| Host | Input watchdog → standalone (interim before W2 deletes it) | #5 |
| Host | Config extraction (`IO_Pins`, check IDs, locations) | #6 |
| Host | Delete commented-out experiments | #7 |
| Host | Data-drive NG classification | #8 |
| Host | State machine rewrite | #9 |
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
| PLC | Expose error detail in `GET_MACHINE_STATE` (axis that faulted, SMC error code). Today errors are "Error state" with no categorization. | follow-on from W1 A4 |
| PLC | Optional: push structured events on state change instead of host polling. Not required; cheaper than every-N-ms polls. | optional |

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
| Shared | `vision_contract.md` — who decides pass/fail, who owns part-ID, who commands the bin actuator, trigger timing tolerances. | codesys_code/README.md A6 |
| Host | Today's flow documented as-is first. | new |
| Future | Possibly direct vision ↔ PLC M4 triggers for sub-cycle latency. Decide based on measurement. | deferred |

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
