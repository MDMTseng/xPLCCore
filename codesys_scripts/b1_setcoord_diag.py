#!/usr/bin/env python3
"""
b1_setcoord_diag.py -- B1 SetCoord1 stall diagnostic.

Drives the PLC from UnInited -> Ready, sends SetCoord1, then dumps the
DBG_SetCoord* GVL fields the PLC code latched along the way. Compares
RuntimeMs progression against wall-clock to detect cyclic-task stalls.

Prereq:
  - daemon.py running in CODESYS Scripting Console
  - PLC project loaded (online change of GVL + AxisGroupSM has been done)
  - Virtual-motors mode forced (this script does it; gate has 10-min TTL)

Usage:
  python codesys_scripts/b1_setcoord_diag.py
"""
from __future__ import annotations
import json, os, socket, struct, subprocess, sys, threading, time

import msgpack

PLC_HOST, PLC_PORT = "192.168.1.70", 8125
HERE = os.path.dirname(os.path.abspath(__file__))
T0 = time.time()


def ts() -> str:
    return f"{(time.time() - T0):7.3f}"


def rpc_exec(file_rel: str, label: str, timeout: float = 60) -> str:
    """Run a daemon job, capture stdout. Raises on non-zero exit."""
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "rpc.py"),
         "exec", "--file", os.path.join(HERE, file_rel),
         "--label", label, "--timeout", str(timeout)],
        capture_output=True, text=True, timeout=timeout + 30,
    )
    if out.returncode != 0:
        sys.stderr.write(f"[diag] rpc {label} FAILED rc={out.returncode}\n"
                         f"stdout:\n{out.stdout}\nstderr:\n{out.stderr}\n")
        raise RuntimeError(f"daemon RPC {label} failed")
    return out.stdout


def parse_dbg(blob: str) -> dict:
    """b1_read_dbg.py emits 'KEY=VALUE' lines. Strip CODESYS type prefixes."""
    out: dict[str, str] = {}
    out["wall_t"] = ""
    for line in blob.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip()
        # CODESYS read_value returns e.g. "LINT#1234", "UDINT#7", "TRUE", "FALSE"
        # Keep raw for forensic clarity but also stash a numeric form when easy.
        out[k] = v
    return out


def dbg_int(d: dict, key: str) -> int | None:
    v = d.get(key)
    if v is None or v.startswith("ERR:"):
        return None
    if "#" in v:
        v = v.split("#", 1)[1]
    try:
        return int(v)
    except ValueError:
        return None


def main() -> int:
    print(f"[{ts()}] b1 setcoord diagnostic start")

    # 1) Force virtual-motors gate.
    print(f"[{ts()}] forcing virtual motors")
    print(rpc_exec("jobs/templates/virtual_motors_force.py", "virtual_motors_force"))

    # 2) Open TCP, start RX reader + 2Hz PING keepalive.
    sock = socket.create_connection((PLC_HOST, PLC_PORT), timeout=5)
    lock = threading.Lock()
    stop = threading.Event()
    rx_log: list[tuple[float, dict]] = []

    def reader():
        unp = msgpack.Unpacker(raw=False)
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                buf = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not buf:
                return
            unp.feed(buf)
            for obj in unp:
                rx_log.append((time.time(), obj))
                # only print interesting frames live
                if isinstance(obj, dict):
                    name = obj.get("name") or obj.get("cmd") or ""
                    if name in ("ST_CHG", "COORD_SET", "MOVE_DONE") or obj.get("ack") is False:
                        print(f"[{ts()}] RX {obj}")

    t_rx = threading.Thread(target=reader, daemon=True); t_rx.start()

    def send(pkt: dict) -> None:
        with lock:
            sock.sendall(msgpack.packb(pkt, use_bin_type=True))

    ping_id = [10_000]
    def ping_loop():
        while not stop.is_set():
            ping_id[0] += 1
            try:
                send({"id": ping_id[0], "type": "SYS", "cmd": "PING"})
            except OSError:
                return
            time.sleep(0.5)
    threading.Thread(target=ping_loop, daemon=True).start()

    def drive(ev: int, label: str, wait: float = 0.8) -> None:
        print(f"[{ts()}] TX GA_EV {label} ({ev})")
        send({"id": ev, "type": "SYS", "cmd": "GA_EV", "ev": ev})
        time.sleep(wait)

    time.sleep(2.0)  # let PINGs settle
    drive(8, "EV_RESET", 0.5)
    drive(2, "EV_POWER_ON", 0.8)
    drive(4, "EV_GROUP_ENABLE", 0.8)
    drive(7, "EV_HOME_GO_FORCE_SKIP", 1.5)

    # 3) Snapshot DBG before SetCoord1.
    print(f"[{ts()}] reading DBG snapshot (pre-SetCoord1)")
    pre_blob = rpc_exec("jobs/templates/b1_read_dbg.py", "b1_dbg_pre")
    pre = parse_dbg(pre_blob)
    print(pre_blob)

    # 4) Trigger SetCoord1.
    setcoord_wall = time.time()
    print(f"[{ts()}] TX SetCoord1 wall={setcoord_wall:.6f}")
    # NOTE: type:'M' is required. Without it, the dispatcher skips
    # ProcessMotionPacket entirely and the packet sits in the ring blocking
    # PINGs queued behind it -- which is what the original B1 'stall' actually
    # was. Real UI senders all set type:'M' on SetCoord0/SetCoord1.
    send({"id": 301, "type": "M", "cmd": "SetCoord1"})

    # 5) Watch for ~8s — covers the 5s supervisor-trip plus settle.
    time.sleep(8.0)

    # 6) Snapshot DBG after.
    print(f"[{ts()}] reading DBG snapshot (post)")
    post_blob = rpc_exec("jobs/templates/b1_read_dbg.py", "b1_dbg_post")
    post = parse_dbg(post_blob)
    post_wall = time.time()
    print(post_blob)

    stop.set()
    try:
        sock.close()
    except OSError:
        pass

    # 7) Compute deltas.
    print()
    print("=" * 60)
    print("TIMELINE")
    print("=" * 60)
    pre_runtime  = dbg_int(pre,  "AxisGroupSM.RuntimeMs")
    post_runtime = dbg_int(post, "AxisGroupSM.RuntimeMs")
    pre_scans    = dbg_int(pre,  "GVL.AxisGroupSMScans")
    post_scans   = dbg_int(post, "GVL.AxisGroupSMScans")
    pre_pings    = dbg_int(pre,  "GVL.UiPingCount")
    post_pings   = dbg_int(post, "GVL.UiPingCount")

    wall_window = post_wall - setcoord_wall
    print(f"wall window from SetCoord1 to post snap : {wall_window:.3f}s")
    if pre_runtime is not None and post_runtime is not None:
        rt_delta_ms = post_runtime - pre_runtime
        print(f"PLC RuntimeMs delta (pre->post)         : {rt_delta_ms} ms"
              f"  (pre={pre_runtime}, post={post_runtime})")
    if pre_scans is not None and post_scans is not None:
        scans = post_scans - pre_scans
        print(f"AxisGroupSM scans (pre->post)           : {scans}")
        if pre_runtime is not None and post_runtime is not None and scans:
            print(f"  avg scan period                       : "
                  f"{(post_runtime - pre_runtime)/scans:.3f} ms/scan")
    if pre_pings is not None and post_pings is not None:
        print(f"PING count delta (pre->post)            : {post_pings - pre_pings}")

    print()
    print("DBG_SetCoord* (latched by PLC):")
    accept = dbg_int(post, "GVL.DBG_SetCoord1AcceptMs")
    rise   = dbg_int(post, "GVL.DBG_SetCoordExecuteRiseMs")
    busy   = dbg_int(post, "GVL.DBG_SetCoordBusyFirstMs")
    done   = dbg_int(post, "GVL.DBG_SetCoordDoneMs")
    err    = dbg_int(post, "GVL.DBG_SetCoordErrorMs")
    fall   = dbg_int(post, "GVL.DBG_SetCoordExecuteFallMs")
    print(f"  accept       (RuntimeMs) = {accept}")
    print(f"  Execute rise (RuntimeMs) = {rise}"
          f"  (delta from accept = {None if rise is None or accept is None else rise - accept})")
    print(f"  Busy first   (RuntimeMs) = {busy}"
          f"  (delta from rise   = {None if busy is None or rise is None else busy - rise})")
    print(f"  Done         (RuntimeMs) = {done}"
          f"  (delta from rise   = {None if done is None or rise is None else done - rise})")
    print(f"  Error        (RuntimeMs) = {err}"
          f"  (delta from rise   = {None if err is None or rise is None else err - rise})")
    print(f"  Execute fall (RuntimeMs) = {fall}"
          f"  (delta from rise   = {None if fall is None or rise is None else fall - rise})")
    print(f"  ErrorID                  = {post.get('GVL.DBG_SetCoordLastErrorID')}")
    print(f"  Last error src           = {post.get('GVL.LastErrorSource')}")
    print(f"  Last error id            = {post.get('GVL.LastErrorID')}")
    print(f"  Final state              = {post.get('AxisGroupSM.AxisGroupManagerFb._eState')}")
    print(f"  CoordSystemConfigured    = {post.get('GVL.CoordSystemConfigured')}")

    print()
    print(f"RX frames captured: {len(rx_log)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
