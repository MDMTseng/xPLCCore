#!/usr/bin/env python3
"""
setcoord_repro.py -- reproduce SetCoord1->Error trip via direct msgpack TCP.

Sends: EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_GO_FORCE_SKIP to reach Ready,
then SetCoord1. Keeps pinging at 2Hz throughout. Logs all RX including
ST_CHG events with host timestamps so we can correlate with the IDE-side
setcoord_watch.py sample log.
"""
from __future__ import annotations
import socket
import struct
import threading
import time
import msgpack

HOST, PORT = "192.168.1.70", 8125
T0 = time.time()


def ts() -> str:
    return f"{(time.time() - T0):7.3f}"


def send(sock: socket.socket, lock: threading.Lock, pkt: dict) -> None:
    data = msgpack.packb(pkt, use_bin_type=True)
    with lock:
        sock.sendall(data)


def reader(sock: socket.socket, stop: threading.Event, log: list) -> None:
    unpacker = msgpack.Unpacker(raw=False)
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
        unpacker.feed(buf)
        for obj in unpacker:
            line = f"[{ts()}] RX {obj}"
            print(line)
            log.append(line)


def main() -> int:
    log: list = []
    stop = threading.Event()
    sock = socket.create_connection((HOST, PORT), timeout=5)
    lock = threading.Lock()
    t_rx = threading.Thread(target=reader, args=(sock, stop, log), daemon=True)
    t_rx.start()

    ping_id = [10_000]

    def ping_loop():
        while not stop.is_set():
            ping_id[0] += 1
            try:
                send(sock, lock, {"id": ping_id[0], "type": "SYS", "cmd": "PING"})
            except OSError:
                return
            time.sleep(0.5)

    t_ping = threading.Thread(target=ping_loop, daemon=True)
    t_ping.start()

    def drive(ev: int, label: str, wait: float = 0.8) -> None:
        print(f"[{ts()}] TX GA_EV {label} ({ev})")
        send(sock, lock, {"id": ev, "type": "SYS", "cmd": "GA_EV", "ev": ev})
        time.sleep(wait)

    # wait for IDE watcher to get going
    time.sleep(3.0)

    # reset in case we're stuck somewhere
    drive(8, "EV_RESET", 0.5)
    drive(2, "EV_POWER_ON", 0.8)
    drive(4, "EV_GROUP_ENABLE", 0.8)
    drive(7, "EV_HOME_GO_FORCE_SKIP", 1.5)

    # should be Ready now
    print(f"[{ts()}] TX GET_MACHINE_STATE")
    send(sock, lock, {"id": 9001, "type": "SYS", "cmd": "GET_MACHINE_STATE"})
    time.sleep(0.8)

    # the trigger. type:'M' is mandatory; without it the dispatcher's
    # PacketType='M' gate skips the packet and it sits in minfo_buf until
    # the heartbeat supervisor trips Error and the not-Ready drain NAKs
    # the whole ring (this was the original false-positive B1 'stall').
    print(f"[{ts()}] TX SetCoord1 (motion path)")
    send(sock, lock, {"id": 301, "type": "M", "cmd": "SetCoord1"})

    # watch for the trip
    time.sleep(10.0)

    # final snapshot
    print(f"[{ts()}] TX GET_MACHINE_STATE (post)")
    send(sock, lock, {"id": 9002, "type": "SYS", "cmd": "GET_MACHINE_STATE"})
    time.sleep(1.0)

    stop.set()
    try:
        sock.close()
    except OSError:
        pass
    t_rx.join(timeout=1.0)

    print("\n=== SUMMARY ===")
    print(f"packets received: {len(log)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
