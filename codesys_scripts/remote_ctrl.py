#!/usr/bin/env python3
"""
remote_ctrl.py  <action> [json-payload] [--no-wait] [--timeout N]

Push a single instruction into remote_harness and (by default) block until
the UI posts a result. The UI must be running with the remote-harness hook
enabled and connected to the same host:port.

Examples:
  python codesys_scripts/remote_ctrl.py ping
  python codesys_scripts/remote_ctrl.py get_state
  python codesys_scripts/remote_ctrl.py send_tcp_msgpack '{"type":"SYS","cmd":"GA_EV","ev":1}'
  python codesys_scripts/remote_ctrl.py connect_tcp '{"host":"127.0.0.1","port":8125}'
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 8127


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("payload", nargs="?", default="{}")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as ex:
        print(f"bad json payload: {ex}", file=sys.stderr)
        return 2

    body = json.dumps(
        {
            "action": args.action,
            "payload": payload,
            "wait_for_result": not args.no_wait,
            "timeout": args.timeout,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/push",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout + 2) as resp:
            out = json.loads(resp.read())
    except urllib.error.URLError as ex:
        print(f"harness unreachable: {ex}", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2))
    result = out.get("result") or {}
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
