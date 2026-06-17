# codesys_scripts/

Tooling around the CODESYS PLC project. Top level holds only the
commands you run by hand; everything else is reached through them.

## What you run

| File | Use for |
| ---- | ------- |
| `rpc.py` | All scripting-daemon interactions: `ping`, `exec --file ...`, `push`, `status`, `stop`, `daemon-start`. Most workflows go through here. |
| `daemon.py` | RPC server. Runs **inside** the CODESYS Scripting Console (Tools > Scripting > Execute Script File... > pick this), not from the terminal. `rpc.py daemon-start` spawns CODESYS with `--runscript=daemon.py` so you usually don't touch it directly. |
| `build.sh` | Headless CODESYS build of the project (writes `build.log`). |
| `pytest.ini` | (Not a command -- here so `pytest` from the repo root picks up the right config.) |

`tests/` is the live regression suite. `jobs/templates/` is the
catalog of scripts `rpc.py exec --file` invokes (push pipeline,
virtual-motors gate, lifecycle).

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

## Common workflows

```bash
# push code (edit .st, then):
python codesys_scripts/rpc.py push                 # full: import + online + tests
python codesys_scripts/rpc.py push --no-tests      # hot fix, skip regression

# environment health check
python codesys_scripts/rpc.py status

# revive a dead daemon (spawns CODESYS if needed)
python codesys_scripts/rpc.py daemon-start

# headless build
codesys_scripts/build.sh

# tests
pytest codesys_scripts/tests/                      # full suite
pytest codesys_scripts/tests/test_plc_4hr_fuzz.py  # 4hr fuzz
```

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

