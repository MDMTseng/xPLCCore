"""Bring the CODESYS RPC daemon back up.

Daemon lives inside the CODESYS Scripting Console (`oapp` and friends
need the IDE's scripting context, so we can't run it as a standalone
process). When it dies, *something* still has to relaunch it inside
that context.

This script handles both cases:
  1. CODESYS IDE not running -> spawn it with --runscript=daemon.py so
     the daemon starts in the IDE's Scripting Console as soon as the
     IDE finishes loading the project. Hands-off recovery.
  2. CODESYS IDE running (Scripting Console died, IDE didn't) -> can't
     re-execute daemon.py without user action; print the exact path so
     a paste into the console finishes the job. This is the realistic
     ceiling without UI automation.

Idempotent: if the daemon is already alive (TCP probe on 127.0.0.1:7420
succeeds), exits 0 without doing anything.

Usage:
    python daemon_kickstart.py                # autodetect + kickstart
    python daemon_kickstart.py --force-launch # always spawn IDE (debug)
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAEMON_PATH = HERE / "daemon.py"
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 7420

# Pulled from build.sh on 2026-06-17. If you upgrade CODESYS, update
# build.sh and this constant in lockstep. Hardcoded rather than scanned
# because Program Files layout differs per install and an Explorer scan
# costs more than the maintenance.
CODESYS_EXE = r"C:\Program Files\CODESYS 3.5.21.20\CODESYS\Common\CODESYS.exe"
CODESYS_PROFILE = "CODESYS V3.5 SP21 Patch 20"
PROJECT_PATH = r"C:\Users\X1\Desktop\XPack2_codesys\PackerX.project"


def _daemon_alive(timeout: float = 1.5) -> bool:
    """TCP probe: ping the daemon and look for a successful reply."""
    try:
        with socket.create_connection((DAEMON_HOST, DAEMON_PORT), timeout=timeout) as s:
            s.sendall((json.dumps({"cmd": "ping"}) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                data += chunk
            rep = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            return bool(rep.get("ok"))
    except (OSError, ValueError):
        return False


def _codesys_running() -> bool:
    """tasklist | findstr CODESYS.exe -- if any process matches we assume
    the IDE is up. False positives don't matter; we only use this to
    decide whether to spawn a new IDE or print instructions."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq CODESYS.exe"],
            text=True, stderr=subprocess.DEVNULL,
        )
        return "CODESYS.exe" in out
    except Exception:
        return False


def _spawn_ide_with_daemon():
    """Launch CODESYS with --runscript=daemon.py so the IDE auto-executes
    daemon.py in its Scripting Console once the project is loaded. The
    daemon's accept loop then takes over. We don't wait for the IDE to
    fully boot here -- the caller can poll _daemon_alive() if needed."""
    if not Path(CODESYS_EXE).exists():
        print(f"ERROR: CODESYS.exe not found at {CODESYS_EXE}", file=sys.stderr)
        print("  Edit daemon_kickstart.py CODESYS_EXE constant to match your install.",
              file=sys.stderr)
        return 3

    cmd = [
        CODESYS_EXE,
        f"--profile={CODESYS_PROFILE}",
        f"--runscript={DAEMON_PATH}",
        f"--scriptargs={PROJECT_PATH}",
    ]
    print("launching:", " ".join(f'"{x}"' if " " in x else x for x in cmd))
    subprocess.Popen(cmd, close_fds=False)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force-launch", action="store_true",
                    help="skip the alive probe and always spawn CODESYS")
    ap.add_argument("--wait", type=float, default=0,
                    help="after launch, poll daemon for up to N seconds")
    args = ap.parse_args()

    if not args.force_launch and _daemon_alive():
        print("daemon already alive on %s:%d -- nothing to do" % (DAEMON_HOST, DAEMON_PORT))
        return 0

    if _codesys_running():
        print("CODESYS IDE appears to be running but daemon is down.")
        print("Open the Scripting Console (Tools > Scripting > Scripting Console)")
        print("and run:")
        print(f"    exec(open(r'{DAEMON_PATH}').read())")
        print("(or use Tools > Scripting > Execute Script File... to pick daemon.py)")
        return 1

    rc = _spawn_ide_with_daemon()
    if rc != 0:
        return rc

    if args.wait > 0:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            if _daemon_alive():
                print(f"daemon came up after {args.wait - (deadline - time.time()):.1f}s")
                return 0
            time.sleep(2.0)
        print(f"daemon did not come up within {args.wait:.0f}s -- IDE may still be loading",
              file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
