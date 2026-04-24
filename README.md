# TCP_UI — tape-and-reel packer control

Electron + React UI plus CODESYS PLC source for a delta-robot
tape-and-reel packer with 4-surface defect and side-projection
dimension inspection.

- **UI / orchestrator** — [`components/`](components/),
  [`hooks/`](hooks/), [`utils/`](utils/), [`lib/`](lib/). Electron
  renderer owns PLC TCP, vision TCP, FlexBowl serial, and the job
  sequence.
- **PLC source** — [`codesys_code/`](codesys_code/) (Structured Text
  for CODESYS 3.5 SP21 Patch 20, SoftMotion + EtherCAT + CiA 402
  servos).
- **PLC round-trip tooling** — [`codesys_scripts/`](codesys_scripts/)
  (Python + IronPython for exporting/importing ST via the CODESYS
  scripting engine over TCP).
- **Vision** — external standalone app, not in this tree. Talks to
  the renderer over TCP+MessagePack.

---

## Docs

All planning and architecture docs live in [`doc/`](doc/):

| File | Purpose |
|---|---|
| [`doc/solidification.md`](doc/solidification.md) | **Start here.** Integrated execution plan — workstreams that cut across PLC + host, phasing, acceptance tests. |
| [`doc/redesign.md`](doc/redesign.md) | Architectural direction — what we're keeping, what we're changing in place, what we explicitly rejected. |
| [`doc/plc.md`](doc/plc.md) | PLC source map, machine sequence, timing diagram, review items (P0–P3, A1–A6), progress log. |
| [`doc/calibpage.md`](doc/calibpage.md) | Renderer main-loop (`CalibPage.tsx`) cleanup items. |
| [`doc/msgpack.md`](doc/msgpack.md) | PLC-side MessagePack library review — findings, ranked fixes, phasing. |
| [`doc/scripting.md`](doc/scripting.md) | CODESYS scripting round-trip: TCP warm-session, export/import flow, encoding rules, tooling gotchas. |

Operator-facing material: [`OPERATION_GUIDE.md`](OPERATION_GUIDE.md).

---

## Conventions

- **Update docs alongside code.** When a tracked item (P-number,
  A-number, workstream step) lands, mark its **Status** column in
  the relevant doc and log the change in "Recently completed."
- **New findings go into the appropriate doc.** PLC items →
  `doc/plc.md`. Renderer items → `doc/calibpage.md`. Cross-cutting
  → `doc/solidification.md`.
- **Cycle time is the acceptance gate for any refactor.** See
  [`doc/plc.md`](doc/plc.md) §Timing diagram.

---

## Build

```
npm install
npm run build       # Vite library build
```

See [`package.json`](package.json) for the full script list.
