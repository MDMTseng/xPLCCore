#!/usr/bin/env python3
"""
mock_plc.py  --  stand-in PLC for exercising the UI dispatcher.

Listens on two sockets:
  * DATA  (default 8125)     -- msgpack request/reply, same wire format
                                 as FB_TcpMsgPakServer on the real PLC.
  * CTRL  (default 8126)     -- line-based control channel. Send scenario
                                 commands here from a terminal / another
                                 tool to change how the mock responds.

Default behaviour: every incoming request with an `id` field gets a
`{id, ack: True}` reply after a few ms. Change that with CTRL commands:

  ack                         -- default mode, reply {id, ack:True}
  nak [err_msg]               -- reply {id, ack:False, err:...}
  drop [N]                    -- silently swallow the next N requests
                                 (default 1), then revert to ack mode
  delay_ms <N>                -- artificial reply latency in milliseconds
  disconnect                  -- close the client socket now
  unsolicited <json>          -- push a raw msgpack payload to the client
                                 with no prompting; used to exercise
                                 cross-talk / NAK-without-request paths
  status                      -- print current scenario state
  quit                        -- stop the mock server

State is process-global; only one client is supported at a time. The UI
reconnects automatically so `disconnect` is a useful stress button.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import msgpack


@dataclass
class State:
    mode: str = "ack"               # ack | nak | drop
    drop_remaining: int = 0
    nak_err: str = "mock nak"
    reply_delay_ms: int = 0
    client: Optional[socket.socket] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def log(msg: str) -> None:
    # Flush so the operator sees activity in real time.
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def send_reply(sock: socket.socket, reply: dict) -> None:
    payload = msgpack.packb(reply, use_bin_type=True)
    sock.sendall(payload)


def handle_request(state: State, sock: socket.socket, req: dict) -> None:
    rid = req.get("id")
    if rid is None:
        log(f"request without id, ignoring: keys={list(req.keys())}")
        return

    with state.lock:
        mode = state.mode
        delay_ms = state.reply_delay_ms
        if mode == "drop":
            state.drop_remaining -= 1
            if state.drop_remaining <= 0:
                state.mode = "ack"
                state.drop_remaining = 0
            log(f"DROP id={rid} cmd={req.get('cmd')} (remaining drops: {state.drop_remaining})")
            return
        err = state.nak_err

    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    if mode == "ack":
        reply = {"id": rid, "ack": True}
        # Echo cmd/type so the client can sanity-check routing.
        if "cmd" in req:
            reply["cmd"] = req["cmd"]
        if "type" in req:
            reply["type"] = req["type"]
        log(f"ACK  id={rid} type={req.get('type')} cmd={req.get('cmd')}")
    else:  # nak
        reply = {"id": rid, "ack": False, "err": err}
        log(f"NAK  id={rid} err={err!r}")

    try:
        send_reply(sock, reply)
    except OSError as ex:
        log(f"reply send failed: {ex}")


def data_client_loop(state: State, sock: socket.socket, addr) -> None:
    log(f"client connected: {addr}")
    with state.lock:
        state.client = sock
    try:
        unpacker = msgpack.Unpacker(raw=False)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                log("client closed (EOF)")
                return
            unpacker.feed(chunk)
            try:
                for obj in unpacker:
                    if isinstance(obj, dict):
                        handle_request(state, sock, obj)
                    else:
                        log(f"non-map frame: {obj!r}")
            except msgpack.exceptions.ExtraData as ex:
                log(f"unpacker extra data: {ex}")
            except Exception as ex:
                log(f"unpacker error: {ex}")
                return
    except OSError as ex:
        log(f"socket error: {ex}")
    finally:
        try:
            sock.close()
        except OSError:
            pass
        with state.lock:
            if state.client is sock:
                state.client = None
        log(f"client gone: {addr}")


def data_server_loop(state: State, host: str, port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    log(f"data server listening on {host}:{port}")
    while True:
        sock, addr = srv.accept()
        # Close any previous client so "only one connection" stays true.
        with state.lock:
            prev = state.client
        if prev is not None and prev is not sock:
            try:
                prev.shutdown(socket.SHUT_RDWR)
                prev.close()
            except OSError:
                pass
        t = threading.Thread(
            target=data_client_loop, args=(state, sock, addr), daemon=True
        )
        t.start()


def apply_ctrl(state: State, line: str) -> str:
    parts = line.strip().split(None, 1)
    if not parts:
        return "noop"
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "ack":
        with state.lock:
            state.mode = "ack"
            state.drop_remaining = 0
        return "mode=ack"

    if cmd == "nak":
        with state.lock:
            state.mode = "nak"
            state.nak_err = arg or "mock nak"
        return f"mode=nak err={state.nak_err!r}"

    if cmd == "drop":
        n = int(arg) if arg else 1
        with state.lock:
            state.mode = "drop"
            state.drop_remaining = n
        return f"mode=drop count={n}"

    if cmd == "delay_ms":
        n = int(arg) if arg else 0
        with state.lock:
            state.reply_delay_ms = n
        return f"delay_ms={n}"

    if cmd == "disconnect":
        with state.lock:
            sock = state.client
            state.client = None
        if sock is None:
            return "no client connected"
        try:
            sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        except OSError as ex:
            return f"close failed: {ex}"
        return "disconnected"

    if cmd == "unsolicited":
        try:
            obj = json.loads(arg) if arg else {}
        except json.JSONDecodeError as ex:
            return f"bad json: {ex}"
        with state.lock:
            sock = state.client
        if sock is None:
            return "no client connected"
        try:
            send_reply(sock, obj)
        except OSError as ex:
            return f"send failed: {ex}"
        return f"sent: {obj}"

    if cmd == "status":
        with state.lock:
            connected = state.client is not None
            return (
                f"mode={state.mode} drop_remaining={state.drop_remaining} "
                f"delay_ms={state.reply_delay_ms} client_connected={connected}"
            )

    if cmd == "quit":
        return "quit"

    return f"unknown command: {cmd!r}"


def ctrl_client_loop(state: State, sock: socket.socket, addr) -> None:
    log(f"ctrl client connected: {addr}")
    try:
        buf = b""
        sock.sendall(b"mock_plc ready. try: ack | nak [msg] | drop [n] | delay_ms N | disconnect | status | quit\n> ")
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                nl = buf.index(b"\n")
                line = buf[:nl].decode("utf-8", "replace")
                buf = buf[nl + 1 :]
                result = apply_ctrl(state, line)
                log(f"CTRL {line!r} -> {result}")
                try:
                    sock.sendall((result + "\n> ").encode("utf-8"))
                except OSError:
                    return
                if result == "quit":
                    import os
                    os._exit(0)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def ctrl_server_loop(state: State, host: str, port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(4)
    log(f"ctrl server listening on {host}:{port}")
    while True:
        sock, addr = srv.accept()
        t = threading.Thread(
            target=ctrl_client_loop, args=(state, sock, addr), daemon=True
        )
        t.start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-port", type=int, default=8125)
    ap.add_argument("--ctrl-port", type=int, default=8126)
    args = ap.parse_args()

    state = State()
    t_data = threading.Thread(
        target=data_server_loop, args=(state, args.host, args.data_port), daemon=True
    )
    t_ctrl = threading.Thread(
        target=ctrl_server_loop, args=(state, args.host, args.ctrl_port), daemon=True
    )
    t_data.start()
    t_ctrl.start()

    log(f"mock_plc up. data=tcp://{args.host}:{args.data_port} ctrl=tcp://{args.host}:{args.ctrl_port}")
    log("point the UI at the data port and use `nc 127.0.0.1 8126` (or tcp_ctrl.py) to drive scenarios")

    # Stay alive until killed. (Threads are daemon=True so Ctrl-C exits.)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
