# codesys_scripts/

Tooling around the CODESYS PLC project.

> **v2 (branch `codesys-control-v2`)** reworked the daemon. If you are
> looking for why `stop` used to hang the IDE, see "What v2 changed".

## Configuration

All machine-specific values live in **one** file, `codesys_env.json`
(copy `codesys_env.sample.json`). Override its location with
`$XPLC_CONFIG`. Relative paths resolve against the repo root.

v1 hardcoded five absolute paths across `daemon.py` and `build.sh`, all
pointing at one developer's desktop, and `daemon.py` called
`os.makedirs()` on the state path -- so moving the repo to another
machine silently built a junk tree instead of failing. Placeholders in
the config now raise instead.

Check everything at once:

```bash
python rpc.py doctor
```

## What you run

| File | Use for |
| ---- | ------- |
| `rpc.py` | Talking to the daemon: `ping`, `exec`, `save`, `read`, `write`, `logout`, `snapshot`, `yield`, `stop`, `doctor`. |
| `supervisor.py` | Owning the CODESYS process from outside: `start`, `watch`, `kill`, `build`, `snapshots`, `restore`, `ps`. |
| `daemon.py` | RPC server. Runs **inside** the Scripting Console; you normally let `supervisor.py start` launch it. |
| `build.sh` | Thin wrapper for `supervisor.py build` (cold headless compile). |
| `config.py` | Shared config loader. Imported by both IronPython and CPython sides. |

## What v2 changed

**The problem.** While the v1 daemon ran, the IDE was unusable, so the
only way to get CODESYS back was to stop the daemon -- and stopping was
the operation that hung. You were forced through the dangerous path
several times a day, always after hours of accumulated edits.

Two root causes:

1. **`daemon.py` never saved.** Saving was opt-in per job template: 14
   of 47 called `proj.save()`, and `import_all.py` explicitly did not.
   Dirty state accumulated for the whole session.
2. **`stop` was `return True`.** No save, no logout, no close. CODESYS
   tore down the IronPython scope holding a dirty project and often a
   live online session. If the IDE needed a modal at that moment it
   could not show one -- the script still owned the scripting context.

**The fixes.**

- **Nothing accumulates.** Every mutating job ends with `proj.save()`,
  including jobs that raised. Teardown cost is proportional to
  accumulated state; drive it to zero and teardown gets cheap.
- **You do not have to leave to get work done.** `save`, `read`,
  `write`, `logout` and `snapshot` are RPCs now, so the usual reasons to
  kill the daemon are served without killing it.
- **Release is first-class.** `rpc.py yield` tears down in a defined
  order (logout -> save -> drop refs -> close socket) and leaves CODESYS
  open with the project saved, so re-entry is warm.
- **Shutdown is observable.** Each teardown step publishes its phase to
  `daemon.status` from the heartbeat thread, which keeps ticking even
  when the main thread is blocked in a CODESYS call. `rpc.py yield`
  prints the phases live, so a slow release tells you *which step*.
- **Every mutating job snapshots first.** `.project` is copied to
  `jobs/snapshots/` before the job runs. Because the file is always
  current, that snapshot really is "the state before this job".
- **Sessions retire on a budget.** After `session_max_jobs` or
  `session_max_seconds` the daemon releases cleanly rather than rotting.
- **The supervisor can kill safely.** `supervisor.py kill` is only an
  acceptable recovery *because* of save-per-job. Do not weaken that rule
  without revisiting `supervisor.py`.

Typical loop:

```bash
python supervisor.py start            # launch CODESYS + daemon
python rpc.py exec --file job.py      # snapshot -> run -> save
python rpc.py read GVL.AxisGroupSMScans
python rpc.py save
python rpc.py yield                   # take the IDE back, watch the phases
```

If a release wedges anyway:

```bash
python supervisor.py ps               # what is running, how old the heartbeat is
python supervisor.py kill --yes       # safe: worst case you lose one job
python supervisor.py snapshots        # rollback points
python supervisor.py restore <name>
```

---



## What's in the subdirectories

- `internals/` -- helpers `rpc.py` and the tests call into:
  `daemon_kickstart.py` (`rpc.py daemon-start` delegates here),
  `online_change_with_regression.py` (`rpc.py push` wrapper),
  `remote_ctrl.py` / `remote_harness.py` (UI <-> harness IPC),
  `tcp_client.py` / `tcp_server.py` (msgpack TCP helpers),
  `build.py` (called by `build.sh`),
  `plc_ctrl.py`, `virtual_motors_smoketest.py`.
- `jobs/templates/` -- 15 active jobs (push, lifecycle, virtual gate,
  msgpack tests). Older one-shots in `jobs/templates/_archive/`.
- `_archive/` -- root-level scripts retired in the 2026-06-17 sweep
  (legacy `watcher.py`, mock harnesses, standalone `test_*.py`
  one-shots). Treat as read-only history.

## Programmatic control surface

There are four distinct channels to drive CODESYS / the PLC. They
solve different problems and have different blast radius -- pick the
narrowest one that does the job.

### 1. Raw msgpack TCP to the PLC (`192.168.1.70:8125`)

The PLC's TCP server speaks length-less msgpack. Best for: tests,
fuzzing, anything that exercises the runtime without touching the IDE.
Cheap (~5ms round-trip), no daemon needed.

**Single-client arbitration**: only one TCP client at a time. The UI
usually owns the slot; before grabbing it, call
`internals/remote_ctrl.py disconnect_tcp '{}'` to release it (and
reconnect after).

```python
import socket, msgpack
s = socket.socket(); s.connect(("192.168.1.70", 8125))
s.sendall(msgpack.packb({"type": "SYS", "cmd": "PING", "id": 1}, use_bin_type=True))
# read reply with msgpack.Unpacker -- no length prefix on the wire
```

Useful SYS commands (all need `type: "SYS"` or they're NAK'd as
`group_not_ready`):
- `PING` -- liveness + UI heartbeat refresh
- `GA_EV {ev: <E_RobotEvent>}` -- drive the FSM
- `GET_MACHINE_STATE` -- full snapshot (state, motion buffer, axes
  errors, coord_set)
- `GET_DIAG` -- 27-field counter dump (replaces daemon `oapp.read_value`
  for soak tests; see `tests/test_plc_4hr_fuzz.py`)
- `VERSION` -- `git_sha` + `build_ts_ms` (stamped by the push pipeline)
- `RESET_DBG_INFO` -- zero the comm-stability counters

`type: "M"` packets drive motion (`SetCoord0`, `SetCoord1`, `M4`
fly-events, etc.); see `codesys_code/Application/APPs/AxisGroupSM/`
for the wire schema.

### 2. CODESYS scripting daemon (`rpc.py exec --file foo.py`)

For anything that needs the IDE's scripting context: project edits,
online change, force values, symbol reads via `oapp.read_value`. The
daemon is a long-lived IronPython process inside the Scripting
Console (`daemon.py`); `rpc.py` is its TCP client.

```bash
# run a one-off job
python codesys_scripts/rpc.py exec --file my_job.py

# or from stdin
echo 'print(projects.primary.path)' | python codesys_scripts/rpc.py exec --label whereami
```

Inside a job, these globals are pre-injected by CODESYS:
`projects`, `system`, `online`, `device_repository`. Typical idioms:

```python
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

# read / write / force GVL symbols
val = oapp.read_value("GVL.AxisGroupSMScans")
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
oapp.force_prepared_values()

# edit a POU's ST text and online-change it
pou = next(iter(proj.find("AxisGroupSM", True)))
pou.textual_implementation.replace("OLD_CODE", "NEW_CODE")
app.generate_code()       # build
oapp.login(OnlineChangeOption.Try, False)  # apply online change
```

`jobs/templates/` has 15 ready-to-use jobs for the common cases:
`import_all.py` (push every .st on disk into the project),
`online_change.py`, `virtual_motors_force/unforce.py`, `cold_reset.py`,
`stamp_build_info.py`, etc.

### 3. UI harness (`internals/remote_ctrl.py`)

The Electron UI exposes an IPC harness so external scripts can drive
its TCP socket without arbitrating directly. Useful when you need the
UI's PLC connection live while still scripting events.

```bash
python codesys_scripts/internals/remote_ctrl.py ping
python codesys_scripts/internals/remote_ctrl.py disconnect_tcp '{}'
python codesys_scripts/internals/remote_ctrl.py connect_tcp '{"host":"192.168.1.70","port":8125}'
python codesys_scripts/internals/remote_ctrl.py send_tcp_msgpack '{"type":"SYS","cmd":"PING"}'
```

Caveats:
- UI window must stay foreground (Electron throttles backgrounded
  renderers; the harness tick loop dies).
- If the harness wedges, raw TCP (channel 1) still works once you
  release the slot.

### 4. Headless CODESYS build (`build.sh`)

Cold-starts a fresh `CODESYS.exe` with `--runscript=internals/build.py`
to compile-check the project. No daemon needed, no PLC touched, no
online change. Exit code 0 = clean, 1 = build errors, 2 = script
crash; `build.log` carries the messages. Mostly for CI or sanity
checks when the daemon is down.

## How the channels stack up

| | Touches code | Touches running PLC | Needs daemon | Needs IDE | Typical use |
|---|---|---|---|---|---|
| TCP msgpack (1) | -- | yes | -- | -- | tests, fuzz, runtime control |
| `rpc.py exec` (2) | yes | yes (via online change) | yes | yes (Console open) | code push, force, symbol reads |
| UI harness (3) | -- | yes (through UI) | -- | -- | scripted UI flows |
| `build.sh` (4) | yes (compile only) | -- | -- | spawns fresh | CI, syntax check |

Rule of thumb: prefer (1) when possible, fall back to (2) for code
changes, use (3) when you need the UI's perspective, reserve (4) for
isolated build verification.

