"""Mock of the Vision Plugin (VisionMaster side) for the all-virtual scene.

    python tools/sim/vision_mock.py [--plc 192.168.1.70:8126] [--port 7950]
                                    [--ng-side 0.05] [--ng-btm 0.03] [--ng-tape 0.02]

The renderer connects to localhost:7950 as it would to VisionMaster. Wire
format (see doc/2-contracts/vision_contract.md and PluginHello.tsx):

  renderer -> vision   JSON objects, each followed by ';'
                       {type, cmd_type, id, ...}  request/reply
  vision -> renderer   JSON objects {"trig_id": n, "data": ...}; the renderer
                       frames them by brace matching, so no delimiter needed
                       trig_id = request id    reply
                       trig_id = 104500 feeder / 114500 side / 124500 bottom /
                                 134500 top    pushed inspection result

When to push: the real cameras are hardware-triggered by PLC output pulses
and the renderer arms its wait *before* the pulse. This polls the PLC's
trigger-edge counters (PRG_SimIo, served at http://<plc>:8126/v) and
pushes one result per new pulse, after a processing delay. The top camera
is pulsed twice per check (side light, then front light) and gets one
push.

It keeps a small model of the world so consecutive results agree with each
other and with what the renderer does:

  feeder plate   parts with positions inside default/calib.json's pixel
                 hull; parts reported pickable are assumed picked by the
                 next shot; vibration commands from the MOCK feeder
                 (serial_ctrl.py, UDP :7951) reshuffle it, the hopper
                 command (0x1D) adds parts
  current part   decided at the first side shot: side OK, bottom OK,
                 measure OK, each failing at the --ng-* rates; the second
                 side shot only happens for a part still OK
  tape slots     3 slots; at each top check: an OK part goes into the
                 first empty slot, the first NG slot is emptied (the
                 renderer picks it out), then the tape shifts left by the
                 ReelAdv pulses counted since the previous check
"""

import argparse
import json
import os
import random
import socket
import threading
import time
import urllib.request

FEEDER_ID, SIDE_ID, BTM_ID, TOP_ID = 104500, 114500, 124500, 134500

# Bottom camera model: nozzle at (1000, 1000) px with the arm at the
# inspection pose, 1 / 0.0124 px per mm (CalibPage.tsx INSP_LOCATION).
INSP_X, INSP_Y = 15.618, 10.330
BTM_CENTER = (1000.0, 1000.0)
BTM_MMPP = 0.0124
BTM_PX_PER_MM = 1.0 / BTM_MMPP
# VISION_MOCK_BTM_STUCK=1: report the nozzle at the image centre whatever
# the arm pose -- exercises the BtmCheckCalib degenerate-calibration path.
BTM_STUCK = os.environ.get("VISION_MOCK_BTM_STUCK") == "1"
# VISION_MOCK_MUTE=134500[,...]: never send these check results -- a lost
# vision reply, for the renderer's reply timeout.
MUTE_IDS = {int(x) for x in os.environ.get("VISION_MOCK_MUTE", "").split(",") if x.strip()}

# Feeder camera pixel area covered by default/calib.json.
FEED_X = (2420.0, 3520.0)
FEED_Y = (1390.0, 2140.0)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class World:
    def __init__(self, ng_side, ng_btm, ng_tape, rng):
        self.rng = rng
        self.ng_side, self.ng_btm, self.ng_tape = ng_side, ng_btm, ng_tape
        self.plate = []
        self.last_pickable = []
        self.add_parts(40)
        self.part = None            # part under inspection
        self.side_shots = 0         # side shots seen for self.part
        self.pending_place = False  # an OK part is on the way to the tape
        self.slots = [None, None, None]   # None empty, True OK, False NG
        self.adv_since_top = 0
        self.lock = threading.Lock()

    # --- feeder -------------------------------------------------------
    def add_parts(self, n):
        for _ in range(n):
            self.plate.append(self.new_part_on_plate())

    def new_part_on_plate(self):
        r = self.rng
        pickable = r.random() < 0.7
        return {
            "x": round(r.uniform(*FEED_X), 2),
            "y": round(r.uniform(*FEED_Y), 2),
            "ang": round(r.uniform(-180, 180), 3),
            "inner": 1 if pickable or r.random() < 0.5 else 0,
            "outer": 1 if pickable else 0,
        }

    def feeder_shot(self):
        with self.lock:
            picked = [p for p in self.last_pickable if p in self.plate]
            for p in picked:
                self.plate.remove(p)
            self.last_pickable = [p for p in self.plate if p["inner"] and p["outer"]]
            return [dict(p) for p in self.plate]

    def feeder_write(self, address, value):
        with self.lock:
            if not value:
                return
            if address == 0x1D:                      # hopper: more parts
                self.add_parts(20)
            if address in (0x1D, 0x05, 0x0A, 0x0B):  # any vibration reshuffles
                self.plate = [self.new_part_on_plate() for _ in self.plate]
                self.last_pickable = []
            self.plate = self.plate[:80]

    # --- inspection ---------------------------------------------------
    def side_shot(self):
        with self.lock:
            p = self.part
            second = p is not None and self.side_shots == 1 and p["btm_seen"] and p["ok_so_far"]
            if not second:
                r = self.rng
                p = self.part = {
                    "side_ok": r.random() >= self.ng_side,
                    "btm_ok": r.random() >= self.ng_btm,
                    "measure_ok": r.random() >= self.ng_side,
                    "facing": r.choice([0, 1]),
                    "btm_seen": False,
                    "ok_so_far": True,
                }
                self.side_shots = 0
                p["ok_so_far"] = p["side_ok"]
            self.side_shots += 1
            if second:
                if p["measure_ok"]:
                    self.pending_place = True
                return {"status": 1, "facing": p["facing"],
                        "measure": {"status": 1 if p["measure_ok"] else 0,
                                    "OK_vec": [1, 1, 1] if p["measure_ok"] else [1, 0, 1]}}
            return {"status": 1 if p["side_ok"] else 0, "facing": p["facing"],
                    "measure": {"status": 1, "OK_vec": [1, 1, 1]}}

    def btm_shot(self, arm_x, arm_y):
        with self.lock:
            r = self.rng
            nx = BTM_CENTER[0] + (arm_x - INSP_X) * BTM_PX_PER_MM
            ny = BTM_CENTER[1] + (arm_y - INSP_Y) * BTM_PX_PER_MM
            if BTM_STUCK:   # fault injection: nozzle never seems to move
                nx, ny = BTM_CENTER
            nozzle = {"x": round(nx, 2), "y": round(ny, 2), "ang": 0.0, "status": 1}
            p = self.part
            ok = True
            if p is not None and not p["btm_seen"]:
                p["btm_seen"] = True
                ok = p["btm_ok"]
                p["ok_so_far"] = p["ok_so_far"] and ok
            obj = {"x": round(nx + r.uniform(-20, 20), 2), "y": round(ny + r.uniform(-20, 20), 2),
                   "ang": round(r.uniform(-10, 10), 3), "status": 1 if ok else 0}
            return {"status": 1 if ok else 0, "obj_pose": obj, "nozzle_pose": nozzle,
                    "mmpp": BTM_MMPP}

    # --- tape ---------------------------------------------------------
    def reel_adv(self, n):
        with self.lock:
            self.adv_since_top += n

    def top_check(self):
        with self.lock:
            # An NG part reported by the previous check is gone by now: the
            # renderer picks NG parts back out of the tape. (It used to be
            # removed in the same check that found it, so the renderer never
            # saw an NG slot and its NG-pick path never ran.)
            self.slots = [None if s is False else s for s in self.slots]
            if self.pending_place:
                self.pending_place = False
                if None in self.slots:
                    i = self.slots.index(None)
                    self.slots[i] = self.rng.random() >= self.ng_tape
            n = min(self.adv_since_top, 3)
            self.adv_since_top = 0
            self.slots = self.slots[n:] + [None] * n
            r = self.rng
            return {
                "is_clear": [1 if s is None else 0 for s in self.slots],
                "is_OK": [1 if s is True else 0 for s in self.slots],
                "locHole": {"status": 1, "x": round(r.uniform(-3, 3), 2),
                            "y": round(r.uniform(-3, 3), 2), "mmpp": 0.02},
                "_advanced": n,
            }


class Server:
    def __init__(self, world, port):
        self.world = world
        self.port = port
        self.client = None
        self.send_lock = threading.Lock()
        self.last_top = None

    def send(self, trig_id, data):
        msg = json.dumps({"trig_id": trig_id, "data": data}, separators=(",", ":"))
        with self.send_lock:
            if self.client is None:
                log("  (no renderer connected; dropped", trig_id, ")")
                return
            try:
                self.client.sendall(msg.encode("ascii"))
            except OSError as e:
                log("  send failed:", e)

    def push_later(self, delay, trig_id, make):
        def run():
            time.sleep(delay)
            data = make()
            if trig_id in MUTE_IDS:
                log("MUTED push", trig_id)
                return
            self.send(trig_id, data)
            log("push", trig_id, json.dumps(data)[:160])
        threading.Thread(target=run, daemon=True).start()

    def handle_request(self, req):
        cmd = req.get("cmd_type")
        rid = req.get("id")
        if cmd == "save_target":
            data = {"ok": 1}
        elif cmd == "BufferSize":
            data = 0
        elif cmd == "revisit":
            data = self.last_top or {}
        else:
            data = {"ok": 0, "err": "unknown cmd_type %r" % cmd}
        log("request", cmd, "id", rid)
        self.send(rid, data)

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self.port))
        srv.listen(1)
        log("vision mock listening on 127.0.0.1:%d" % self.port)
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log("renderer connected", addr)
            with self.send_lock:
                self.client = conn
            buf = ""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", "replace")
                    while ";" in buf:
                        part, buf = buf.split(";", 1)
                        part = part.strip()
                        if part:
                            try:
                                self.handle_request(json.loads(part))
                            except ValueError:
                                log("bad request", part[:80])
            except OSError:
                pass
            log("renderer disconnected")
            with self.send_lock:
                self.client = None


def poll_plc(server, world, url, period, reel_cell):
    prev = None
    reel_cells = None
    top_pending = False
    while True:
        try:
            # 2.5 s, not less: the PLC's HTTP server accepts one connection
            # at a time, and while it is busy Windows re-sends our SYN only
            # after ~1 s. A shorter timeout gave up on every such request,
            # piled up abandoned connections and blacked the mock out for
            # 10+ s mid-run (2026-09-24).
            with urllib.request.urlopen(url, timeout=2.5) as r:
                v = json.loads(r.read().decode())
        except Exception as e:
            # Retry fast: the PLC latches only the *last* bottom-camera
            # position, so a long gap folds several shots onto one pose
            # (BtmCheckCalib then sees colinear points).
            log("PLC poll failed:", e)
            time.sleep(0.05)
            continue
        if prev is None:
            prev = v
            log("PLC counters", v)
            continue
        d = {k: v[k] - prev[k] for k in ("sd", "bt", "ff", "tp", "ad")}
        if d["ad"] > 0:                       # output-6 pulses (bench button)
            world.reel_adv(d["ad"])
        if "rp" in v:                         # ReelGo: reel travel in whole cells
            cells = int(round(v["rp"] / reel_cell))
            if reel_cells is None or cells < reel_cells:   # first poll, or axis re-referenced
                reel_cells = cells
            elif cells > reel_cells:
                world.reel_adv(cells - reel_cells)
                reel_cells = cells
        for _ in range(d["ff"]):
            server.push_later(0.15, FEEDER_ID, world.feeder_shot)
        for _ in range(d["sd"]):
            server.push_later(0.04, SIDE_ID, world.side_shot)
        if d["bt"] > 1:
            log("WARN: %d bottom shots in one poll gap; they share one latched pose" % d["bt"])
        for _ in range(d["bt"]):
            bx, by = v["bx"], v["by"]
            server.push_later(0.04, BTM_ID, lambda bx=bx, by=by: world.btm_shot(bx, by))
        if d["tp"] > 0 and not top_pending:
            # Both top pulses (side light, front light ~80 ms apart) make one
            # check; answer 250 ms after the first.
            top_pending = True

            def top():
                nonlocal top_pending
                data = world.top_check()
                server.last_top = data
                top_pending = False
                return data
            server.push_later(0.25, TOP_ID, top)
        prev = v
        time.sleep(period)


def listen_feeder(world, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    while True:
        msg, _ = sock.recvfrom(1024)
        try:
            ev = json.loads(msg.decode())
        except ValueError:
            continue
        if ev.get("event") == "feeder_write":
            world.feeder_write(ev["address"], ev["value"])
            log("feeder write 0x%02X = %s (plate now %d parts)" % (ev["address"], ev["value"], len(world.plate)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plc", default="192.168.1.70:8126", help="PLC HTTP host:port (GET /v)")
    ap.add_argument("--port", type=int, default=7950, help="port the renderer connects to")
    ap.add_argument("--feeder-udp", type=int, default=7951)
    ap.add_argument("--ng-side", type=float, default=0.05)
    ap.add_argument("--ng-btm", type=float, default=0.03)
    ap.add_argument("--ng-tape", type=float, default=0.02)
    # The PLC's HTTP server takes one connection at a time (~25 requests/s
    # in all); event_log.py's collector shares it. 50 ms is plenty: the
    # mock answers 40 ms after a pulse, the renderer waits up to 10 s.
    ap.add_argument("--poll-ms", type=float, default=50)
    ap.add_argument("--reel-cell", type=float, default=8.0,
                    help="reel axis units per tape cell (CalibPage REEL_CELL_DISTANCE)")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    world = World(a.ng_side, a.ng_btm, a.ng_tape, random.Random(a.seed))
    server = Server(world, a.port)
    threading.Thread(target=listen_feeder, args=(world, a.feeder_udp), daemon=True).start()
    threading.Thread(target=poll_plc, args=(server, world, "http://%s/v" % a.plc, a.poll_ms / 1000.0, a.reel_cell),
                     daemon=True).start()
    server.serve()


if __name__ == "__main__":
    main()
