# xPLCCore — Delta-Robot Tape-and-Reel Packer

Electron + React operator UI and CODESYS PLC firmware for a delta-robot
tape-and-reel packer with 4-surface defect inspection and
side-projection dimensioning.

The UI in this repo is the **renderer + orchestrator**: it owns the PLC
TCP link, the vision TCP link, FlexBowl serial, and the overall job
sequence. The PLC owns safety-critical motion and coordinate transforms.
Vision runs as an external standalone app and streams results over
MessagePack/TCP.

---

## Architecture at a glance

```
        +----------------------+       msgpack / TCP 8125       +----------------------+
        |                      |  <--------------------------   |                      |
        |   Electron + React   |         heartbeat / cmd        |  CODESYS PLC (SP21)  |
        |      renderer        |  <----- ST_CHG push events --- |   SoftMotion 3.x     |
        |                      |                                |   EtherCAT + CiA402  |
        |   PluginHello.tsx    |                                |   AxisGroupSM.st     |
        |   ControlPage  ---+  |                                |                      |
        |   CalibPage       +--+- harness registry              +----------+-----------+
        |   OperationPage   |  |                                           |
        |   MiscControls    |  |                                           | EtherCAT
        |                   |  |                                           v
        +---------+---------+  |                                +----------+-----------+
                  |            |                                |  Delta servos +      |
                  |   msgpack  |                                |  EL1809 I/O / etc.   |
        +---------+---------+  +---- serial (COM) ----> FlexBowl+----------------------+
        |   Vision app       |
        |   (external TCP)   |
        +--------------------+
```

---

## Repository layout

| Path | What lives there |
|---|---|
| [`components/`](components/) | Tab pages: `ControlPage` (shell + reconciliation), `CalibPage`, `OperationPage`, `MiscControlsPage` |
| [`PluginHello.tsx`](PluginHello.tsx) | Top-level renderer: mounts the TCP link, heartbeat, reconnect snapshot, harness registry |
| [`harness/`](harness/) | Test-harness action registry (`useHarnessAction`, `register/unregisterHarnessAction`) |
| [`hooks/`](hooks/), [`utils/`](utils/), [`lib/`](lib/), [`i18n.ts`](i18n.ts) | Shared renderer infrastructure |
| [`codesys_code/`](codesys_code/) | CODESYS Structured Text source — `AxisGroupSM.st`, GVLs, POUs |
| [`codesys_scripts/`](codesys_scripts/) | Python/IronPython tooling: TCP warm-session server, inbox-watcher, job templates |
| [`doc/`](doc/) | All planning and architecture docs (see table below) |
| [`OPERATION_GUIDE.md`](OPERATION_GUIDE.md) | Operator-facing runbook |

---

## Documentation map

| Doc | Purpose |
|---|---|
| [`doc/solidification.md`](doc/solidification.md) | **Start here.** Integrated execution plan. Workstreams spanning PLC + host, acceptance tests, phasing. |
| [`doc/redesign.md`](doc/redesign.md) | Architectural direction: what we keep, what we change in place, what we rejected and why. |
| [`doc/plc.md`](doc/plc.md) | PLC source map, state machine, timing diagram, review items (P0–P3, A1–A6), progress log. |
| [`doc/calibpage.md`](doc/calibpage.md) | Renderer main-loop cleanup items (`CalibPage.tsx`). |
| [`doc/msgpack.md`](doc/msgpack.md) | PLC-side MessagePack library review — findings, ranked fixes, phasing. |
| [`doc/scripting.md`](doc/scripting.md) | CODESYS scripting round-trip: warm-session TCP, export/import flow, encoding rules, tooling gotchas. |
| [`doc/ringbuf-bugs.md`](doc/ringbuf-bugs.md) | Known ring-buffer pitfalls (catalogued while hardening `reMP_info_ridx` / `minfo_buf_ridx`). |

---

## PLC protocol — quick reference

TCP/msgpack on `192.168.1.70:8125`. No length framing; a single TCP
connection carries request and unsolicited event packets.

Every packet is a msgpack map. Every request carries `id` (DINT) which
the reply echoes.

### Request dispatch

| `type` | `cmd` | Effect | Reply |
|---|---|---|---|
| `"SYS"` | `PING` | UI heartbeat. Stamps `GVL.LastUiPingMs`. | `{pong:true, runtime_ms, id, ack:true}` |
| `"SYS"` | `GA_EV` + `ev:<int>` | Drive FSM (see event table below). | `{st, st_str, err_src, err_id, id, ack:true}` |
| `"SYS"` | `GET_MACHINE_STATE` | Full snapshot for UI reconnect. | `{st, st_str, err_src, err_id, motion_buffer_size, movement_id, runtime_ms, coord_set, id, ack:true}` |
| _(none)_ | `G1` / `G2` / `G3` / `SetCoord0` / `SetCoord1` / `ReelGo` / ... | Motion queue. Runs only in `Ready`. | `{..., id, ack:true/false[, err]}` |

In any non-`Ready` state, motion packets are NAK'd with
`{err:'group_not_ready', id, ack:false}` — **never silently dropped**.
G-code motion is additionally gated by the coord system: if the host
has not called `SetCoord0` or `SetCoord1` since the last `UnInited`
entry, G1/G2/G3 NAK with `err:'coord_not_configured'`.

> If you craft test packets by hand, remember **`type:"SYS"` is
> required** for PING/GA_EV/GET_MACHINE_STATE. Without it they land
> in the motion buffer and come back NAK'd. See
> [`doc/plc.md`](doc/plc.md) §Protocol.

### Server-push channel

Packets with `kind:"event"` arrive **unsolicited** on the same socket.
Events have no `id` — the renderer distinguishes them before the
id-based reply dispatcher.

| `name` | Fields | When |
|---|---|---|
| `ST_CHG` | `st, st_str, from, runtime_ms` | Any FSM transition, including those triggered autonomously by the PLC (e.g., A3 heartbeat supervisor flipping to `Error`). |
| `COORD_SET` | `runtime_ms` | `GVL.CoordSystemConfigured` goes FALSE→TRUE (after `SetCoord0`/`SetCoord1`). Lets the UI unblock G1-dependent workflows without polling. |
| `MOVE_DONE` | `movement_id, runtime_ms` | Motion buffer drained: a queued move just finished and no more are pending. `movement_id` is `LastAcceptedMovementId`. |

### Event codes (`GA_EV`)

| `ev` | Event |
|---|---|
| 2 | `EV_POWER_ON` |
| 4 | `EV_GROUP_ENABLE` |
| 6 | `EV_HOME_GO` |
| 7 | `EV_HOME_GO_FORCE_SKIP` *(virtual-motors bring-up)* |
| 8 | `EV_RESET` |

---

## State machine

```
                +----------+
                | UnInited |<--------- EV_RESET (from anywhere) --------+
                +----+-----+                                            |
                     | EV_POWER_ON                                      |
                     v                                                  |
                +----------+  (auto)   +---------+                      |
                | Powering |---------->| Powered |                      |
                +----------+           +----+----+                      |
                                            | EV_GROUP_ENABLE           |
                                            v                           |
                                    +-------+--------+ (auto)           |
                                    | GroupEnabling  |----+             |
                                    +----------------+    v             |
                                                   +-------------+      |
                                                   | GroupEnabled |     |
                                                   +------+-------+     |
                                                          | EV_HOME_GO  |
                                                          | / SKIP      |
                                                          v             |
                                                      +---------+       |
                                                      | Homing  |       |
                                                      +----+----+       |
                                                           |            |
                                                           v            |
                                                       +-------+        |
                                                       | Ready |        |
                                                       +---+---+        |
                                                           |            |
                                              supervisor / kin / drive  |
                                                       fault            |
                                                           v            |
                                                       +-------+        |
                                                       | Error |--------+
                                                       +-------+
```

Numeric states: `UnInited=10  Powering=20  Powered=30  GroupEnabling=40
GroupEnabled=50  Homing=60  Ready=70  Error=990`.

---

## Host-authority invariants (W1 solidification)

| ID | Invariant | Enforced at |
|---|---|---|
| A1 | Motion packets in non-Ready states get an explicit `group_not_ready` NAK — never silently dropped. | PLC (`AxisGroupSM.st`) |
| A2 | Post-homing coord gate: G1/G2/G3 refuse to run until `SetCoord0`/`SetCoord1` is called since the last `UnInited` entry. | PLC |
| A3 | If no UI heartbeat (`PING`) for 5 s, the PLC autonomously trips FSM to `Error`. | PLC |
| A4 | `GET_MACHINE_STATE` provides a single-shot snapshot for UI reconnect (`coord_set` included). | PLC |
| A5 | ST_CHG push events carry `from` so the UI doesn't have to poll. | PLC + Host |

The heartbeat cascade is `UI ping 1 s → UI stale 3.5 s → PLC
supervisor trip 5 s`, so the UI catches staleness first and the PLC is
the last line of defence.

---

## CODESYS round-trip tooling

[`codesys_scripts/`](codesys_scripts/) implements a warm-session bridge
so Structured Text edits can round-trip via the IDE without repeatedly
paying the project-open cost.

- [`codesys_scripts/tcp_server.py`](codesys_scripts/tcp_server.py) —
  pasted into the CODESYS scripting console; listens on
  `127.0.0.1:9790` for job payloads.
- [`codesys_scripts/watcher.py`](codesys_scripts/watcher.py) — the
  inbox-watcher variant. Polls `codesys_scripts/jobs/inbox/` for
  `.py` files, executes each inside the warm IDE session, captures
  stdout/stderr to `jobs/done/<name>.log`, and writes a heartbeat
  file to `jobs/watcher.status` for liveness probing.
- [`codesys_scripts/jobs/templates/`](codesys_scripts/jobs/templates/) —
  curated job templates: project build/download, POU export/import,
  online variable read/write, PLC-state probes, edit transforms. Pick
  one, copy into `jobs/inbox/` to run.

Gotchas: IronPython 2.7 inside CODESYS; all scripts need
`# -*- coding: ascii -*-`. Expression format for `read_value` does not
take the `Application.` prefix. Results come back as stringified IEC
values (`"UDINT#42"`, `"BOOL#TRUE"`). See
[`doc/scripting.md`](doc/scripting.md) for the full catalogue.

---

## Build

```bash
npm install
npm run build     # Vite library build -> dist/
```

The package is published as an ESM + CJS library (see
[`package.json`](package.json) `main`/`module`/`types`). It is consumed
by the host Electron application, which is out of scope for this repo.

---

## Conventions

- **Update docs alongside code.** When a tracked item (P-number,
  A-number, workstream step) lands, mark its **Status** column in the
  relevant doc and log the change in "Recently completed."
- **New findings go into the appropriate doc.** PLC items →
  `doc/plc.md`. Renderer items → `doc/calibpage.md`. Cross-cutting →
  `doc/solidification.md`.
- **Cycle time is the acceptance gate for any refactor.** See
  [`doc/plc.md`](doc/plc.md) §Timing diagram.
- **No silent drops.** Every client request must get a reply — `ack`
  true or false with a machine-readable `err`. This is an invariant,
  not a nicety: the UI's `sendTcpMsgPack` would otherwise hang until
  its own timeout and obscure the real fault.

---

## License

MIT. See [`package.json`](package.json).
