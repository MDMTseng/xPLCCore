# doc/ — reading tour

Newcomer's guided tour through the project docs. Docs are grouped into
four tiers by reading order:

| Tier | Folder | What's in it | When to read |
|---|---|---|---|
| 1 | [`1-concepts/`](1-concepts/) | System architecture, design rationale, invariants, execution plan | First — to build the mental model |
| 2 | [`2-contracts/`](2-contracts/) | Wire protocols / interface contracts | When you'll touch a wire / API surface |
| 3 | [`3-subsystems/`](3-subsystems/) | Per-area deep dives (PLC, renderer cycle, conveyor, ring buffers) | When you'll work on that area |
| 4 | [`4-dev/`](4-dev/) | Dev tooling (CODESYS scripting + RPC daemon) | When you'll push PLC code |

For the high-level "what is this project" overview, read
[`../README.md`](../README.md) (5 min) and then
[`../PROJECT.md`](../PROJECT.md) (15 min) before diving in here.

---

## Recommended reading paths

### Path A — "I just joined, give me 90 minutes"

1. [`../README.md`](../README.md) — quick start + wire cheat sheet
2. [`../PROJECT.md`](../PROJECT.md) — full overview + capability matrix
3. [`1-concepts/architecture.md`](1-concepts/architecture.md) — integrated component map
4. [`1-concepts/solidification.md`](1-concepts/solidification.md) (skim §Workstreams) — what's been done, what's in flight
5. [`2-contracts/protocol.md`](2-contracts/protocol.md) (skim) — the PLC wire surface

You now know enough to follow conversations and find your way around.

### Path B — "I'm working on the PLC"

1. Path A first
2. [`1-concepts/coupling_invariants.md`](1-concepts/coupling_invariants.md) — what must move together across layers
3. [`3-subsystems/plc.md`](3-subsystems/plc.md) — source map, FSM, timing, items P0–P3 / A1–A6
4. [`4-dev/scripting.md`](4-dev/scripting.md) — how to push code via the daemon
5. [`3-subsystems/ringbuf-bugs.md`](3-subsystems/ringbuf-bugs.md) — pitfalls when touching the ring buffers
6. [`2-contracts/msgpack.md`](2-contracts/msgpack.md) — PLC-side msgpack lib review

### Path C — "I'm working on the renderer / UI"

1. Path A first
2. [`3-subsystems/calibpage.md`](3-subsystems/calibpage.md) — the main calibration loop
3. [`2-contracts/vision_contract.md`](2-contracts/vision_contract.md) — how vision data arrives
4. [`2-contracts/protocol.md`](2-contracts/protocol.md) — full PLC wire reference

### Path D — "I'm working on conveyor pick"

1. Path A first
2. [`3-subsystems/conveyor_pick.md`](3-subsystems/conveyor_pick.md) — Phase 4 (PLC) + Phase 6 (renderer) architecture
3. [`1-concepts/solidification.md`](1-concepts/solidification.md) (§Workstreams) — overall plan context
4. [`3-subsystems/plc.md`](3-subsystems/plc.md) — for the PLC-side details

### Path E — "I'm reviewing or planning architectural change"

1. Path A first
2. [`1-concepts/redesign.md`](1-concepts/redesign.md) — what we keep, change in place, rejected (and why)
3. [`1-concepts/solidification.md`](1-concepts/solidification.md) — full workstream tracker
4. [`1-concepts/coupling_invariants.md`](1-concepts/coupling_invariants.md) — invariants that constrain refactors

---

## Index — all docs by tier

### Tier 1 — concepts ([`1-concepts/`](1-concepts/))

| Doc | What you'll learn |
|---|---|
| [`architecture.md`](1-concepts/architecture.md) | Integrated snapshot — components, concurrency, safety contract, diagnostic surfaces |
| [`redesign.md`](1-concepts/redesign.md) | Architectural direction — keep / change in place / rejected, and why |
| [`coupling_invariants.md`](1-concepts/coupling_invariants.md) | Cross-layer invariants that MUST move together (enum ordinals, packet layouts, etc.) |
| [`solidification.md`](1-concepts/solidification.md) | Integrated execution plan — workstreams W1–W5, acceptance tests, phasing, status |

### Tier 2 — contracts ([`2-contracts/`](2-contracts/))

| Doc | What you'll learn |
|---|---|
| [`protocol.md`](2-contracts/protocol.md) | Authoritative PLC ↔ renderer wire spec (msgpack TCP 8125): every command, reply, push event, NAK code |
| [`vision_contract.md`](2-contracts/vision_contract.md) | Renderer ↔ vision plugin protocol (JSON TCP 7950): per-camera IDs, trigger timing, pass/fail interpretation |
| [`msgpack.md`](2-contracts/msgpack.md) | PLC-side MessagePack library review — findings, ranked fixes, regression-test mapping |

### Tier 3 — subsystems ([`3-subsystems/`](3-subsystems/))

| Doc | What you'll learn |
|---|---|
| [`plc.md`](3-subsystems/plc.md) | PLC source map, state machine, timing diagram, review items, progress log |
| [`conveyor_pick.md`](3-subsystems/conveyor_pick.md) | Conveyor pick-and-place: PCS_1 kernel tracking, FlyEvent BIND, window-exit fault |
| [`calibpage.md`](3-subsystems/calibpage.md) | Renderer main-loop cleanup items (the `runAllObjects` cycle) |
| [`ringbuf-bugs.md`](3-subsystems/ringbuf-bugs.md) | Known ring-buffer pitfalls (catalogued during `reMP_info_ridx` / `minfo_buf_ridx` hardening) |
| [`softmotion_snapshot.yaml`](3-subsystems/softmotion_snapshot.yaml) | SoftMotion library snapshot (data file, not prose) |

### Tier 4 — dev tooling ([`4-dev/`](4-dev/))

| Doc | What you'll learn |
|---|---|
| [`scripting.md`](4-dev/scripting.md) | CODESYS scripting round-trip — warm-session TCP, RPC daemon, export/import flow, IronPython gotchas |

---

## Conventions for updating these docs

- **Update docs alongside code.** When a tracked item lands, mark its
  **Status** column in the relevant doc.
- **Per-area docs own per-area findings.** PLC items →
  `3-subsystems/plc.md`. Renderer cycle items →
  `3-subsystems/calibpage.md`. Cross-cutting → `1-concepts/solidification.md`.
- **New capability → new tier-3 doc + reading-tour link here + index in
  PROJECT.md.** Keep this README the single source of truth for
  "where do I find docs about X?".
- **Code comments referencing docs use the full `doc/<tier>/<file>.md`
  path** so a grep finds both prose and code citations at once.
