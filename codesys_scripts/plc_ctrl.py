#!/usr/bin/env python3
"""
plc_ctrl.py  --  unified CLI over both control channels.

Two channels, auto-routed by subcommand:

  TCP channel (via remote_harness -> UI -> PLC port 8125)
    event  <EV_NAME|int>       fire SYS/GA_EV
    tcp    <json>              send raw msgpack packet
    smoketest                  run virtual_motors_smoketest.py

  IDE channel (via jobs/inbox -> watcher.py inside CODESYS)
    read   <GVL.symbol>        online_application.read_value
    force  <GVL.symbol> <val>  set_prepared_value + force_prepared_values
    unforce                    unforce_all_values
    import                     run jobs/templates/import_all.py
    virt   on|off              shortcut: force/unforce the virtual-motors gate

Prereqs:
  - remote_harness.py running on 127.0.0.1:8127 (for TCP channel)
  - UI with harness toggle ON and TCP connected (for TCP channel)
  - watcher.py running inside CODESYS IDE (for IDE channel)

Exit code: 0 on success, 1 on failure, 2 on bad args.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE / "remote_ctrl.py"
SMOKETEST = HERE / "virtual_motors_smoketest.py"
INBOX = HERE / "jobs" / "inbox"
DONE = HERE / "jobs" / "done"
TEMPLATES = HERE / "jobs" / "templates"

# E_RobotEvent name -> index (mirrors Robot_FBs/E_RobotEvent.st)
EVENTS = {
    "EV_NONE": 0, "EV_BOOT": 1, "EV_POWER_ON": 2, "EV_POWER_OFF": 3,
    "EV_GROUP_ENABLE": 4, "EV_GROUP_DISABLE": 5,
    "EV_HOME_GO": 6, "EV_HOME_GO_FORCE_SKIP": 7,
    "EV_RESET": 8, "EV_ERROR": 9, "EV_OK": 10, "EV_ER": 11,
}


# ---- TCP channel ------------------------------------------------------

def _remote_ctrl(action: str, payload: dict, timeout: float = 5.0) -> dict:
    cmd = [sys.executable, str(REMOTE_CTRL), action, json.dumps(payload),
           "--timeout", str(timeout)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    try:
        return json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return {"raw": res.stdout, "stderr": res.stderr}


def cmd_event(args) -> int:
    ev = EVENTS.get(args.ev.upper()) if not args.ev.isdigit() else int(args.ev)
    if ev is None:
        print(f"unknown event: {args.ev}", file=sys.stderr)
        print(f"known: {', '.join(EVENTS)}", file=sys.stderr)
        return 2
    out = _remote_ctrl("send_tcp_msgpack",
                       {"type": "SYS", "cmd": "GA_EV", "ev": ev})
    print(json.dumps(out, indent=2))
    return 0 if (out.get("result") or {}).get("ok", True) else 1


def cmd_tcp(args) -> int:
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as ex:
        print(f"bad json: {ex}", file=sys.stderr)
        return 2
    out = _remote_ctrl("send_tcp_msgpack", payload, timeout=args.timeout)
    print(json.dumps(out, indent=2))
    return 0 if (out.get("result") or {}).get("ok", True) else 1


def cmd_smoketest(args) -> int:
    return subprocess.call([sys.executable, str(SMOKETEST)])


# ---- IDE channel ------------------------------------------------------

def _submit_job(name: str, body: str, timeout_sec: float = 120.0) -> tuple[int, str]:
    """Drop an ad-hoc script into jobs/inbox and poll jobs/done for its log."""
    INBOX.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    job_name = f"{stamp}-{name}"
    (INBOX / f"{job_name}.py").write_text(body, encoding="ascii")
    log_path = DONE / f"{job_name}.log"
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if log_path.exists():
            return 0, log_path.read_text(encoding="utf-8", errors="replace")
        time.sleep(0.4)
    return 1, f"TIMEOUT after {timeout_sec}s; is watcher.py running in CODESYS?"


def _oapp_wrap(body: str) -> str:
    """Wrap a scriptengine snippet in the standard login/logout boilerplate."""
    return (
        "# -*- coding: ascii -*-\n"
        "proj = projects.primary\n"
        "app_obj = None\n"
        "for app in list(proj.find('Application', True) or []):\n"
        "    app_obj = app; break\n"
        "oapp = online.create_online_application(app_obj)\n"
        "oapp.login(OnlineChangeOption.Try, False)\n"
        "try:\n"
        + "".join("    " + line + "\n" for line in body.strip().splitlines())
        + "finally:\n"
        "    oapp.logout()\n"
    )


def cmd_read(args) -> int:
    body = _oapp_wrap(f"print('{args.symbol} =', oapp.read_value({args.symbol!r}))")
    rc, log = _submit_job(f"read-{args.symbol.replace('.', '_')}", body, args.timeout)
    print(log)
    return rc


def cmd_force(args) -> int:
    body = _oapp_wrap(
        f"oapp.set_prepared_value({args.symbol!r}, {args.value!r})\n"
        f"oapp.force_prepared_values()\n"
        f"import time; time.sleep(0.2)\n"
        f"print('{args.symbol} =', oapp.read_value({args.symbol!r}))"
    )
    rc, log = _submit_job(f"force-{args.symbol.replace('.', '_')}", body, args.timeout)
    print(log)
    return rc


def cmd_unforce(args) -> int:
    body = _oapp_wrap("oapp.unforce_all_values()\nprint('unforce_all_values done')")
    rc, log = _submit_job("unforce", body, args.timeout)
    print(log)
    return rc


def cmd_import(args) -> int:
    # Re-use the existing template verbatim.
    body = (TEMPLATES / "import_all.py").read_text(encoding="ascii")
    rc, log = _submit_job("import_all", body, args.timeout)
    print(log)
    return rc


def cmd_virt(args) -> int:
    if args.state == "on":
        body = _oapp_wrap(
            "oapp.set_prepared_value('GVL.bVirtualMotorsMode_Request', 'TRUE')\n"
            "oapp.force_prepared_values()\n"
            "import time; time.sleep(0.2)\n"
            "print('Request =', oapp.read_value('GVL.bVirtualMotorsMode_Request'))\n"
            "print('Derived =', oapp.read_value('GVL.bVirtualMotorsMode'))"
        )
        name = "virt-on"
    else:
        body = _oapp_wrap(
            "oapp.unforce_all_values()\n"
            "import time; time.sleep(0.2)\n"
            "print('Request =', oapp.read_value('GVL.bVirtualMotorsMode_Request'))\n"
            "print('Derived =', oapp.read_value('GVL.bVirtualMotorsMode'))"
        )
        name = "virt-off"
    rc, log = _submit_job(name, body, args.timeout)
    print(log)
    return rc


# ---- argparse ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="plc_ctrl")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("event", help="fire SYS/GA_EV via TCP")
    s.add_argument("ev", help="event name (e.g. EV_POWER_ON) or integer")
    s.set_defaults(func=cmd_event)

    s = sub.add_parser("tcp", help="send raw msgpack packet via TCP")
    s.add_argument("payload", help="json payload")
    s.add_argument("--timeout", type=float, default=5.0)
    s.set_defaults(func=cmd_tcp)

    s = sub.add_parser("smoketest", help="run virtual_motors_smoketest.py")
    s.set_defaults(func=cmd_smoketest)

    s = sub.add_parser("read", help="online read a symbol via watcher")
    s.add_argument("symbol")
    s.add_argument("--timeout", type=float, default=60.0)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("force", help="force a symbol via watcher")
    s.add_argument("symbol")
    s.add_argument("value", help="literal, e.g. TRUE, FALSE, 42")
    s.add_argument("--timeout", type=float, default=60.0)
    s.set_defaults(func=cmd_force)

    s = sub.add_parser("unforce", help="unforce_all_values via watcher")
    s.add_argument("--timeout", type=float, default=60.0)
    s.set_defaults(func=cmd_unforce)

    s = sub.add_parser("import", help="push codesys_code/ into project + generate_code")
    s.add_argument("--timeout", type=float, default=180.0)
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("virt", help="virtual-motors gate shortcut")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--timeout", type=float, default=60.0)
    s.set_defaults(func=cmd_virt)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
