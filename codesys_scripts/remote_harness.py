#!/usr/bin/env python3
"""
remote_harness.py  --  one-way bridge so an outside driver (the assistant)
can push instructions into the UI and read back results.

HTTP endpoints (bind 127.0.0.1 only):
  GET  /poll?ui=<id>        UI long-poll. Returns the next instruction
                             for this UI ("" if none) as JSON.
  POST /result               UI posts {instr_id, ok, value|err, elapsed_ms}
                             after executing an instruction.
  POST /push                 Driver posts an instruction
                             {action, payload, wait_for_result:bool}.
                             If wait_for_result is true, the response blocks
                             until the UI POSTs /result for that id (or
                             timeout). Otherwise returns instr_id right away.
  GET  /status               Queue/result snapshot for debugging.

This is a *development* tool -- no auth, no TLS; only binds to loopback.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict


class Harness:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: "queue.Queue[dict]" = queue.Queue()
        # results keyed by instr_id; each entry is a list of (event, data)
        self.results: Dict[str, Dict[str, Any]] = {}
        # waiters keyed by instr_id: threading.Event
        self.waiters: Dict[str, threading.Event] = {}
        # log of recent UI-side results for /status
        self.recent: list = []

    def push(self, action: str, payload: Any) -> str:
        instr_id = uuid.uuid4().hex[:10]
        entry = {"instr_id": instr_id, "action": action, "payload": payload}
        with self.lock:
            ev = threading.Event()
            self.waiters[instr_id] = ev
        self.pending.put(entry)
        return instr_id

    def wait_result(self, instr_id: str, timeout: float) -> Dict[str, Any]:
        with self.lock:
            ev = self.waiters.get(instr_id)
        if ev is None:
            return {"ok": False, "err": f"no such instr_id {instr_id}"}
        try:
            if not ev.wait(timeout):
                return {"ok": False, "err": "harness wait timeout"}
            with self.lock:
                return self.results.get(instr_id, {"ok": False, "err": "missing result"})
        finally:
            # Prune both waiter and result once the caller has consumed (or
            # given up). Otherwise these dicts grow unbounded over a long
            # session and /status becomes huge.
            with self.lock:
                self.waiters.pop(instr_id, None)
                self.results.pop(instr_id, None)

    def record_result(self, data: Dict[str, Any]) -> None:
        instr_id = data.get("instr_id")
        if not instr_id:
            return
        with self.lock:
            self.results[instr_id] = data
            ev = self.waiters.get(instr_id)
            self.recent.append({"t": time.time(), **data})
            self.recent[: -50] = []  # trim to last 50
        if ev:
            ev.set()

    def next_instr(self, timeout: float = 0.0) -> Dict[str, Any]:
        try:
            return self.pending.get(timeout=timeout) if timeout > 0 else self.pending.get_nowait()
        except queue.Empty:
            return {}


state = Harness()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        # Suppress noisy default access log; keep stderr clean for real errors.
        pass

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # UI is served from a different dev origin; CORS gets us past that.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json({})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/poll"):
            # Short poll (UI is already on a timer, no need to long-poll).
            instr = state.next_instr(timeout=0.0)
            return self._send_json(instr)
        if self.path.startswith("/status"):
            with state.lock:
                snap = {
                    "queued": state.pending.qsize(),
                    "waiters": list(state.waiters.keys()),
                    "recent": list(state.recent),
                }
            return self._send_json(snap)
        self._send_json({"err": "unknown path"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as ex:
            return self._send_json({"err": f"bad json: {ex}"}, status=400)

        if self.path.startswith("/result"):
            state.record_result(data)
            return self._send_json({"ok": True})

        if self.path.startswith("/push"):
            action = data.get("action")
            payload = data.get("payload", {})
            wait = bool(data.get("wait_for_result", False))
            timeout = float(data.get("timeout", 10.0))
            if not action:
                return self._send_json({"err": "missing action"}, status=400)
            instr_id = state.push(action, payload)
            if not wait:
                return self._send_json({"instr_id": instr_id, "queued": True})
            result = state.wait_result(instr_id, timeout)
            return self._send_json({"instr_id": instr_id, "result": result})

        self._send_json({"err": "unknown path"}, status=404)


class ThreadingHTTPServer(HTTPServer):
    """One thread per request so /push can block without stalling /poll."""

    def process_request(self, request, client_address):  # type: ignore[override]
        t = threading.Thread(
            target=self._handle, args=(request, client_address), daemon=True
        )
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        finally:
            self.shutdown_request(request)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8127)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"remote_harness up at http://{args.host}:{args.port}", flush=True)
    print("  UI polls:  GET  /poll", flush=True)
    print("  UI posts:  POST /result", flush=True)
    print("  driver:    POST /push  body={action,payload,wait_for_result?,timeout?}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
