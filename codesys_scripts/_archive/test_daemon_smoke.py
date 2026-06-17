#!/usr/bin/env python3
"""Smoke-test the CODESYS RPC daemon end to end.

Prereq: daemon.py is running in the CODESYS Scripting Console.

Cases:
  1. ping              -- reachability
  2. trivial exec      -- captures stdout
  3. exec that raises  -- ok=False, traceback in error
  4. read GVL counter  -- verifies project + online API still wired
  5. forgot-logout job -- verifies safe_logout cleanup (next call must succeed)

Run:  python codesys_scripts/test_daemon_smoke.py
"""
import json, socket, sys, time

HOST, PORT = "127.0.0.1", 7420

def call(req, timeout=60):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        # No shutdown(SHUT_WR): IronPython side wedges on half-close. Read
        # until we have one full line of JSON (the daemon's framing).
        chunks = []
        while True:
            buf = s.recv(65536)
            if not buf:
                break
            chunks.append(buf)
            if b"\n" in buf:
                break
    finally:
        s.close()
    return json.loads(b"".join(chunks).decode("utf-8").strip())


def case(label, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print("  [{}] {}{}".format(tag, label, ("  -- " + detail) if detail else ""))
    return ok


def main():
    failures = 0

    # 1. ping
    print("\n1. ping")
    try:
        rep = call({"cmd": "ping"})
    except ConnectionRefusedError:
        print("  [FAIL] connection refused -- is daemon.py running in CODESYS Scripting Console?")
        sys.exit(2)
    if not case("ping returns ok", rep.get("ok") is True, str(rep)):
        failures += 1

    # 2. trivial exec
    print("\n2. trivial exec")
    rep = call({"cmd": "exec", "label": "smoke_trivial",
                "code": "print('hello from inside CODESYS')"})
    if not case("ok=True", rep.get("ok") is True, "elapsed=%s" % rep.get("elapsed")):
        failures += 1
    if not case("stdout captured", "hello from inside CODESYS" in rep.get("stdout","")):
        failures += 1

    # 3. exec that raises
    print("\n3. exec that raises")
    rep = call({"cmd": "exec", "label": "smoke_raises",
                "code": "print('before'); 1/0; print('after')"})
    if not case("ok=False", rep.get("ok") is False):
        failures += 1
    if not case("partial stdout preserved",
                "before" in rep.get("stdout","")
                and "after" not in rep.get("stdout","")):
        failures += 1
    if not case("traceback in error", "ZeroDivisionError" in (rep.get("error") or "")):
        failures += 1

    # 4. real CODESYS API call -- read a GVL counter
    print("\n4. real CODESYS API call (read GVL.UiPingCount)")
    code = (
        "import time\n"
        "for app in list(projects.primary.find('Application', True) or []):\n"
        "    oapp = online.create_online_application(app)\n"
        "    oapp.login(OnlineChangeOption.Try, False)\n"
        "    print('UiPingCount=', oapp.read_value('GVL.UiPingCount'))\n"
        "    oapp.logout()\n"
        "    break\n"
    )
    rep = call({"cmd": "exec", "label": "smoke_read_gvl", "code": code}, timeout=120)
    if not case("ok=True", rep.get("ok") is True,
                "err=%s" % (rep.get("error","")[:120] if not rep.get("ok") else "")):
        failures += 1
    if not case("counter read", "UiPingCount=" in rep.get("stdout","")):
        failures += 1

    # 5. forgot-logout job; safe_logout should clean up so the NEXT call works.
    print("\n5. forgot-logout cleanup")
    forgot_code = (
        "for app in list(projects.primary.find('Application', True) or []):\n"
        "    oapp = online.create_online_application(app)\n"
        "    oapp.login(OnlineChangeOption.Try, False)\n"
        "    print('logged in, NOT calling logout')\n"
        "    break\n"
    )
    rep = call({"cmd": "exec", "label": "smoke_forgot_logout", "code": forgot_code}, timeout=120)
    if not case("forgot-logout job ran ok", rep.get("ok") is True,
                "err=%s" % (rep.get("error","")[:120] if not rep.get("ok") else "")):
        failures += 1

    # Same code immediately after -- if safe_logout didn't fire, this hangs or fails.
    rep = call({"cmd": "exec", "label": "smoke_relogin", "code": forgot_code}, timeout=120)
    if not case("relogin still works (proves safe_logout cleaned up)",
                rep.get("ok") is True,
                "err=%s" % (rep.get("error","")[:120] if not rep.get("ok") else "")):
        failures += 1

    print("\n=== %s ===" % ("ALL PASS" if failures == 0 else ("%d FAIL" % failures)))
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
