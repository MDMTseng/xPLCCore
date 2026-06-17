# codesys_scripts/

Tooling around the CODESYS PLC project. Top level holds only the
commands you run by hand; everything else is reached through them.

## What you run

| File | Use for |
| ---- | ------- |
| `rpc.py` | All scripting-daemon interactions: `ping`, `exec --file ...`, `push`, `status`, `stop`, `daemon-start`. Most workflows go through here. |
| `build.sh` | Headless CODESYS build of the project (writes `build.log`). |
| `pytest.ini` | (Not a command -- here so `pytest` from the repo root picks up the right config.) |

`tests/` is the live regression suite. `jobs/templates/` is the
catalog of scripts `rpc.py exec --file` invokes (push pipeline,
virtual-motors gate, lifecycle).

## What's in the subdirectories

- `internals/` -- helpers `rpc.py` and the tests call into:
  `daemon.py` (runs in CODESYS Scripting Console),
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
