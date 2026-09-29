"""Talk to the PLC's msgpack server (port 8125) directly, without the UI.

    from plc_direct import Plc
    with Plc("192.168.1.70") as plc:
        print(plc.sys("PLAN_GET"))
        plc.m("ReelGo", Distance=8, F=200, ACC=2000, DEA=2000, JERK=20000)

The PLC takes one client at a time: close the UI's PLC link first.
Packets are msgpack maps written back to back (the UI's framing); a
reply carries the request's `id` and `ack`, events carry kind='event'.
A background thread sends PING every second, as the UI does (the PLC
trips Error after 5 s without one once it has seen the first).
"""

import socket
import threading
import time

import msgpack

PROTOCOL_VERSION = 1


class Nak(RuntimeError):
    pass


class Plc:
    def __init__(self, host, port=8125, timeout=10.0, ping=True):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(0.2)
        self.unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        self.lock = threading.Lock()
        self.replies = {}
        self.events = []
        self.next_id = 1000
        self.timeout = timeout
        self.alive = True
        self.rx = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx.start()
        self.pinger = None
        if ping:
            self.pinger = threading.Thread(target=self._ping_loop, daemon=True)
            self.pinger.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _rx_loop(self):
        while self.alive:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            self.unpacker.feed(data)
            for obj in self.unpacker:
                if isinstance(obj, dict) and obj.get("kind") == "event":
                    with self.lock:
                        self.events.append(obj)
                elif isinstance(obj, dict) and "id" in obj:
                    with self.lock:
                        self.replies[obj["id"]] = obj
        self.alive = False

    def _ping_loop(self):
        while self.alive:
            try:
                self.send({"type": "SYS", "cmd": "PING"}, timeout=3)
            except Exception:
                pass
            time.sleep(1.0)

    def send(self, pkt, timeout=None):
        """Send one packet and wait for its reply. Raises Nak on ack=False."""
        with self.lock:
            self.next_id += 1
            pid = self.next_id
        pkt = dict(pkt, id=pid, protocol_version=PROTOCOL_VERSION)
        self.sock.sendall(msgpack.packb(pkt, use_bin_type=True))
        end = time.time() + (timeout or self.timeout)
        while time.time() < end:
            with self.lock:
                rep = self.replies.pop(pid, None)
            if rep is not None:
                if rep.get("ack") is False:
                    raise Nak("%s: %s" % (pkt.get("cmd"), rep.get("err", rep)))
                return rep
            if not self.alive:
                raise ConnectionError("PLC connection closed")
            time.sleep(0.005)
        raise TimeoutError("no reply to %s within %.1f s" % (pkt.get("cmd"), timeout or self.timeout))

    def sys(self, cmd, timeout=None, **kw):
        return self.send(dict(kw, type="SYS", cmd=cmd), timeout)

    def m(self, cmd, timeout=None, **kw):
        return self.send(dict(kw, type="M", cmd=cmd), timeout)

    def take_events(self, name=None):
        with self.lock:
            out = [e for e in self.events if name is None or e.get("name") == name]
            self.events = [e for e in self.events if not (name is None or e.get("name") == name)]
        return out
