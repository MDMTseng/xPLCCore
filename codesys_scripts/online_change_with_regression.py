#!/usr/bin/env python3
"""Wrapper: run import_all + online_change, then immediately re-run the
end-to-end pytest regression. Use after touching anything in the AxisGroupSM
state machine, ProcessMotionPacket dispatcher, or motion-related GVL fields
to catch regressions before the operator does.

Usage:
    python codesys_scripts/online_change_with_regression.py
    python codesys_scripts/online_change_with_regression.py --skip-tests

Pre-reqs:
  - CODESYS RPC daemon running (rpc.py serve)
  - UI running in foreground with remote_harness reachable
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RPC = HERE / "rpc.py"
JOBS = HERE / "jobs" / "templates"


def run_daemon_job(name: str) -> int:
    print(f"\n=== daemon job: {name} ===")
    res = subprocess.run(
        [sys.executable, str(RPC), "exec", "--file", str(JOBS / f"{name}.py")],
        timeout=180,
    )
    return res.returncode


def run_pytest() -> int:
    print("\n=== pytest regression ===")
    res = subprocess.run(
        [sys.executable, "-m", "pytest", str(HERE / "tests"), "-v", "-x"],
        cwd=str(HERE.parent),
    )
    return res.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true",
                    help="push code only; don't run regression after")
    args = ap.parse_args()

    # Stamp build provenance into GVL.st BEFORE import_all. The next
    # SYS/VERSION call from a test or external observer will then return
    # the exact git rev that's compiled into the running PLC.
    print("\n=== stamp build_info ===")
    rc = subprocess.run(
        [sys.executable, str(JOBS / "stamp_build_info.py")],
        timeout=30,
    ).returncode
    if rc != 0:
        print(f"stamp_build_info failed (rc={rc}); aborting.")
        return rc

    rc = run_daemon_job("import_all")
    if rc != 0:
        print(f"import_all failed (rc={rc}); aborting.")
        return rc
    rc = run_daemon_job("online_change")
    if rc != 0:
        print(f"online_change failed (rc={rc}); aborting.")
        return rc

    # Revert the stamp so the on-disk GVL.st stays canonical ('unknown'/0).
    # The compiled PLC already has the stamped values baked in via the
    # import_all -> online_change above; the disk file is just the
    # template the next push will re-stamp.
    print("\n=== revert build_info stamp ===")
    subprocess.run(
        [sys.executable, str(JOBS / "stamp_build_info.py"), "--revert"],
        timeout=30,
    )

    if args.skip_tests:
        print("\nSkipping regression (--skip-tests).")
        return 0

    rc = run_pytest()
    if rc != 0:
        print(f"\nREGRESSION FAILED (pytest rc={rc}). PLC has the new code; "
              "investigate before further changes.")
    else:
        print("\nRegression passed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
