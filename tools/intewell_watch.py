"""Log the Intewell RTOS task table (and CPU use) of the PLC over its
telnet shell, for post-mortem when the PLC hangs.

    python tools/intewell_watch.py [--host 192.168.1.70] [--out FILE] [--period 0.7]

Every period: `task` (state and ticks of each task); every 5 s: `cpuuse`.
Each block is written with a timestamp and flushed at once, so the last
block before a hang is on disk. Stops with Ctrl+C or when the shell stops
answering (logged as such).
"""

import argparse
import socket
import time

import topology as tp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=tp.PLC_HOST)
    ap.add_argument("--out", default="intewell_watch.log")
    ap.add_argument("--period", type=float, default=0.7)
    a = ap.parse_args()
    s = socket.create_connection((a.host, 23), timeout=5)

    def cmd(c, wait):
        s.sendall((c + "\r\n").encode())
        end = time.time() + wait
        out = b""
        s.settimeout(0.2)
        while time.time() < end or not out.rstrip().endswith(b"#"):
            try:
                d = s.recv(65536)
                if not d:
                    raise ConnectionError("shell closed")
                out += d
            except socket.timeout:
                if time.time() > end + 5:
                    raise TimeoutError("shell did not answer '%s' in %.0f s" % (c, wait + 5))
        return out.decode("latin1")

    cmd("", 0.5)
    last_cpu = 0.0
    with open(a.out, "a", encoding="utf-8") as f:
        while True:
            try:
                ts = time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000)
                f.write("=== %s task\n%s\n" % (ts, cmd("task", 0.1)))
                if time.time() - last_cpu > 5:
                    f.write("=== %s cpuuse\n%s\n" % (ts, cmd("cpuuse", 3)))
                    last_cpu = time.time()
                f.flush()
                time.sleep(a.period)
            except KeyboardInterrupt:
                break
            except Exception as e:
                f.write("=== %s SHELL LOST: %s\n" % (time.strftime("%H:%M:%S"), e))
                f.flush()
                break


if __name__ == "__main__":
    main()
