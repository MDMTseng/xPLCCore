#!/usr/bin/env python3
"""
virtual_motors_smoketest.py  --  drive the AxisGroup state machine through the
virtual-motors safety gate.

Prereqs:
  1. CODESYS IDE open, project running.
  2. Run `bash codesys_scripts/run_job.sh virtual_motors_force` first to force
     GVL.bVirtualMotorsMode_Request := TRUE (confirms derived flag is TRUE).
     The 10-minute TTL (GVL.VIRTUAL_MOTORS_TTL) caps how long the gate stays
     open even if you forget to unforce.
  3. UI running with harness toggle ON, connected to the real PLC on 8125.
  4. remote_harness.py running on 127.0.0.1:8127.

What this does:
  Fires SYS/GA_EV events in sequence through the UI's sendTcpMsgPack:
    EV_POWER_ON      (2)   UnInited     -> Powering
    EV_OK           (10)   Powering     -> Powered
    EV_GROUP_ENABLE  (4)   Powered      -> GroupEnabling
    EV_OK           (10)   GroupEnabling-> GroupEnabled
    EV_HOME_GO_FORCE_SKIP (7) GroupEnabled -> Ready  (only if gate open)
  After each fire it prints the {st, st_str} the PLC returned.

  Finally reminds the caller to run virtual_motors_unforce as cleanup.

Note: EV_OK in a real-motor session is emitted by the drive-wrapping FB when
servos settle. For a pure virtual-motors smoketest we fire it manually so the
FSM advances without real hardware.
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE_CTRL = HERE / "remote_ctrl.py"

# E_RobotEvent indices (must match Robot_FBs/E_RobotEvent.st)
EV_POWER_ON = 2
EV_GROUP_ENABLE = 4
EV_HOME_GO_FORCE_SKIP = 7
EV_RESET = 8

# E_RobotState values we expect
READY = 70

# The FSM emits EV_OK internally from Update(0) when the SoftMotion-simulated
# drives report Status/Done. We only fire the user-driven events and wait
# between them so the internal transitions can complete.
SETTLE_SEC = 1.0


def fire(ev: int, label: str) -> dict:
    payload = json.dumps({"type": "SYS", "cmd": "GA_EV", "ev": ev})
    cmd = [sys.executable, str(REMOTE_CTRL), "send_tcp_msgpack", payload, "--timeout", "5"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        out = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        out = {"raw": res.stdout, "stderr": res.stderr}
    result = (out.get("result") or {}).get("value") or out.get("result") or {}
    print(f"[fire] {label:<24} ev={ev:>2} -> {result}")
    return result if isinstance(result, dict) else {}


def main() -> int:
    steps = [
        # EV_RESET first: Transition.st has a universal fallback so any state
        # snaps back to UnInited, letting the smoketest start from a known
        # baseline even if a prior run left the FSM at Ready/Error/etc.
        (EV_RESET,               "EV_RESET",               SETTLE_SEC),
        (EV_POWER_ON,            "EV_POWER_ON",            SETTLE_SEC),
        (EV_GROUP_ENABLE,        "EV_GROUP_ENABLE",        SETTLE_SEC),
        (EV_HOME_GO_FORCE_SKIP,  "EV_HOME_GO_FORCE_SKIP",  0.3),
    ]

    final_state = None
    for ev, label, wait in steps:
        reply = fire(ev, label)
        final_state = reply.get("st", final_state)
        time.sleep(wait)

    # The UI may return {"sync": false} when the PLC state reply doesn't
    # arrive inside the UI's own send-timeout window. Fall back to reading
    # the FSM state directly via the IDE so the smoketest still reports
    # truthfully.
    if final_state != READY:
        time.sleep(1.0)
        plc_ctrl = HERE / "plc_ctrl.py"
        res = subprocess.run(
            [sys.executable, str(plc_ctrl), "read", "AxisGroupSM.AxisGroupManagerFb._eState"],
            capture_output=True, text=True, timeout=60,
        )
        tail = (res.stdout or "").strip().splitlines()[-1:] or [""]
        print(f"[verify] FSM direct read -> {tail[0]}")
        if "Ready" in tail[0]:
            final_state = READY

    print()
    if final_state == READY:
        print(f"PASS -- reached Ready ({READY}); virtual-motors gate is open.")
        rc = 0
    else:
        print(f"FAIL -- final state={final_state}, expected {READY}.")
        print("       If st<50 the force may not be set; run virtual_motors_force first.")
        print("       If st==50 the gate is closed (flag FALSE or TTL expired).")
        rc = 1

    print()
    print("Cleanup: run  bash codesys_scripts/run_job.sh virtual_motors_unforce")
    return rc


if __name__ == "__main__":
    sys.exit(main())
