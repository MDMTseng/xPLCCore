# PROJECT — xPLCCore

Complete project overview as of 2026-06-21. Single-stop "what is this,
what does it do, where is everything, what state is each piece in." For
focused per-area detail follow the links into [`doc/`](doc/).

The [`README.md`](README.md) covers the wire protocol and quick-start;
this doc covers **scope, capabilities, current status, and how the
pieces compose**.

---

## What this is

xPLCCore is the **renderer + PLC firmware** half of a delta-robot
tape-and-reel packer. The product line:

- **Delta robot** with SoftMotion SpiderR kinematics (3 prismatic
  joints, optional C-axis), EtherCAT + CiA402 drives.
- **4-surface defect inspection** (FFeeder, Side, BTM, TOP cameras).
- **Side-projection dimensioning** for outline conformance.
- **FlexBowl feeder** over serial.
- **Conveyor pick-and-place** (in progress) — track moving objects on
  a belt, pick at speed, place in the carrier tape.

This repo is one piece of the system. The Vision plugin is a separate
external app communicating over TCP/JSON; the Electron host shell that
mounts our renderer is also out of scope.

---

## What it can do today

| Capability | Status | Where |
|---|---|---|
| TCP/msgpack PLC link with heartbeat, reconnect snapshot, server-push events | ✅ Production | [`PluginHello.tsx`](PluginHello.tsx), [`AxisGroupSM.st`](codesys_code/Application/APPs/AxisGroupSM/) |
| FSM safety contract (UnInited → Powering → ... → Ready / Error, no silent drops) | ✅ Production | [`doc/plc.md`](doc/3-subsystems/plc.md), [`doc/architecture.md`](doc/1-concepts/architecture.md) |
| Coord gate (G1/G2/G3 require SetCoord0/1) | ✅ Production | PLC, A2 invariant |
| Heartbeat watchdog (UI 1s → stale 3.5s → PLC trip 5s) | ✅ Production | A3 invariant |
| `GET_MACHINE_STATE` reconnect snapshot | ✅ Production | A4 invariant |
| Protocol version enforcement | ✅ Production | W3 |
| Operator UI (Welcome / Operation / Calib / MiscControls / Control) | ✅ Production | [`components/`](components/) |
| `CalibPage` calibration loop (FFeeder check, Side / BTM / TOP checks, toss routing) | ✅ Production | [`doc/calibpage.md`](doc/3-subsystems/calibpage.md), [`doc/vision_contract.md`](doc/2-contracts/vision_contract.md) |
| FlexBowl serial control | ✅ Production | [`SerialManager.tsx`](SerialManager.tsx) |
| CODESYS RPC daemon (replaces inbox-watcher) | ✅ Production | [`codesys_scripts/rpc.py`](codesys_scripts/), [`doc/scripting.md`](doc/4-dev/scripting.md) |
| FlyEvent scheduler (DistanceTrigger, PulseTrigger, MotionProgressTrigger) | ✅ Production | PLC `ProcessMotionPacket.st` |
| Diagnostic ring buffers (reMP_info ring 32 slots, minfo_buf 6 slots) | ✅ Production | [`doc/ringbuf-bugs.md`](doc/3-subsystems/ringbuf-bugs.md) |
| Build provenance stamping (commit SHA + timestamp into GVL on every push) | ✅ Production | `codesys_scripts/stamp_build.py` |
| Conveyor pick — kernel-native belt tracking (PCS_1 + `MC_TrackConveyorBelt`) | ✅ Phase 4 done | [`doc/conveyor_pick.md`](doc/3-subsystems/conveyor_pick.md) |
| Conveyor pick — FlyEvent COORD1_BIND + window-exit fault | ✅ Phase 4 step 3 done | [`flyevent_coord1_bind` memory](.claude/projects/.../memory/flyevent_coord1_bind.md) |
| Conveyor pick — renderer orchestrator (`runConveyorPick`) | 🚧 Phase 6 step 1 (skeleton, no UI) | [`orchestrator/conveyorPick.ts`](orchestrator/conveyorPick.ts) |
| Real EC encoder for belt | ⏳ Phase 0 not started (synthetic pulse in use) | — |
| Vision → bind adapter on renderer | ⏳ Phase 6 step 3 pending | — |
| 4hr fuzz baseline (240min @25pkt/s clean) | ✅ Captured 2026-06-17 | `plc_4hr_fuzz_baseline` memory |

---

## System architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                  Electron host shell  (out of repo)                  │
│  loads xPLCCore as a library + mounts <PluginHello/> in its window   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ React component tree
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ xPLCCore renderer  (this repo)                                       │
│                                                                      │
│  PluginHello.tsx — TCP socket owner                                  │
│    • msgpack frame + reconnect + heartbeat (1s ping)                 │
│    • protocol_version stamping                                       │
│    • on reconnect: GET_MACHINE_STATE → window 'plc:machine-state'    │
│    • inbound push events → window 'plc:event'                        │
│    • harness registry (test action API)                              │
│                                                                      │
│  Pages                                                               │
│    • ControlPage — shell, reconcile router                           │
│    • CalibPage   — main inspection loop (runAllObjects)              │
│    • OperationPage / MiscControlsPage — init_plc_motion path         │
│                                                                      │
│  orchestrator/ — pure async state machines                           │
│    • conveyorPick.ts — Phase 6 pick cycle (binding→tracking→…)       │
│                                                                      │
│  lib/protocol.ts — typed builders (cmd.G1, cmd.M4, cmd.M4Bind, …)    │
│    + REQUIRED_KEYS runtime schema-drift guard                        │
└───────────────────┬──────────────────────────┬───────────────────────┘
        msgpack/TCP │                      JSON/TCP │ (per-camera id)
        port 8125   │                      port 7950│
                    ▼                              ▼
┌─────────────────────────────┐    ┌─────────────────────────────────┐
│ CODESYS PLC  (3.5 SP21)     │    │ Vision plugin  (external)       │
│                             │    │  FFeederCheckID=104500          │
│ FB_TcpMsgPakServer ─┐       │    │  SideCheckID    =114500          │
│  Comm task 5ms      │       │    │  BTMCheckID     =124500          │
│                     ▼       │    │  TOPCheckID     =134500          │
│ GVL.minfo_buf_ridx  ◀─ 6-slot│   │  push messages on connection    │
│                     │       │    └─────────────────────────────────┘
│                     ▼       │
│ AxisGroupSM  (EC task 1ms)  │     ┌─────────────────────────────┐
│  • SYS drain (bounded 16/scan)    │ FlexBowl  (serial COMx)     │
│  • Motion dispatcher              │  SerialManager.tsx          │
│  • FlyEvent scheduler             └─────────────────────────────┘
│  • Coord1 / PCS_1 binding +
│    window-exit detector
│  • Reel two-FB driver
│  • Supervisors (A3 hb, A6 axes, …)
│  • Server-push → reMP_info_ridx (32 slot)
│                     │
│                     ▼ EtherCAT
│  ┌──────────────────────────────────────┐
│  │ Delta servos  (3× CiA402 + reel)     │
│  │ EL1809 / EL2809 I/O                  │
│  │ ConveyorEncoderAxis (virtual today)  │
│  └──────────────────────────────────────┘
└─────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ Dev / tooling                                                        │
│                                                                      │
│  CODESYS scripting console → daemon.py → rpc.py exec --file <…>     │
│    • online_change / login / start / stop / cold-reset              │
│    • online var read/write (set_prepared_value + force/unforce)     │
│    • POU export/import                                              │
│    • Used by: codesys_scripts/jobs/templates/*.py                   │
│                                                                      │
│  Direct TCP msgpack to 192.168.1.70:8125 (no framing) — see memory   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Repository layout

```
TCP_UI/
├── README.md                  — quick start + protocol cheat sheet
├── PROJECT.md                 — this doc (full overview + status)
├── OPERATION_GUIDE.md         — operator runbook
├── package.json / vite.config.ts / tsconfig.json
├── index.tsx                  — library entry
│
├── PluginHello.tsx            — top-level renderer (TCP + dispatch)
├── components/                — pages (Control, Calib, Operation, Misc)
├── lib/protocol.ts            — typed wire builders + schema guard
├── orchestrator/              — pure state machines (Phase 6+)
│   └── conveyorPick.ts
├── hooks/                     — useTcpStringConnection, etc.
├── harness/                   — test-action registry
├── utils/                     — math, retry, etc.
├── i18n.ts                    — i18n strings
├── types.ts                   — shared TS types
├── Modal.tsx / JoggingPad.tsx / SerialManager.tsx — misc components
│
├── codesys_code/              — CODESYS Structured Text source
│   └── Application/
│       ├── GVL.st             — global state, counters, latches
│       ├── PROTOCOL_VERSION.st
│       ├── APPs/AxisGroupSM/  — main dispatcher (1ms EC task)
│       ├── APP_COMM_FBs/      — FlyEvent / packer / unpacker FBs
│       ├── Robot_FBs/         — E_RobotState, E_RobotEvent, manager
│       ├── Motion_FBs/        — motion helpers
│       ├── Comm_FBs/          — TCP server / msgpack lib
│       └── MyMACHINE.Device/  — device tree, I/O mappings
│
├── codesys_scripts/           — Python tooling for the PLC
│   ├── daemon.py              — warm-session host (IronPython 2.7)
│   ├── rpc.py                 — CLI: exec / status / lifecycle
│   ├── stamp_build.py         — provenance stamping
│   └── jobs/templates/        — live job templates (~15 files)
│
├── doc/                       — architecture + planning docs (see below)
├── build/ / dist/             — generated; gitignored where applicable
└── .claude/projects/.../memory/ — agent persistent notes (28 entries)
```

---

## Wire surfaces

### A) PLC ↔ Renderer  (msgpack, TCP 8125)

**Single connection.** No length framing — TCP stream + msgpack
self-delimiting. PLC accepts ONE client at a time
([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/ui_plc_single_client_arbitration.md)).

Request types: `SYS` (system / FSM / introspection) and `M` (motion).
Every request carries `id` echoed in reply. Replies always include
`ack: true|false` + optional `err`.

Categories:
- **System** — `PING`, `GET_MACHINE_STATE`, `GET_DIAG`, `GA_EV`,
  `RESET_DBG_INFO`, `VERSION`, `COORD1_UNBIND` (dev),
  `GET_COORD1_DEBUG`
- **Motion** — `G1`/`G2`/`G3`/`G4`, `M4` (pin-op pulse / coord1 bind),
  `SetCoord0`/`SetCoord1`, `ReelGo`, `WAIT_FOR_MOTION_STOP`,
  `BLOCK_FOR_DIGITAL_INPUT`, `WAIT_FOR_TRIGGER_MOTION_PROGRESS`,
  `READ_LATEST_CMD_LOCATION`, `GET_DIGITAL_INPUT`,
  `getDigitalInputFlipCount`, `RESET_DBG_INFO`
- **Server-push** (kind:'event') — `ST_CHG`, `COORD_SET`, `MOVE_DONE`,
  `COORD1_ERROR`

NAK codes (non-exhaustive): `group_not_ready`, `coord_not_configured`,
`protocol_version_mismatch`, `bad_frame`, `block_timeout`,
`missing_type_field`, `Coord1:rebind_active`, `Coord1:bad_scale`,
`Coord1:window_exit`.

Authoritative spec: [`doc/protocol.md`](doc/2-contracts/protocol.md). Per-feature:
- Conveyor pick: [`doc/conveyor_pick.md`](doc/3-subsystems/conveyor_pick.md)
- Schema drift guards: `lib/protocol.ts` `REQUIRED_KEYS` +
  `plc:schema-drift` window event

### B) Renderer ↔ Vision  (JSON, TCP 7950)

Push-message channel. Per-camera IDs route to per-callback tunnels
(`VP_regTcpMsgCB`). Pre-armed promises drained by `CalibPage.runAllObjects`.

Spec: [`doc/vision_contract.md`](doc/2-contracts/vision_contract.md).

### C) Renderer ↔ FlexBowl  (serial COMx)

Owned by `SerialManager.tsx`. Out of scope of TCP stack.

### D) Dev ↔ CODESYS scripting  (TCP 127.0.0.1:7420)

RPC daemon protocol (replaces the old inbox-watcher). `rpc.py exec
--file foo.py` ships an IronPython 2.7 script into the warm CODESYS
scripting session. Spec: [`doc/scripting.md`](doc/4-dev/scripting.md).

---

## Phase / workstream status

PLC + host workstreams from the solidification plan:

| ID | Workstream | Status |
|---|---|---|
| W1 | Host-authority safety contract (A1–A5) | ✅ Done 2026-04-26 |
| W2 | Cycle pipelining / runAllObjects refactor | (renderer-side; see `calibpage.md`) |
| W3 | Protocol versioning | ✅ Done 2026-04-25 |
| W4 | Diagnostics surface (`GET_DIAG`, fuzz counters) | ✅ Done 2026-05 |
| W5 | reMP_info ring hardening | ✅ Done; 4hr fuzz baseline clean |

Conveyor pick phases (parallel track):

| Phase | Scope | Status |
|---|---|---|
| 0 | Real EC encoder hardware | ⏳ Not started |
| 1 | Coord1 tracking scaffold | ✅ `a328f54` |
| 2 | COORD1_BIND SYS handler + cam-table | ✅ `98c48b8` |
| 3 | G1 `frame` field for tracking | ✅ `764e87f`, `00de892` |
| 4.1 | Virtual SM3 conveyor encoder bridge | ✅ `bc87755` |
| 4.2 | `MC_TrackConveyorBelt` + PCS_1 kernel-native tracking | ✅ `854fbbc` |
| 4.3 | FlyEvent COORD1_BIND + window-exit fault | ✅ `f0a01af` |
| 5 | M4 PulseTrigger | ✅ `8836370` |
| 6.1 | Renderer orchestrator skeleton | ✅ `40ec78d` |
| 6.2 | UI integration (button → real cycle on mock object) | ⏳ Pending |
| 6.3 | Vision → bind adapter | ⏳ Pending |
| 7 | Real belt calibration | ⏳ Pending |

---

## State machines

### PLC FSM (`E_RobotState`)

```
UnInited(10) ─POWER_ON─▶ Powering(20) ─auto─▶ Powered(30)
       ▲                                          │ GROUP_ENABLE
       │ RESET (from anywhere)                    ▼
       │                              GroupEnabling(40) ─auto─▶ GroupEnabled(50)
       │                                                              │ HOME_GO / SKIP
       │                                                              ▼
       │                                                          Homing(60)
       │                                                              │ auto
       │                                                              ▼
       │                                                          Ready(70)
       │                                                              │
       └──────────────────────── supervisor / kin / drive / Coord1 ─▶ Error(990)
                                                                       │
                                                            MC_GroupStop runs;
                                                            requires EV_RESET to clear
```

EV codes: `POWER_ON=2, GROUP_ENABLE=4, HOME_GO=6, HOME_GO_FORCE_SKIP=7,
RESET=8, ERROR=9`. NOTE: 5 = `GROUP_DISABLE`, not error.

### Renderer pick orchestrator (`runConveyorPick`)

```
idle → binding → tracking → picking → untracking → placing → done
                       │         │         │           │
                       ▼         ▼         ▼           ▼
                     ─── COORD1_ERROR (event_id match) ────▶ error
```

See [`doc/conveyor_pick.md`](doc/3-subsystems/conveyor_pick.md).

---

## Tech stack

| Layer | Tech |
|---|---|
| Renderer | React 18 + TypeScript + Vite library build (ESM + CJS) |
| Wire (PLC) | msgpack over TCP, no framing |
| Wire (Vision) | JSON over TCP, `;`-delimited |
| PLC | CODESYS 3.5 SP21, SoftMotion 4.18, SM3_Robotics |
| Bus | EtherCAT (CiA402 drives, EL1809 / EL2809 I/O) |
| Robot kinematics | SpiderR delta (workspace Z<0, [memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/delta_workspace_z_negative.md)) |
| Belt tracking | `MC_TrackConveyorBelt` + PCS_1 + virtual `ConveyorEncoderAxis` |
| PLC scripting | IronPython 2.7 (CODESYS scripting host) |
| Test driver | Python 3 + msgpack on host; daemon RPC for IDE-side ops |

---

## Build & run

```bash
npm install
npm run build       # vite library build -> dist/
```

This package is consumed by the Electron host shell (out of repo). To
launch the full UI, restart the launcher in
`C:\Users\X1\Desktop\X2.5\` ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/ui_restart_location.md)).
UI must stay foreground or the harness tick loop throttles
([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/ui_must_stay_foreground.md)).

PLC side: open `codesys_code` in CODESYS IDE, ensure the scripting
console runs `daemon.py`, then push code via `rpc.py exec --file
codesys_scripts/jobs/templates/<task>.py`. Always `import_all` before
`online_change` — the build runs from the project, not the filesystem
([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/codesys_import_then_online_change.md)).

---

## Testing

| Class | Where | Notes |
|---|---|---|
| End-to-end PLC probes | `codesys_scripts/jobs/templates/probe_*.py` | Direct TCP msgpack; 6/6 PASS gates |
| Visualizations | `codesys_scripts/jobs/templates/viz_*.py` | Plot arm vs belt, mv_progress, full cycle |
| Fuzz / soak | `codesys_scripts/jobs/templates/fuzz_*.py` | 4hr baseline @25pkt/s clean (2026-06-17) |
| Harness actions | `harness/registry.ts` | UI-driven test entry points |
| Type checking | `npx tsc --noEmit` | Renderer side |

No vitest / jest yet. Renderer orchestrator is dep-injected so a fake
transport unit test is straightforward to add when first needed.

---

## Diagnostic surfaces

| Probe | Source | Use |
|---|---|---|
| `GET_DIAG` (SYS) | `DiagSnapshot` in `lib/protocol.ts` | counters: drops, NAKs, scans, ping gap |
| `GET_MACHINE_STATE` (SYS) | `MachineState` | full snapshot for reconnect |
| `GET_COORD1_DEBUG` (SYS) | arm_x/y/z + pcs_x/y/z + pulse_raw | live tracking state |
| `VERSION` (SYS) | git SHA + build timestamp | provenance |
| Push event ring | `reMP_info_ridx` 32-slot | ST_CHG, COORD_SET, MOVE_DONE, COORD1_ERROR |
| `plc:event` window event | `PluginHello.tsx` | renderer subscribers |
| `plc:machine-state` window event | reconnect snapshot fan-out | reconcile router |
| `plc:schema-drift` window event | `validateReply()` | catches PLC field rename/removal |

---

## Documentation map

Top of the tree:

| Doc | Read it for |
|---|---|
| [`README.md`](README.md) | Quick start + wire protocol cheat sheet |
| [`PROJECT.md`](PROJECT.md) | (this) full overview + capability + status |
| [`OPERATION_GUIDE.md`](OPERATION_GUIDE.md) | Operator runbook |

Architecture & planning:

| Doc | Read it for |
|---|---|
| [`doc/architecture.md`](doc/1-concepts/architecture.md) | Integrated component map snapshot |
| [`doc/solidification.md`](doc/1-concepts/solidification.md) | Workstream execution plan + status |
| [`doc/redesign.md`](doc/1-concepts/redesign.md) | Architectural direction (keep/change/reject) |
| [`doc/coupling_invariants.md`](doc/1-concepts/coupling_invariants.md) | Cross-layer invariants that MUST move together |

Per-area detail:

| Doc | Read it for |
|---|---|
| [`doc/plc.md`](doc/3-subsystems/plc.md) | PLC source map, FSM, timing, items P0–P3 / A1–A6 |
| [`doc/protocol.md`](doc/2-contracts/protocol.md) | Wire spec (PLC ↔ renderer) |
| [`doc/calibpage.md`](doc/3-subsystems/calibpage.md) | Renderer main loop (calibration cycle) |
| [`doc/vision_contract.md`](doc/2-contracts/vision_contract.md) | Vision TCP plugin protocol |
| [`doc/conveyor_pick.md`](doc/3-subsystems/conveyor_pick.md) | Conveyor pick architecture (Phase 4 + 6) |
| [`doc/msgpack.md`](doc/2-contracts/msgpack.md) | PLC-side msgpack library review |
| [`doc/scripting.md`](doc/4-dev/scripting.md) | CODESYS scripting + RPC daemon |
| [`doc/ringbuf-bugs.md`](doc/3-subsystems/ringbuf-bugs.md) | Ring-buffer pitfalls catalogue |

---

## Cross-cutting invariants

1. **No silent drops.** Every client request gets a reply with `ack`
   true or false + machine-readable `err`. Silent drops obscure the
   real fault and hang `sendTcpMsgPack` until its own timeout.
2. **PLC stays generic.** Business / domain / per-job logic lives in
   the renderer. The PLC is a motion / IO / safety server. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/plc_stays_generic.md))
3. **PLC is vision-blind.** Vision routes through host; no direct link.
   ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/plc_vision_direct_link_rejected.md))
4. **Type:"SYS" required for system packets.** PING / GA_EV /
   GET_MACHINE_STATE without `type:"SYS"` land in the motion buffer and
   NAK as `group_not_ready`. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/tcp_type_sys_required.md))
5. **One commit per logical segment.** Never one mega-commit. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/commit_per_segment.md))
6. **Cycle time is the acceptance gate** for any refactor (see PLC
   timing diagram).
7. **`import_all` before `online_change`.** The build runs from the
   loaded project, not the .st files on disk. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/codesys_import_then_online_change.md))

---

## Known limits / gotchas

- **SMC virtual axis self-halt** — virtual axes trip after the first G1
  past Ready; subsequent G1 ACKs but no motion. Real EtherCAT axes are
  fine. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/smc_virtual_axis_self_halt.md))
- **Delta workspace is Z<0.** G1 to Z>0 ACKs but arm doesn't move
  (silent kinematic fail). Use Z negative in tests/probes. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/delta_workspace_z_negative.md))
- **A-axis `/10` workaround** in `ProcessMotionPacket` is intentional;
  SpiderR `Kin_CAxis` structurally wraps `c.A`. Do not "fix" without
  reading the memory entry. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/a_axis_div10_workaround.md))
- **UI/PLC single-client arbitration.** Only one client on TCP 8125 at
  a time; orchestrate UI connect/disconnect via `remote_ctrl` if you
  need to script alongside live UI. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/ui_plc_single_client_arbitration.md))
- **Online change + FB interface edit** (e.g. new VAR_INPUT on
  NBS-wrapping FB) leaves listener stale-active — needs cold reset, not
  stop/start. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/online_change_fb_interface_change.md))
- **Virtual-motors gate TTL** — TON Q stays latched after TTL expiry;
  cycle Request FALSE before re-forcing TRUE. ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/virtual_motors_gate_ton_reset.md))
- **CODESYS inbox-watcher is deprecated** — use the RPC daemon
  (`rpc.py exec --file …`). ([memory](.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/codesys_rpc_daemon.md))

---

## Conventions

- **Update docs alongside code.** When a tracked item lands, mark its
  Status in the relevant doc.
- **New findings to the appropriate doc.** PLC → `plc.md`. Renderer
  cycle → `calibpage.md`. Cross-cutting → `solidification.md`. New
  capability → new doc + index update here + in README.
- **No silent drops** is a hard invariant, not a nicety.
- **Cycle time is the acceptance gate.**
- **Commit per logical segment.** Never one mega-commit.

---

## License

MIT. See [`package.json`](package.json).
