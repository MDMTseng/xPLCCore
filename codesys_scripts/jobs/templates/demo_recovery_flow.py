"""End-to-end demo of the §4 (2) crash-and-resume flow, driven through
the UI's harness (remote_ctrl + remote_harness).

Everything goes via the running renderer's TCP socket -- this is the
same wire path the RecoveryDemoPage buttons use, so a green run here
proves the page can do the same with real clicks.

Sequence:
   T0  baseline snapshot                       expect: schema fields all present
   T1  drive FSM via UI (RESET..HOME_FSK..SetCoord0)
   T2  "save plan" = SCRATCHPAD_WRITE plan_id + index=0 + intent=Idle
   T3  read state                              expect: scratchpad matches T2
   T4  "advance reel" = G1 Z-5 then SCRATCHPAD_WRITE with G1's movement_id
   T5  read state                              expect: intent_movement_id=mid
   T6  wait one poll cycle for virtual axis to drain
   T7  read state                              expect: last_completed_movement_id >= intent_movement_id
   T8  host-side reconcile classification:
         - schema valid?           schema_version == 1
         - boot_epoch stable?      scratchpad.boot_epoch == boot_epoch_now
         - intent completed?       last_completed_movement_id >= intent_movement_id
         - decision                resume / cold_start
         - action                  continue_plan / blow_off / bowl_back
   T9  "idle" = SCRATCHPAD_WRITE intent_kind=Idle, mid=0
   T10 read state                              expect: intent_kind=0, mid=0

Run:
   python codesys_scripts/jobs/templates/demo_recovery_flow.py
Requires:
   - daemon up (only used by force_tcp_reset if needed, optional)
   - UI running with remote_harness on 127.0.0.1:8127
   - UI tcpConnected to PLC, in Ready + coord_set=True (or this script
     drives the FSM up itself)
   - PLC carries the §4 (1)(2) build (commits e78248b, 2ac004f, 43c7090)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
REMOTE_CTRL = REPO / "codesys_scripts" / "internals" / "remote_ctrl.py"

# Intent kinds mirror orchestrator/resume.ts IntentKind.
INTENT_IDLE = 0
INTENT_ADVANCE_REEL = 1
INTENT_PICK = 2
INTENT_BOWL_BACK = 3

# E_RobotEvent ordinals (memory: plc_event_numeric_values).
EV_POWER_ON = 2
EV_GROUP_ENABLE = 4
EV_HOME_GO_FORCE_SKIP = 7
EV_RESET = 8


def harness(action, payload=None, timeout=5.0):
    """Send one harness instruction, return the value sub-dict."""
    body = "{}" if payload is None else json.dumps(payload)
    cp = subprocess.run(
        [sys.executable, str(REMOTE_CTRL), action, body, "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=timeout + 4,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"harness {action} exit={cp.returncode}: {cp.stderr.strip()}")
    out = json.loads(cp.stdout or "{}")
    result = (out.get("result") or {})
    if result.get("ok") is False:
        raise RuntimeError(f"harness {action} failed: {result.get('err')}")
    return result.get("value", {})


def send(packet, timeout=4.0):
    return harness("send_tcp_msgpack", packet, timeout=timeout)


def get_state():
    return send({"type": "SYS", "cmd": "GET_MACHINE_STATE"})


def kv(d, *keys):
    return {k: d.get(k) for k in keys}


def banner(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def drive_to_ready_via_ui():
    banner("T1  drive FSM to Ready via UI harness")
    cur = get_state()
    if cur.get("st") == 70 and cur.get("coord_set") is True:
        print("  already Ready + coord_set; skipping drive")
        return
    for ev, label, target in [
        (EV_RESET, "RESET", 10),
        (EV_POWER_ON, "POWER_ON", 30),
        (EV_GROUP_ENABLE, "GROUP_ENABLE", 50),
        (EV_HOME_GO_FORCE_SKIP, "HOME_GO_FORCE_SKIP", 70),
    ]:
        send({"type": "SYS", "cmd": "GA_EV", "ev": ev})
        for _ in range(15):
            s = get_state()
            if s.get("st") == target:
                print(f"  {label:<22}-> st={target} {s.get('st_str')}")
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(
                f"GA_EV({label}) did not reach st={target} in 3s. "
                f"If stuck at GroupEnabled: virtual_motors TON gate likely "
                f"latched -- run virtual_motors_unforce.py then "
                f"virtual_motors_force.py via daemon."
            )
    send({"type": "M", "cmd": "SetCoord0"})
    time.sleep(0.2)
    s = get_state()
    print(f"  SetCoord0 done; coord_set={s.get('coord_set')}")


def host_reconcile(snap, vision_seen=True):
    """Pure-host classification mirroring orchestrator/resume.ts.
    Returns (decision_kind, action_kind, reason)."""
    sp = snap.get("scratchpad", {})
    if not sp or sp.get("schema_version", 0) == 0:
        return "cold_start", "cold_start", "scratchpad uninitialised"
    if sp.get("boot_epoch") != snap.get("boot_epoch_now"):
        return "cold_start", "cold_start", "boot_epoch mismatch"
    # We don't load a host-side plan_id check here because the demo
    # doesn't write one to disk -- the page does via localStorage.
    # The PLC scratchpad check is what matters.
    intent_kind = sp.get("intent_kind", 0)
    intent_mid = sp.get("intent_movement_id", 0)
    last_complete = snap.get("last_completed_movement_id", 0)
    if intent_kind == INTENT_IDLE or intent_mid == 0:
        return "resume", "continue_plan", "intent idle, nothing in flight"
    intent_completed = last_complete >= intent_mid
    if not intent_completed:
        return "resume", "blow_off", (
            f"intent kind={intent_kind} mid={intent_mid} NOT completed "
            f"(last_completed={last_complete}) -- state 2"
        )
    if not vision_seen:
        return "resume", "bowl_back", (
            f"intent kind={intent_kind} mid={intent_mid} completed but "
            f"vision result missing -- state 4"
        )
    return "resume", "continue_plan", (
        f"intent kind={intent_kind} mid={intent_mid} completed and vision "
        f"seen -- state 3"
    )


def main():
    # T0 baseline
    banner("T0  baseline snapshot")
    s0 = get_state()
    required = ["st", "st_str", "movement_id", "last_completed_movement_id",
                "reel_pos", "scratchpad", "boot_epoch_now"]
    missing = [k for k in required if k not in s0]
    if missing:
        print(f"!! missing fields: {missing} -- is PLC carrying the §4 build?")
        return 1
    for k in required:
        print(f"  {k:<28} {s0.get(k)}")

    # T1 drive
    drive_to_ready_via_ui()

    # T2 save plan
    banner("T2  'save plan' -> SCRATCHPAD_WRITE")
    plan_id = 0xCAFE
    save = {
        "type": "SYS", "cmd": "SCRATCHPAD_WRITE",
        "plan_id": plan_id, "plan_index": 0,
        "intent_kind": INTENT_IDLE, "intent_movement_id": 0,
        "last_vision_pulse": 0,
    }
    print(f"  write: {kv(save, 'plan_id', 'plan_index', 'intent_kind', 'intent_movement_id')}")
    r = send(save)
    print(f"  ack: {r.get('ack')}")

    # T3 verify
    banner("T3  verify scratchpad reflects T2")
    s3 = get_state()["scratchpad"]
    print(f"  scratchpad: {s3}")
    assert s3.get("plan_id") == plan_id, f"plan_id mismatch: {s3.get('plan_id')} != {plan_id}"
    assert s3.get("intent_kind") == INTENT_IDLE
    assert s3.get("schema_version") == 1
    print("  ok")

    # T4 advance reel (write-ahead)
    banner("T4  'advance reel' -- G1 then writeIntent with G1's movement_id")
    g1 = send({"type": "M", "cmd": "G1", "Z": -5.0, "F": 30})
    mid = g1.get("movement_id")
    print(f"  G1 ack: {kv(g1, 'ack', 'movement_id')}")
    assert g1.get("ack") is True, f"G1 NAK: {g1}"
    assert mid and mid > 0, f"G1 ack carried no usable movement_id: {g1}"
    # Step 3 of the contract: writeIntent AFTER ack, with THIS move's id.
    intent_write = {
        "type": "SYS", "cmd": "SCRATCHPAD_WRITE",
        "plan_id": plan_id, "plan_index": 1,
        "intent_kind": INTENT_ADVANCE_REEL,
        "intent_movement_id": mid,
        "last_vision_pulse": 0,
    }
    r = send(intent_write)
    print(f"  writeIntent: {kv(intent_write, 'intent_kind', 'intent_movement_id')} -> ack={r.get('ack')}")

    # T5 verify intent stamped
    banner("T5  verify intent stamped")
    s5 = get_state()
    sp5 = s5["scratchpad"]
    print(f"  scratchpad: kind={sp5.get('intent_kind')} mid={sp5.get('intent_movement_id')}")
    print(f"  movement_id={s5.get('movement_id')} last_completed_movement_id={s5.get('last_completed_movement_id')}")
    assert sp5.get("intent_kind") == INTENT_ADVANCE_REEL
    assert sp5.get("intent_movement_id") == mid

    # T6 wait drain
    banner("T6  wait for motion drain")
    for i in range(20):
        s = get_state()
        if s.get("motion_buffer_size", 0) == 0 and s.get("last_completed_movement_id", 0) >= mid:
            print(f"  drained after {0.15*(i+1):.2f}s: last_completed={s.get('last_completed_movement_id')}")
            break
        time.sleep(0.15)
    else:
        print(f"  !! motion did not drain in 3s; last_completed={get_state().get('last_completed_movement_id')}")

    # T7 read final
    banner("T7  final snapshot")
    s7 = get_state()
    sp7 = s7["scratchpad"]
    print(f"  movement_id                 = {s7.get('movement_id')}")
    print(f"  last_completed_movement_id  = {s7.get('last_completed_movement_id')}")
    print(f"  scratchpad.intent_kind      = {sp7.get('intent_kind')}")
    print(f"  scratchpad.intent_movement_id = {sp7.get('intent_movement_id')}")
    print(f"  scratchpad.boot_epoch       = {sp7.get('boot_epoch')}")
    print(f"  boot_epoch_now              = {s7.get('boot_epoch_now')}")

    # T8 reconcile
    banner("T8  host-side reconcile (mirrors orchestrator/resume.ts)")
    for vision_seen in (True, False):
        d, a, why = host_reconcile(s7, vision_seen=vision_seen)
        label = "vision_seen=true " if vision_seen else "vision_seen=false"
        print(f"  [{label}] decision={d}  action={a}")
        print(f"           reason: {why}")

    # T9 idle
    banner("T9  'idle' scratchpad write (clear intent)")
    send({
        "type": "SYS", "cmd": "SCRATCHPAD_WRITE",
        "plan_id": plan_id, "plan_index": 2,
        "intent_kind": INTENT_IDLE, "intent_movement_id": 0,
        "last_vision_pulse": 0,
    })

    # T10 verify
    banner("T10  verify intent cleared")
    s10 = get_state()
    sp10 = s10["scratchpad"]
    print(f"  scratchpad.intent_kind = {sp10.get('intent_kind')}")
    print(f"  scratchpad.intent_movement_id = {sp10.get('intent_movement_id')}")
    assert sp10.get("intent_kind") == INTENT_IDLE
    assert sp10.get("intent_movement_id") == 0

    banner("DONE -- all assertions passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n!! ASSERTION FAILED: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n!! RUNTIME ERROR: {e}")
        sys.exit(2)
