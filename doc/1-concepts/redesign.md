# Redesign / in-place improvement plan

Scope: **keep the current Electron + React + CODESYS architecture**;
improve it in place. No backend process extraction — the renderer
keeps owning all I/O. Chosen after a design discussion where a
split-backend option was considered and rejected as too many moving
parts for a one-person project.

This file is the working list for the larger-scale improvements.
Point-fix issues (P0–A6) live in
[`plc.md`](../3-subsystems/plc.md); renderer main-loop
cleanup lives in [`calibpage.md`](../3-subsystems/calibpage.md).
This one is for things that span both sides and don't fit in either.

---

## What we're keeping

- **CODESYS + SoftMotion + EtherCAT** for motion. Not changing.
- **Electron + React + Ant + Vite** for UI + control. Not changing.
- **MessagePack over TCP** for PLC ↔ host.
- **Standalone vision app** (separate process, talks TCP).
- **Renderer-as-orchestrator** model. Renderer owns PLC socket,
  vision socket, FlexBowl serial, and the job sequence.
- **PLC-as-authority** design intent: PLC enforces invariants so the
  renderer can crash and recover.

## What we're changing

The changes themselves — PLC robustness, renderer cleanup, state
machine, observability, i18n, config, protocol schema — are organized
into seven workstreams in
[`solidification.md`](./solidification.md) with dependencies, phasing,
and acceptance tests. **That is the authoritative execution plan;
don't duplicate it here.**

This file only captures the strategic direction: what we keep,
what we reject, and open questions.

---

## What we explicitly rejected

| Option | Why rejected |
|---|---|
| Separate Node backend process | "Too many moving parts" for one-person maintenance; stated user preference. |
| Move control to Electron main process | Same reason + user stated "renderer is fine, I can recover via PLC." |
| Python backend + TS frontend | Vision is already its own app; no language-mismatch benefit. |
| gRPC / protobuf | Overkill at this scale; MsgPack is enough. |
| From-scratch PLC rewrite | Current ST is 80% there; iterate via P0–A6. |
| Split AxisGroupSM now (P3 #15) | Premature; wait until state machine + vision contract clarify the right boundaries. |

---

## Open questions for later

- **Cover-tape sealing** — not in this code. Is it a separate
  upstream/downstream process? Does the machine need to coordinate
  with it? (Flagged as unknown in machine-sequence review.)
- **Reel-end / reel-change** — operator procedure? Auto-detect? UI
  prompt? Currently undefined.
- **Production plan semantics** — the negative/positive-array
  encoding works but isn't documented. Formalize before recipe
  system lands.
- **Safety model** — E-stop path, light curtain, door interlocks.
  Assumed handled by machine wiring + safety relay; confirm before
  shipping as a product.
